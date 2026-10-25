"""Versioned Choice backends with bounded admission and caller deadlines.

Original integration code; no jevtests implementation is vendored. Provider
credentials are environment references and never part of request recordings.
"""

import ipaddress
import json
import math
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChoiceRequest:
    observation: dict
    candidates: dict[str, str]
    instructions: str = "Choose one legal next action using the observation."

    def __post_init__(self):
        if not 1 <= len(self.candidates) <= 12:
            raise ValueError("Choice profile requires 1..12 candidates")
        if not all(
            isinstance(k, str) and k and isinstance(v, str) and v
            for k, v in self.candidates.items()
        ):
            raise ValueError("Candidates require stable IDs and descriptions")
        json.dumps(self.observation, allow_nan=False)


@dataclass(frozen=True)
class Decision:
    choice: str
    probabilities: dict[str, float]
    probability_kind: str
    latency_ms: float
    served_model: str | None


def endpoint(root: str, kind: str, *, network_scope: str = "remote") -> str:
    parts = urllib.parse.urlsplit(root)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Use an HTTP(S) root without credentials/query/fragment")
    if network_scope not in {"remote", "private_lan"}:
        raise ValueError("Unknown backend network scope")
    private_lan = False
    if network_scope == "private_lan":
        try:
            address = ipaddress.ip_address(parts.hostname)
        except ValueError:
            raise ValueError(
                "Private LAN endpoints require a resolved private IP"
            ) from None
        networks = (
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "fc00::/7",
            "fe80::/10",
        )
        private_lan = any(address in ipaddress.ip_network(n) for n in networks)
        if not private_lan:
            raise ValueError("Private LAN endpoint is outside private address ranges")
    if (
        parts.scheme == "http"
        and not private_lan
        and parts.hostname
        not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
    ):
        raise ValueError("Remote provider roots require HTTPS")
    suffix = {"systemone": "systemone", "chat": "chat/completions"}[kind]
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urllib.parse.urlunsplit(parts._replace(path=f"{path}/v1/{suffix}"))


def probabilities(value: object, candidates: dict[str, str]) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != set(candidates):
        raise BackendError("Probability IDs must exactly match candidates")
    if any(type(v) not in {int, float} for v in value.values()):
        raise BackendError("Probabilities must be numeric")
    if any(not 0 <= v <= 1 for v in value.values()):
        raise BackendError("Probabilities must be finite and in [0,1]")
    result = {k: float(v) for k, v in value.items()}
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in result.values()):
        raise BackendError("Probabilities must be finite and in [0,1]")
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-5):
        raise BackendError("Probabilities must sum to one")
    return result


def parse_response(raw: dict, kind: str, request: ChoiceRequest) -> tuple:
    try:
        if kind == "systemone":
            answer = raw["answers"]["q"]
            probs = probabilities(answer["probabilities"], request.candidates)
            choice = answer.get("choice", max(probs, key=probs.get))
            probability_kind = "provider_reported"
        else:
            answer = json.loads(raw["choices"][0]["message"]["content"])
            choice = answer["choice"]
            probs = {key: float(key == choice) for key in request.candidates}
            probability_kind = "synthetic_one_hot_not_model_likelihood"
        if choice not in request.candidates:
            raise BackendError("Response chose an unknown candidate")
        return choice, probs, probability_kind
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise BackendError("Malformed Choice response") from error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BackendError("Provider redirect rejected")


class HttpBackend:
    """One outstanding request, zero retries, explicit per-instance budget.

    A daemon owns network I/O so callers return at their monotonic deadline.
    After timeout it retains admission until I/O exits; it cannot accumulate
    background calls. close() prevents admission and interrupts the caller wait.
    """

    def __init__(self, spec: dict):
        self.name = spec["name"]
        self.kind = spec["kind"]
        self.network_scope = spec.get("network_scope", "remote")
        self.inference_scope = spec.get("inference_scope", "unspecified")
        self.url = endpoint(spec["root"], self.kind, network_scope=self.network_scope)
        self.model = spec["model"]
        self.enable_thinking = spec.get("enable_thinking")
        if self.enable_thinking is not None and (
            self.kind != "chat" or type(self.enable_thinking) is not bool
        ):
            raise ValueError("enable_thinking must be an explicit chat boolean")
        self.deadline = float(spec.get("deadline_s", 1.0))
        self.budget = spec["request_limit"]
        if (
            not 0 < self.deadline <= 10
            or type(self.budget) is not int
            or not 1 <= self.budget <= 100
        ):
            raise ValueError("Require deadline <=10s and request_limit 1..100")
        self.local = urllib.parse.urlsplit(self.url).hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        self.local = self.local or (
            self.network_scope == "private_lan" and self.inference_scope == "local"
        )
        if not self.local:
            ceiling = spec.get("cost_ceiling_usd")
            maximum = spec.get("max_request_cost_usd")
            if (
                type(ceiling) not in {int, float}
                or type(maximum) not in {int, float}
                or not math.isfinite(ceiling)
                or not math.isfinite(maximum)
                or not 0 < maximum <= ceiling
                or self.budget * maximum > ceiling
            ):
                raise ValueError(
                    "Remote probes require a conservative bounded cost budget"
                )
        self._key = os.environ.get(spec.get("key_env", ""))
        if spec.get("key_env") and not self._key:
            raise BackendError("Configured credential is unavailable")
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.requests = 0
        self.exchanges: list[dict] = []

    def provenance(self) -> dict:
        costs = []
        for exchange in self.exchanges:
            raw = exchange.get("raw_response")
            usage = raw.get("usage") if isinstance(raw, dict) else None
            cost = usage.get("cost") if isinstance(usage, dict) else None
            if type(cost) in {int, float} and 0 <= cost <= 1e12 and math.isfinite(cost):
                costs.append(cost)
        return {
            "schema_version": 1,
            "name": self.name,
            "kind": self.kind,
            "root": self.url.rsplit("/v1/", 1)[0],
            "network_scope": self.network_scope,
            "inference_scope": self.inference_scope,
            "requested_model": self.model,
            "enable_thinking": self.enable_thinking,
            "deadline_s": self.deadline,
            "retries": 0,
            "request_limit": self.budget,
            "requests": self.requests,
            "reported_cost_usd": sum(costs) if costs else None,
            "costed_requests": len(costs),
            "actual_cost_usd": sum(costs)
            if self.requests and len(costs) == self.requests
            else None,
            "cost_source": "provider_usage_not_independently_audited",
        }

    def _payload(self, request: ChoiceRequest) -> dict:
        question = {
            "type": "choice",
            "instructions": request.instructions,
            "criteria": request.candidates,
        }
        if self.kind == "systemone":
            return {
                "model": self.model,
                "state": request.observation,
                "questions": {"q": question},
            }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "observation": request.observation,
                            "question": question,
                            "format": {"choice": "candidate ID"},
                        }
                    ),
                }
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 64,
            "temperature": 0,
        }
        if self.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        return payload

    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload, allow_nan=False).encode(),
            headers=headers,
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(req, timeout=self.deadline) as response:
                data = response.read(1_048_577)
                if len(data) > 1_048_576:
                    raise BackendError("Response exceeded byte limit")
                return json.loads(data)
        except urllib.error.HTTPError as error:
            # Never include provider bodies/headers, which may echo credentials.
            raise BackendError(f"Provider HTTP {error.code}") from None
        except (OSError, ValueError) as error:
            raise BackendError(
                f"Provider transport/JSON error: {type(error).__name__}"
            ) from None

    def decide(self, request: ChoiceRequest) -> Decision:
        if self._closed.is_set():
            raise BackendError("Backend is closed")
        if not self._lock.acquire(blocking=False):
            raise BackendError("Backend already has an outstanding request")
        if self._closed.is_set():
            self._lock.release()
            raise BackendError("Backend is closed")
        if self.requests >= self.budget:
            self._lock.release()
            raise BackendError("Request budget exhausted")
        self.requests += 1
        start = time.monotonic()
        result = queue.Queue(maxsize=1)
        payload = self._payload(request)
        exchange = {
            "schema_version": 1,
            "request_monotonic_ns": time.monotonic_ns(),
            "candidate_order": list(request.candidates),
            "request": self._redact(payload),
            "status": "pending",
        }
        self.exchanges.append(exchange)

        def work():
            try:
                raw = self._post(payload)
                exchange["response_monotonic_ns"] = time.monotonic_ns()
                exchange["raw_response"] = self._redact(raw)
                result.put((True, raw))
            except Exception as error:
                exchange["response_monotonic_ns"] = time.monotonic_ns()
                exchange["transport_error_type"] = type(error).__name__
                result.put((False, error))
            finally:
                self._lock.release()

        threading.Thread(target=work, daemon=True).start()
        while True:
            remaining = self.deadline - (time.monotonic() - start)
            if remaining <= 0 or self._closed.is_set():
                exchange["status"] = "deadline_or_cancelled"
                raise BackendError(
                    "Decision deadline/cancellation exceeded; release controls"
                )
            try:
                ok, raw = result.get(timeout=min(remaining, 0.01))
                break
            except queue.Empty:
                continue
        if time.monotonic() - start > self.deadline or self._closed.is_set():
            exchange["status"] = "late_discarded"
            raise BackendError("Late decision discarded; release controls")
        if not ok:
            exchange["status"] = "transport_error"
            raise raw
        try:
            choice, probs, label = parse_response(raw, self.kind, request)
        except BackendError:
            exchange["status"] = "invalid_response"
            raise
        exchange["status"] = "valid"
        exchange["choice"] = choice
        served_model = raw.get("model")
        if served_model is not None and not isinstance(served_model, str):
            exchange["status"] = "invalid_response"
            raise BackendError("Served model must be a string or null")
        if time.monotonic() - start > self.deadline or self._closed.is_set():
            exchange["status"] = "late_discarded"
            raise BackendError("Validated decision exceeded deadline; release controls")
        return Decision(
            choice,
            probs,
            label,
            (time.monotonic() - start) * 1000,
            self._redact(served_model),
        )

    def _redact(self, value):
        # A provider may echo a credential in its response body.
        if isinstance(value, str):
            return value.replace(self._key, "[REDACTED]") if self._key else value
        if isinstance(value, dict):
            return {self._redact(k): self._redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        return value

    def health(self) -> dict:
        # A valid Choice response, rather than HTTP status alone, proves readiness.
        decision = self.decide(
            ChoiceRequest({"probe": "readiness"}, {"stop": "Release all controls"})
        )
        return {
            "status": "ready",
            "decision": asdict(decision),
            "provenance": self.provenance(),
            "exchanges": self.exchanges,
        }

    def close(self):
        self._closed.set()


def probe_config(specs: list[dict]) -> list[dict]:
    outcomes = []
    for spec in specs:
        if not spec.get("enabled", False):
            outcomes.append({"name": spec["name"], "status": "skipped_disabled"})
            continue
        backend = None
        try:
            backend = HttpBackend(spec)
            outcomes.append({"name": spec["name"], **backend.health()})
        except (BackendError, ValueError, KeyError) as error:
            outcomes.append(
                {
                    "name": spec.get("name", "unknown"),
                    "status": "not_ready",
                    "error": str(error),
                    "provenance": backend.provenance() if backend else {},
                    "exchanges": backend.exchanges if backend else [],
                }
            )
        finally:
            if backend:
                backend.close()
    return outcomes
