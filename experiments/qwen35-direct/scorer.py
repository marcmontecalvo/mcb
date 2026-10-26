"""Experiment HTTP wrapper around pinned, unchanged SemIf direct scoring."""

import hashlib
import json
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from semif_phase1.core import load_causal_model
from semif_phase1.direct import score

MODEL = "Qwen/Qwen3.5-4B"
REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
SOURCE_REVISION = "23cf1f39fc9534fe81437200959b6dfc7106e45a"


def decision_row(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected an object")
    question = payload["questions"]["q"]
    if question["type"] != "choice":
        raise ValueError("This experiment accepts Choice only")
    return {
        "id": "minecraft-choice",
        "state": payload["state"],
        "question": question["instructions"],
        "options": [
            {"id": key, "description": description}
            for key, description in question["criteria"].items()
        ],
    }


def main():
    model, tokenizer, metadata = load_causal_model(
        MODEL, REVISION, device="cuda", dtype="bfloat16"
    )
    metadata.update(
        semif_source_revision=SOURCE_REVISION,
        wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        implementation_sha256={
            filename: hashlib.sha256(
                (Path(__file__).parent / "semif_phase1" / filename).read_bytes()
            ).hexdigest()
            for filename in ("core.py", "direct.py")
        },
    )
    # Startup warmup is recorded separately and excluded from timed requests.
    warmup = score(
        model,
        tokenizer,
        {
            "id": "startup-warmup",
            "state": "A man is playing a guitar.",
            "question": "Choose the statement supported by the evidence.",
            "options": [
                {"id": "music", "description": "Someone is making music."},
                {"id": "sleep", "description": "The man is asleep."},
            ],
        },
        metadata,
        max_tokens=2048,
    )
    print(json.dumps({"startup_warmup": warmup}), flush=True)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            data = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != "/health":
                self.reply(404, {"error": "Unknown endpoint"})
                return
            self.reply(
                200,
                {
                    "status": "ready",
                    "metadata": metadata,
                    "startup_warmup": warmup,
                    "generated_tokens_per_decision": 0,
                },
            )

        def do_POST(self):
            if self.path != "/v1/systemone":
                self.reply(404, {"error": "Unknown endpoint"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 262144:
                    raise ValueError("Require a JSON body no larger than 256 KiB")
                payload = json.loads(self.rfile.read(size))
                row = decision_row(payload)
                result = score(model, tokenizer, row, metadata, max_tokens=2048)
                probabilities = dict(
                    zip(result["option_ids"], result["probabilities"], strict=True)
                )
                self.reply(
                    200,
                    {
                        "model": MODEL,
                        "answers": {
                            "q": {
                                "choice": max(probabilities, key=probabilities.get),
                                "probabilities": probabilities,
                            }
                        },
                        "semif": result,
                    },
                )
            except (KeyError, TypeError, ValueError) as error:
                self.reply(400, {"error": str(error)})
            except Exception:
                traceback.print_exc()
                self.reply(500, {"error": "Scoring failed; inspect container logs"})

    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
