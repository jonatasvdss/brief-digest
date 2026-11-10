# Handover

For whoever installs and looks after brief-digest on the end user's Mac. The end
user's own guide is `LEIA-ME.txt` (Portuguese), which the installer copies into the
data folder.

**Not yet verified on a Mac:** the `launchd` registration, the Time Machine
exclusion, and macOS folder permissions for the background agent. The steps under
"Check the install" exist to verify them. Everything else is covered by tests on
macOS CI.

## Rules that do not change at install time

- Use only synthetic PDFs to test the install. Never test with a real filing while
  someone else is watching the screen or sharing it, and never copy logs or outputs
  of real filings off the machine.
- No hosted model, no remote Ollama. The config only accepts `127.0.0.1` or `::1`.
- The summary is a reading aid. The legal professional must be told, in person, that
  it can be wrong and that the document folder is deleted after 7 days.

## Install

1. Install [uv](https://docs.astral.sh/uv/) and Ollama.
2. Disable Ollama's cloud features: put `{"disable_ollama_cloud": true}` in
   `~/.ollama/server.json`, restart Ollama, and confirm its log reports cloud
   disabled. In Ollama's settings, also set it to start at login and turn automatic
   updates off if the option exists; an update can change model behaviour or
   defaults without anyone noticing.
3. Put the repository somewhere in the home folder that is **not** Documents,
   Desktop or Downloads.
4. Run the two one-time downloads and the installer:

   ```sh
   uv run --locked python scripts/fetch_tessdata.py
   ollama pull gemma4:12b
   uv run --locked --no-dev python scripts/install.py
   ```

The installer stops with a list if a prerequisite is missing. On success it has:
created `~/BriefDigest` (owner-only), written `config.toml` and `LEIA-ME.txt` there,
excluded the folder from Time Machine, and registered the agent
`local.brief-digest.watcher`.

## Check the install

Run each of these once after installing.

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
- The Time Machine exclusion does not cover other backup or sync tools. Do not put
  the data folder inside a synced folder (iCloud Drive, Dropbox and the like).

## Before making the repository public

- Review the full git history by hand, commit by commit, for names, paths, courts,
  case numbers and screenshots. The CI secrets scan does not replace this.
- Add the AGPL-3.0 `LICENSE` file.
- Confirm no issue, pull request, or Actions log in the repository holds anything
  identifying.
