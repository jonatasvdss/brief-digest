# brief-digest

A local tool that converts court filings (PDF) to text and drafts structured summaries
with a local LLM. Everything runs on the user's own machine; no data leaves it.

Status: early development (repository skeleton).

## Development

```sh
uv sync
uv run pre-commit install
uv run python scripts/fetch_tessdata.py   # one-time: Portuguese OCR data, pinned + checksummed
uv run pytest
uv run ruff check .
```

Tests use only synthetic PDFs generated at test time. Never add real documents.

Opt-in checks against a real local model (Ollama running, model pulled, cloud
features disabled):

```sh
BRIEF_DIGEST_OLLAMA_MODEL=gemma4:12b uv run pytest -m ollama
```
