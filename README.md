# brief-digest

A local tool that converts court filings (PDF) to text and drafts structured summaries
with a local LLM. Everything runs on the user's own machine; no data leaves it.

Status: in development. The pipeline runs end to end on synthetic documents; the
macOS installer is exercised on a clean macOS CI runner, not yet on the target Mac. Installing and operating it:
[docs/handover.md](docs/handover.md). End-user guide (Portuguese):
[docs/LEIA-ME.txt](docs/LEIA-ME.txt).

## How it works

The watcher keeps one data folder (default `~/BriefDigest`):

| Path | What it is |
|---|---|
| `Entrada/` | Drop PDFs here. |
| `Resumos/<file name>/` | One folder per PDF: `original.pdf`, `full.md` (text with page markers), `summary.md` (draft summary), and small status files. |
| `config.toml` | Settings. Restart the watcher after editing. |
| `.state/` | Job database and logs. Logs hold counts, durations and error types only. |

A PDF is picked up once it has stopped changing, moved into its own folder under
`Resumos/`, converted, summarized, and checked. When no summary can be produced,
`summary.md` says so instead.

**Each document folder is deleted 7 days after processing finished**, including the
PDF and anything else saved inside it. Copy elsewhere what must be kept. The
installer makes the data folder private to its owner and excludes it from Time
Machine; other backup or sync tools are outside the app's control and keep their
own copies, so keep the folder out of synced locations.

## Install on macOS

Unzip the project anywhere and double-click `Instalar.command`. After one
confirmation it installs what is missing (uv, Ollama, the Portuguese OCR data, the
model), turns Ollama cloud features off, registers background agents for the
Ollama server and the watcher, and checks itself with a synthetic filing. Details,
and what to do if macOS blocks the double-click: [docs/handover.md](docs/handover.md).

The installer puts two more launchers in the data folder: `Trocar modelo.command`
switches the Ollama model, and `Diagnostico.command` writes a report with no
document text or file names, for sending to whoever maintains the install.

Developer route, with uv, Ollama, the OCR data and the model already in place, and
the repository outside Documents, Desktop and Downloads:

```sh
uv run --locked --no-dev python scripts/install.py
```

It downloads nothing and stops with a list if a prerequisite is missing.
`--uninstall` removes the agents and leaves the data folder.

To run the watcher by hand instead (any platform):

```sh
uv run brief-digest --config ~/BriefDigest/config.toml          # until stopped
uv run brief-digest --config ~/BriefDigest/config.toml --once   # process what is there, exit
```

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

Compare models on the synthetic filings (rule-following metrics only, not legal
quality):

```sh
uv run python scripts/eval_models.py gemma4:12b qwen3.5:9b
```

## License

AGPL-3.0; see [LICENSE](LICENSE).
