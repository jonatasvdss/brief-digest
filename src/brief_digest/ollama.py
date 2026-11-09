"""Summarizer backed by a local Ollama server.

The only module allowed network access (hard rule 5), and only to a loopback IP
literal: hostnames are refused so name resolution can never redirect traffic, and
http.client ignores proxy environment variables. Cloud-offloaded models are refused,
because Ollama would send the prompt to its cloud service.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
from dataclasses import dataclass

from brief_digest.summarize import SummarizerError, SummaryRejected


@dataclass(frozen=True)
class OllamaConfig:
    model: str
    host: str = "127.0.0.1"
    port: int = 11434
    num_ctx: int = 16384
    seed: int = 0
    timeout_s: float = 900.0


def check_loopback(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("Ollama host must be a loopback IP literal (127.0.0.1 or ::1)") from None
    if not address.is_loopback:
        raise ValueError("Ollama host must be a loopback IP literal (127.0.0.1 or ::1)")


def check_local_model_name(model: str) -> None:
    if not model.strip() or "cloud" in model.casefold():
        raise ValueError("model must be a locally installed model, not a cloud model")


class OllamaSummarizer:
    def __init__(self, config: OllamaConfig) -> None:
        check_loopback(config.host)
        check_local_model_name(config.model)
        self.config = config
        self._model_checked = False

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> dict:
        conn = http.client.HTTPConnection(
            self.config.host, self.config.port, timeout=self.config.timeout_s
        )
        try:
            payload = None if body is None else json.dumps(body)
            conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise SummarizerError("ollama_unavailable") from exc
        finally:
            conn.close()
        if response.status != 200:
            raise SummarizerError(f"ollama_http_{response.status}")
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SummarizerError("ollama_bad_response") from exc
        if not isinstance(parsed, dict):
            raise SummarizerError("ollama_bad_response")
        return parsed

    def ensure_local_model(self) -> None:
        models = self._request("GET", "/api/tags").get("models") or []
        entry = next((m for m in models if m.get("name") == self.config.model), None)
        if entry is None:
            raise SummarizerError("model_not_installed")
        if entry.get("remote_host") or entry.get("remote_model") or not entry.get("size"):
            raise SummarizerError("model_not_local")

    def complete(self, system: str, prompt: str, schema: dict[str, object]) -> str:
        if not self._model_checked:
            self.ensure_local_model()
            self._model_checked = True
        reply = self._request(
            "POST",
            "/api/chat",
            {
                "model": self.config.model,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {
                    "temperature": 0,
                    "seed": self.config.seed,
                    "num_ctx": self.config.num_ctx,
                },
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        message = reply.get("message")
        if not isinstance(message, dict):
            raise SummarizerError("ollama_bad_response")
        if message.get("tool_calls"):
            raise SummaryRejected("tool_call")
        content = message.get("content")
        if not isinstance(content, str):
            raise SummarizerError("ollama_bad_response")
        return content
