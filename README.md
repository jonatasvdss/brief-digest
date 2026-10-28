# brief-digest

A local tool that converts court filings (PDF) to text and drafts structured summaries
with a local LLM. Everything runs on the user's own machine; no data leaves it.

Status: early development (repository skeleton).

## Development

```sh
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check .
```

Tests use only synthetic PDFs generated at test time. Never add real documents.
