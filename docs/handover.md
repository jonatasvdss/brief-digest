# Handover

For whoever installs and looks after brief-digest on the end user's Mac. The end
user's own guide is `LEIA-ME.txt` (Portuguese), which the installer copies into the
data folder.

**What has been verified, and where.** The manual `install-smoke` CI workflow runs
the real installer on a clean macOS runner with synthetic documents: uv and Ollama
install, both `launchd` agents, the Time Machine exclusion, folder permissions, the
example summary, the model switcher, the diagnostic report and uninstall. Not
verified anywhere: double-clicking a `.command` file that arrived in a downloaded
ZIP (macOS may block it, see below), behaviour across a reboot, a Mac where the
Ollama desktop app is already running, and the default model on the real hardware
(the runner uses a small model).

## Rules that do not change at install time

- The installer tests itself with a synthetic filing. Real filings are handled only
  by the legal professional, alone; outputs of real filings never leave the machine.
- No hosted model, no remote Ollama. The config only accepts `127.0.0.1` or `::1`.
- The summary is a reading aid. The legal professional must be told, in person, that
  it can be wrong and that the document folder is deleted after 7 days.

## Install

The end user does this alone:

1. Unzip the project folder anywhere (Downloads is fine).
2. Double-click `Instalar.command` and answer the one confirmation.

If macOS refuses to open the file because it was downloaded, either right-click it
and choose Open, or allow it under System Settings > Privacy & Security. The
fallback that always works is one line in Terminal:
`bash ~/Downloads/brief-digest/Instalar.command` (adjust the folder).

What the installer does, in order:

1. Copies the program to `~/.brief-digest`, because a background program cannot run
   from Downloads, Desktop or Documents. The unzipped folder can then be deleted.
2. Installs uv (pinned version) if missing, and the Python packages from the lock
   file.
3. Sets `disable_ollama_cloud` in `~/.ollama/server.json`, keeping other settings.
4. If no Ollama server is running: downloads the Ollama app if missing and runs
   its bundled server as the agent `local.brief-digest.ollama`, on loopback with
   `OLLAMA_NO_CLOUD=1`. The desktop app itself is not opened: it stops at a first-run
   password prompt and updates itself. If an Ollama server is already running, it is
   kept; when the cloud setting had to be changed, it is stopped and replaced by
   this agent so the setting takes effect.
5. Downloads the Portuguese OCR data (pinned, checksummed) and the model.
6. Runs `scripts/install.py`: creates `~/BriefDigest` (owner-only) with
   `config.toml` and `LEIA-ME.txt`, excludes it from Time Machine, and registers the
   agent `local.brief-digest.watcher`.
7. Copies `Trocar modelo.command` and `Diagnostico.command` into `~/BriefDigest`.
8. Drops a synthetic filing into `Entrada/` and waits for its summary. The result
   stays in `Resumos/` as an example until retention removes it.

Running the installer again is safe and is also how to update: unzip the new
version and double-click `Instalar.command`. Settings and documents are kept.

The Ollama download is the current release, not a pinned one, so it has no
checksum here; instead the installer refuses an app that is not signed with
Ollama's Apple Developer ID. The uv install script is fetched over HTTPS at a
pinned version and is not checksummed. The developer route, without any of the downloads, is
still `uv run --locked --no-dev python scripts/install.py`.

## Switching models

The end user double-clicks `Trocar modelo.command` in `~/BriefDigest` and types a
number. The list shows the two evaluated models (`gemma4:12b`, `qwen3.5:9b`) and any
other model installed locally; cloud models are never listed or accepted. A model
that is not installed is downloaded first. The tool then rewrites the `model` line
of `config.toml` and restarts the watcher. Documents already summarized are not
redone; to compare models on one document, copy its `original.pdf` back into
`Entrada/`.

By hand: edit `model` in `~/BriefDigest/config.toml`, or run
`uv run --locked python -m scripts.set_model --model NAME` in `~/.brief-digest`.
Models beyond the two evaluated ones have not been tested, and models much larger
than about 14B parameters do not fit the target machine's memory.

## Testing with real documents

The end user starts with real filings straight away, so the developer never sees
the documents. When something goes wrong, the end user double-clicks
`Diagnostico.command` and sends the resulting `diagnostico.txt`. It holds program
state, counts, failure types and the app log; no document text, no file or folder
names, and the home folder path is masked. Nothing else from a real filing (PDF,
summary, screenshot) should be sent to anyone or pasted into an AI assistant.

## Check the install

The installer's own self-test covers the first five rows. The last two are worth
doing once by hand on the real Mac.

| Check | How | Expected |
|---|---|---|
| Agent is loaded | `launchctl print gui/$(id -u)/local.brief-digest.watcher` | Prints the service, `state = running` |
| Watcher started | Last lines of `~/BriefDigest/.state/logs/brief-digest.log` | A `watching jobs=...` line |
| Backup exclusion | `tmutil isexcluded ~/BriefDigest` | `[Excluded]` |
| Folder is private | `ls -ld ~/BriefDigest` | `drwx------` |
| End to end | `uv run python -m tests.fixtures.synth /tmp/synthetic`, then copy `appeal_native.pdf` and `appeal_signed_scan.pdf` into `Entrada/` | Within a minute or two, each has a folder in `Resumos/` with a `summary.md` that starts with the banner and lists items with page references |
| Survives restart | Log out and in again (or reboot), drop another synthetic PDF | Processed as before |
| Model down | Quit Ollama, drop a synthetic PDF, wait, start Ollama | Folder appears at once without a summary; summary appears after Ollama is back |

Delete `/tmp/synthetic` and the synthetic folders in `Resumos/` afterwards (they
would also be removed by retention).

## Day to day

- **Log:** `~/BriefDigest/.state/logs/brief-digest.log` (1 MB, 3 rotations). It holds
  job numbers, counts, durations and error types. It never holds file names or
  document text, so a job number cannot be mapped to a document from the log alone;
  match by time against the folders in `Resumos/`.
- **Stop / start:** `uv run --locked --no-dev python scripts/install.py --uninstall`
  and run the installer again. Run the installer again after moving the repository
  or pulling an update.
- **Settings:** edit `~/BriefDigest/config.toml`, then run the installer again to
  restart the watcher. Keys: `data_dir`, `template`, `tessdata`, `model`,
  `ollama_host`, `ollama_port`, `poll_seconds`, `max_attempts`, `retry_minutes`,
  `min_native_chars`, `ocr_image_coverage`. Unknown keys are refused.
- **Summary format:** add a file under `templates/`, point `template` at it, restart.
  The default template has not been validated by the end user yet.

## When a summary is missing

`summary.md` or the log gives a failure type.

| Failure type | Meaning | What to do |
|---|---|---|
| `summarizer not ready ... ollama_unavailable` (log) | Ollama is not running | Start Ollama; waiting documents continue on their own |
| `model_not_installed`, `model_not_local` | The configured model is missing, or is a cloud model | `ollama pull <model>`; never configure a cloud model |
| `ollama_http_NNN`, `ollama_bad_response` | Ollama answered with an error | Retried 3 times, 10 minutes apart; check Ollama if it persists |
| `invalid_json`, `schema_violation`, `unknown_section`, `missing_page_reference`, `page_out_of_range`, `missing_required_section` | The model's answer broke the output rules and was discarded | Final for that document; drop the PDF again only after changing model or template |
| `tool_call`, `tool_use_claim` | The model claimed to run a tool, possibly prompted by text in the PDF | Final; the answer was discarded. Worth a look at the PDF by its owner |
| "Extração incompleta" | OCR failed on at least one page | Final; if it happens on every scan, the OCR data is missing (run the installer to check) |
| `SomeError@file.py:123` | The document could not be handled (damaged or password-protected PDF, for example) | Final; the location points at the code line for a bug report |
| `too_many_attempts` | The watcher died three times while on this document | Final; report with the log |

A final failure is not retried. To try again, copy `original.pdf` from the
document's folder back into `Entrada/`.

## Known limits

- The verifier only checks that citations and legal references appear on the cited
  page. A wrong paraphrase with no reference in it is not flagged.
- Reference matching compares kind and number ("art. 1.022"), not the statute.
- Measured only on short, clean synthetic filings. Long filings and poor scans are
  untested; expect several minutes per document.
- A document still waiting (Ollama down for days) is not deleted by retention, which
  counts from the end of processing. The watcher's startup log line reports
  `untracked_folders`: folders in `Resumos/` it does not know and will never delete.
- `full.md` and `summary.md` are written so that a Markdown viewer loads nothing
  from the internet: HTML, images and link targets from the PDF or the model are
  shown as plain text. Other files in a document folder are not Markdown.
- The model server is trusted because it answers on loopback, not because it proves
  what it is. Another account on the same Mac that takes port 11434 before Ollama
  starts would receive document text. Use a Mac that only the legal professional
  logs in to.
- An oversized or malformed PDF can make one attempt slow or crash the watcher; it
  is given up after three attempts, but there is no time limit per document.
- The installer runs whatever is in the downloaded folder. Get it only from the
  project's own repository.
- The Time Machine exclusion does not cover other backup or sync tools. Do not put
  the data folder inside a synced folder (iCloud Drive, Dropbox and the like).

## Before making the repository public

- Review the full git history by hand, commit by commit, for names, paths, courts,
  case numbers and screenshots. The CI secrets scan does not replace this.
- Turn on two-factor authentication for the account and branch protection for
  `main`: the installer trusts whatever that branch holds.
- Confirm no issue, pull request, or Actions log in the repository holds anything
  identifying.
