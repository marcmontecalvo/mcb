import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mclab.backends import (
    BackendError,
    ChoiceRequest,
    HttpBackend,
    endpoint,
    parse_response,
    probe_config,
)


@pytest.fixture
def provider():
    calls = []
    behavior = {
        "status": 200,
        "delay": 0,
        "raw": {
            "model": "served",
            "answers": {"q": {"choice": "stop", "probabilities": {"stop": 1}}},
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            time.sleep(behavior["delay"])
            self.send_response(behavior["status"])
            self.end_headers()
            try:
                self.wfile.write(json.dumps(behavior["raw"]).encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Expected when the client deadline closes the socket.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", behavior, calls
    server.shutdown()
    server.server_close()
    thread.join()


def spec(root, **changes):
    return {
        "name": "test",
        "root": root,
        "kind": "systemone",
        "model": "test",
        "request_limit": 2,
        "deadline_s": 0.1,
        **changes,
    }


def test_real_http_choice_probe_and_no_expected_label(provider):
    root, _, calls = provider
    backend = HttpBackend(spec(root))
    result = backend.health()
    assert result["status"] == "ready"
    assert result["decision"]["served_model"] == "served"
    assert calls[0]["questions"]["q"]["criteria"] == {"stop": "Release all controls"}
    assert "expected" not in json.dumps(calls)
    exchange = result["exchanges"][0]
    assert exchange["request"] == calls[0]
    assert exchange["raw_response"]["model"] == "served"
    assert exchange["candidate_order"] == ["stop"]
    assert exchange["status"] == "valid"
    assert exchange["response_monotonic_ns"] >= exchange["request_monotonic_ns"]


def test_usage_cost_is_only_complete_when_all_requests_report_it(provider):
    root, behavior, _ = provider
    behavior["raw"]["usage"] = {"cost": 0.00002}
    backend = HttpBackend(spec(root))
    backend.health()
    assert backend.provenance()["actual_cost_usd"] == 0.00002
    behavior["raw"]["usage"] = {"cost": True}
    backend.health()
    assert backend.provenance()["actual_cost_usd"] is None
    assert backend.provenance()["reported_cost_usd"] == 0.00002
    assert backend.provenance()["costed_requests"] == 1


@pytest.mark.parametrize("raw", [None, [], {"usage": {"cost": 10**400}}])
def test_cost_accounting_tolerates_malformed_provider_response(raw):
    backend = HttpBackend(spec("http://localhost:8000"))
    backend.requests = 1
    backend.exchanges = [{"raw_response": raw}]
    assert backend.provenance()["actual_cost_usd"] is None
    assert backend.provenance()["costed_requests"] == 0


@pytest.mark.parametrize("status", [401, 404, 429, 503])
def test_http_error_never_healthy_or_retried(provider, status):
    root, behavior, calls = provider
    behavior["status"] = status
    with pytest.raises(BackendError, match=f"HTTP {status}"):
        HttpBackend(spec(root)).health()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "probs",
    [
        {"other": 1},
        {"stop": float("nan")},
        {"stop": -1},
        {"stop": 0.8},
        {"stop": True},
        {},
    ],
)
def test_invalid_probabilities_do_not_guess(probs):
    with pytest.raises(BackendError):
        parse_response(
            {"answers": {"q": {"probabilities": probs}}},
            "systemone",
            ChoiceRequest({}, {"stop": "stop"}),
        )


def test_unknown_choice_is_rejected():
    with pytest.raises(BackendError, match="unknown"):
        parse_response(
            {"answers": {"q": {"probabilities": {"stop": 1}, "choice": "invented"}}},
            "systemone",
            ChoiceRequest({}, {"stop": "stop"}),
        )


def test_arbitrary_precision_probability_is_a_recordable_validation_error():
    with pytest.raises(BackendError):
        parse_response(
            {"answers": {"q": {"probabilities": {"stop": 10**400}}}},
            "systemone",
            ChoiceRequest({}, {"stop": "stop"}),
        )


def test_provider_credential_echo_is_redacted(provider, monkeypatch):
    root, behavior, _ = provider
    monkeypatch.setenv("MC_TEST_KEY", "secret-test-only")
    behavior["raw"]["echo"] = "key=secret-test-only"
    behavior["raw"]["model"] = "echo-secret-test-only"
    backend = HttpBackend(spec(root, key_env="MC_TEST_KEY"))
    outcome = backend.health()
    assert "secret-test-only" not in json.dumps(outcome)
    assert "secret-test-only" not in json.dumps(backend.exchanges)
    assert "[REDACTED]" in json.dumps(backend.exchanges)


def test_chat_probabilities_are_labeled_synthetic():
    _, probs, label = parse_response(
        {"choices": [{"message": {"content": '{"choice":"stop"}'}}]},
        "chat",
        ChoiceRequest({}, {"stop": "stop"}),
    )
    assert probs == {"stop": 1}
    assert label == "synthetic_one_hot_not_model_likelihood"


def test_root_normalization_and_credential_protection():
    assert (
        endpoint("https://openrouter.ai/api/v1", "systemone")
        == "https://openrouter.ai/api/v1/systemone"
    )
    assert (
        endpoint("http://localhost:8000/v1/", "chat")
        == "http://localhost:8000/v1/chat/completions"
    )
    with pytest.raises(ValueError):
        endpoint("https://secret@example.com", "chat")
    with pytest.raises(ValueError):
        endpoint("http://example.com", "chat")
    with pytest.raises(ValueError, match="cost"):
        HttpBackend(spec("https://example.com"))


def test_explicit_private_lan_endpoint_preserves_public_https_requirement():
    assert (
        endpoint("http://10.10.70.3:4000/v1", "chat", network_scope="private_lan")
        == "http://10.10.70.3:4000/v1/chat/completions"
    )
    for root in ("http://8.8.8.8", "http://atom:4000", "http://0.0.0.0"):
        with pytest.raises(ValueError):
            endpoint(root, "chat", network_scope="private_lan")
    with pytest.raises(ValueError, match="HTTPS"):
        endpoint("http://10.10.70.3:4000", "chat")
    backend = HttpBackend(
        spec(
            "http://10.10.70.3:4000",
            network_scope="private_lan",
            inference_scope="local",
        )
    )
    assert backend.local
    with pytest.raises(ValueError, match="cost"):
        HttpBackend(spec("http://10.10.70.3:4000", network_scope="private_lan"))


def test_thinking_switch_is_explicit_scoped_and_recorded(provider):
    root, behavior, calls = provider
    behavior["raw"] = {
        "model": "served",
        "choices": [{"message": {"content": '{"choice":"stop"}'}}],
    }
    backend = HttpBackend(spec(root, kind="chat", enable_thinking=False))
    assert backend.health()["status"] == "ready"
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert backend.provenance()["enable_thinking"] is False
    for value in ("false", 0):
        with pytest.raises(ValueError):
            HttpBackend(spec(root, kind="chat", enable_thinking=value))
    with pytest.raises(ValueError):
        HttpBackend(spec(root, enable_thinking=False))


def test_response_validation_time_is_inside_the_decision_deadline(monkeypatch):
    from mclab import backends

    backend = HttpBackend(spec("http://localhost:8000", deadline_s=0.02))
    monkeypatch.setattr(backend, "_post", lambda _payload: {})

    def slow_parse(*_args):
        time.sleep(0.05)
        return "stop", {"stop": 1}, "provider_reported"

    monkeypatch.setattr(backends, "parse_response", slow_parse)
    with pytest.raises(BackendError, match="exceeded deadline"):
        backend.health()
    assert backend.exchanges[0]["status"] == "late_discarded"


def test_deadline_returns_and_late_work_is_not_applied(provider):
    root, behavior, calls = provider
    behavior["delay"] = 0.25
    backend = HttpBackend(spec(root, deadline_s=0.04))
    start = time.monotonic()
    with pytest.raises(BackendError):
        backend.health()
    assert time.monotonic() - start < 0.2
    assert backend.requests == 1
    backend.close()
    with pytest.raises(BackendError, match="closed"):
        backend.health()
    assert len(calls) <= 1


def test_budget_and_missing_credentials(provider, monkeypatch):
    root, _, calls = provider
    backend = HttpBackend(spec(root, request_limit=1))
    backend.health()
    with pytest.raises(BackendError, match="budget"):
        backend.health()
    assert len(calls) == 1
    monkeypatch.delenv("MC_TEST_KEY", raising=False)
    with pytest.raises(BackendError, match="unavailable"):
        HttpBackend(spec(root, key_env="MC_TEST_KEY"))
    assert (
        probe_config([{**spec(root), "enabled": False}])[0]["status"]
        == "skipped_disabled"
    )


def test_single_outstanding_call_and_close_interrupts(monkeypatch):
    backend = HttpBackend(spec("http://localhost:8000", deadline_s=1))
    started = threading.Event()
    finish = threading.Event()
    errors = []

    def post(_payload):
        started.set()
        finish.wait(2)
        return {}

    def caller():
        try:
            backend.health()
        except BackendError as error:
            errors.append(str(error))

    monkeypatch.setattr(backend, "_post", post)
    thread = threading.Thread(target=caller)
    thread.start()
    assert started.wait(1)
    with pytest.raises(BackendError, match="outstanding"):
        backend.health()
    backend.close()
    thread.join(0.2)
    finish.set()
    assert not thread.is_alive()
    assert errors and "cancellation" in errors[0]
