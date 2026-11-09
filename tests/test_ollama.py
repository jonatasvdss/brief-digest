import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from brief_digest.ollama import OllamaConfig, OllamaSummarizer
from brief_digest.summarize import SummarizerError, SummaryRejected

MODEL = "fake-model:1b"
LOCAL_MODEL = {"name": MODEL, "model": MODEL, "size": 123}


class FakeOllama(ThreadingHTTPServer):
    tags: list[dict]
    chat_reply: dict
    chat_status: int
    requests: list[dict]


class Handler(BaseHTTPRequestHandler):
    server: FakeOllama

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(200, {"models": self.server.tags})

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        self.server.requests.append(json.loads(self.rfile.read(length)))
        self._send(self.server.chat_status, self.server.chat_reply)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[FakeOllama]:
    srv = FakeOllama(("127.0.0.1", 0), Handler)
    srv.tags = [LOCAL_MODEL]
    srv.chat_reply = {"message": {"role": "assistant", "content": '{"a": []}'}}
    srv.chat_status = 200
    srv.requests = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def client(server: FakeOllama, model: str = MODEL) -> OllamaSummarizer:
    return OllamaSummarizer(OllamaConfig(model=model, port=server.server_address[1], timeout_s=5))


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "0.0.0.0",  # noqa: S104 - asserting it is refused
        "10.0.0.5",
        "192.168.1.10",
        "example.com",
        "::",
        "",
    ],
)
def test_non_loopback_hosts_are_refused(host: str) -> None:
    with pytest.raises(ValueError):
        OllamaSummarizer(OllamaConfig(model=MODEL, host=host))


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_ip_literals_are_accepted(host: str) -> None:
    OllamaSummarizer(OllamaConfig(model=MODEL, host=host))


@pytest.mark.parametrize("model", ["gemma4:cloud", "gemma4:31b-cloud", "Qwen:CLOUD", " "])
def test_cloud_model_names_are_refused(model: str) -> None:
    with pytest.raises(ValueError):
        OllamaSummarizer(OllamaConfig(model=model))


def test_request_has_schema_no_tools_and_deterministic_options(server: FakeOllama) -> None:
    schema = {"type": "object"}
    assert client(server).complete("sistema", "pedido", schema) == '{"a": []}'
    (body,) = server.requests
    assert "tools" not in body
    assert body["format"] == schema
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_model_must_be_installed_locally(server: FakeOllama) -> None:
    server.tags = []
    with pytest.raises(SummarizerError) as err:
        client(server).complete("s", "p", {})
    assert err.value.failure_type == "model_not_installed"
    assert server.requests == []


def test_remote_backed_models_are_refused(server: FakeOllama) -> None:
    server.tags = [{**LOCAL_MODEL, "remote_host": "https://example.invalid"}]
    with pytest.raises(SummarizerError) as err:
        client(server).complete("s", "p", {})
    assert err.value.failure_type == "model_not_local"
    assert server.requests == []


def test_tool_calls_are_rejected(server: FakeOllama) -> None:
    server.chat_reply = {"message": {"content": "", "tool_calls": [{"function": {}}]}}
    with pytest.raises(SummaryRejected) as err:
        client(server).complete("s", "p", {})
    assert err.value.failure_type == "tool_call"


def test_http_errors_become_failure_types(server: FakeOllama) -> None:
    server.chat_status = 500
    server.chat_reply = {"error": "boom"}
    with pytest.raises(SummarizerError) as err:
        client(server).complete("s", "p", {})
    assert err.value.failure_type == "ollama_http_500"


def test_unreachable_server_is_a_failure_type() -> None:
    probe = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = probe.server_address[1]
    probe.server_close()
    summarizer = OllamaSummarizer(OllamaConfig(model=MODEL, port=port, timeout_s=2))
    with pytest.raises(SummarizerError) as err:
        summarizer.complete("s", "p", {})
    assert err.value.failure_type == "ollama_unavailable"
