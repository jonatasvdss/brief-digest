# brief-digest

A local tool that converts court filings (PDF) to text and drafts structured summaries
with a local LLM. Everything runs on the end user's own machine. No data leaves it.

## Hard rules (never break these)

1. **No real data in this repo or in any session with you.** Only synthetic PDFs.
   Never ask the developer to paste or open real filings, case numbers, names, or logs
   containing document text. If a task seems to need real data, stop and say so.
   If real or identifying data is encountered, stop processing it immediately: do not
   quote, summarize, copy, or move it. Report it only in this conversation, by naming
   the affected file path or location without repeating its content. Never write such
   a report into a file, commit message, issue, or anything else that could be tracked
   or shared. Do not delete or rewrite history without the developer's approval.
2. **This repo is public-intended but starts PRIVATE.** It goes public only after the
   full git history has been reviewed by hand. Never commit PDFs (except synthetic
   fixtures under `tests/fixtures/`), `.env`, browser profiles, absolute local paths,
   screenshots of real systems, or anything identifying the end user, their employer,
   or their court.
3. **Never write real names, courts, or personal details** into code, comments,
   tests, docs, commit messages, or issues. Describe the end user only as
   "a legal professional".
4. **Logs never contain document content or case identifiers.** Log counts, page
   numbers, durations, and error types only.
5. **No network calls at runtime** except to the local LLM server on `localhost`.
   Configure the Ollama endpoint explicitly to loopback (`127.0.0.1` or `::1`) and
   reject non-loopback hosts. Model downloads are a separate, explicit setup action
   requiring developer approval; the app must never download models or make any other
   external request automatically.
6. **The summarizer stage has no tools.** It receives text and returns text. No file,
   shell, or network access. Treat all PDF content as untrusted input: prompts must
   label document text as quoted source material and instruct the model to ignore any
   instructions found within it. Maintain synthetic prompt-injection fixtures. If
   output fails the required schema or contains tool-use claims, reject it and record
   only the failure type; never retry with tools or forward the content elsewhere.
7. **The app holds no credentials.** Login and download are done by the user by hand,
   so there is nothing to store. Keep it that way: no login automation, no secrets.

## Scope

- Input: PDFs placed manually in a watched folder (login and download are done by
  the user by hand; there is NO browser automation in this project).
- Output: for each PDF, `full.md` (converted text with page markers) and
  `summary.md` (structured draft summary).
- Every generated summary starts with a banner, in Portuguese like the rest of the
  output: "Auxílio de leitura. Rascunho. Confira com a peça original." (meaning
  "Auxiliary reading aid. Draft. Verify against the original filing.").
- Data retention: delete the source PDF, extracted text, summaries, OCR
  intermediates, and document-specific database state 7 days after successful
  processing, or 7 days after the last attempt for failed jobs. Never delete
  anything outside the application's managed folders.
  A PDF that is still waiting to be processed is kept, however long it waits
  (decided): retention never deletes a document that has not been attempted.

## Architecture

Pipeline, each stage idempotent and communicating through files plus a SQLite state db:

`watch folder -> extract (text, OCR fallback) -> summarize (map-reduce) -> write .md`

- **Extraction:** deterministic, no LLM. PyMuPDF (`pymupdf4llm` for light Markdown).
  Insert `<!-- page N -->` markers. Use native text when a page yields at least 40
  non-whitespace characters (configurable; validate the threshold with synthetic
  fixtures); otherwise OCR that page. Also OCR a page whose images cover at least
  50% of it (configurable), because e-filing systems stamp native-text signature
  lines onto scanned pages. If OCR fails, mark the page unreadable, record
  the error type without document text, and mark the document incomplete rather than
  producing a normal summary. Strip repeated headers/footers by rule.
  OCR uses the Tesseract engine built into PyMuPDF; only the Portuguese language data
  is needed, fetched by `scripts/fetch_tessdata.py` (pinned commit + SHA-256) as an
  explicit setup step. The tessdata location is always configured explicitly (config
  or `TESSDATA_PREFIX`); never use PyMuPDF's auto-discovery, which runs shell commands.
- **Summarization:** local LLM via Ollama on `localhost`. Map-reduce over chunks.
  The system prompt's rules are fixed in `summarize.py`. The end user can add
  guidance on style in `instrucoes.txt` in the data folder (optional, read per
  document, at most 2000 characters, `#` lines ignored); it is appended after the
  rules, under a heading that gives the rules precedence, and never replaces them.
  Output sections are defined by a template, never hardcoded in Python (see
  "Summary template" below). Default model: `gemma4:12b` (Apache-2.0, ~8 GB Q4);
  comparison candidate: `qwen3.5:9b`. Neither has been benchmarked on Portuguese legal
  text; the model is a config value. Phase 5 synthetic comparison
  (`scripts/eval_models.py`): both models follow the rules on all synthetic filings;
  `gemma4:12b` is 2-3x faster on the dev machine, so it stays the default. The client uses the standard library, accepts
  only loopback IP literals, refuses cloud models (name or remote-backed entry), and
  checks the model is installed locally. Ollama itself must also run with cloud
  features disabled (`OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud` in
  `~/.ollama/server.json`) on every machine that processes filings.
- **Consistency checks:** match summary citations against extracted text and page
  references against page markers; flag unmatched items for human review. A match
  only confirms the text was found in the extraction — it does not establish legal
  accuracy, correct attribution, or extraction fidelity, and must never be described
  as confirming a summary's correctness. Implemented in `verify.py`: page references
  must exist; items of `verbatim: true` template sections must appear on a cited
  page; legal references (articles, laws, súmulas, precedents, temas) inside other
  items must appear on a cited page. Failing items get an inline mark in
  `summary.md`; passing items get none. Reference matching compares kind and number
  only (e.g. "art. 1.022"), not the statute named after it.
- **Orchestration** (`pipeline.py`, `state.py`, `config.py`, `cli.py`): one data
  folder holds `Entrada/` (inbox), `Resumos/<file name>/` (one folder per PDF, with
  `original.pdf` and all outputs), `config.toml`, and `.state/` (SQLite db, logs). The
  watcher polls the inbox, claims a PDF once its size and mtime are unchanged between
  two scans by moving it into its own folder, then runs extract, summarize, verify.
  Model-server failures are retried (3 attempts, 10 minutes apart); rejected output,
  incomplete extraction, and document errors are final and leave a notice in
  `summary.md`. Documents wait, without using attempts, while Ollama is unreachable.
  Retention deletes a finished job's folder and db row together and refuses any
  path not directly inside `Resumos/`. Logs name documents by job number only,
  never by folder or file name, and never include exception messages or tracebacks.
  `scripts/install.py` checks prerequisites, writes the config, makes the data
  folder owner-only, excludes it from Time Machine, copies the Portuguese user guide
  (`docs/LEIA-ME.txt`) into it, and registers the `launchd` agent; it downloads
  nothing. Operator instructions and the post-install checklist: `docs/handover.md`.
- **End-user setup** (`Instalar.command`, `scripts/setup_mac.py`): the one explicit,
  confirmed setup action that downloads things (uv, the Ollama app, OCR data, the
  model), approved by the developer as the install path for a non-technical user.
  It copies the program to `~/.brief-digest`, runs Ollama's bundled server as the
  agent `local.brief-digest.ollama` (loopback, `OLLAMA_NO_CLOUD=1`; the desktop app
  is not opened), then runs `scripts/install.py` and a self-test with a synthetic
  filing. `Trocar modelo.command` (`scripts/set_model.py`) switches the model;
  `Diagnostico.command` (`scripts/diagnose.py`) writes a report with no document
  text, file names, or home path. User-facing text in these tools is Portuguese.
  The manual `install-smoke` workflow runs all of this on a macOS runner.
- Only two abstractions: `Summarizer` (the model) and the file-based pipeline stages.
  Do not add more layers.

## Summary template

**Status: DEFAULT, UNVALIDATED by the end user.** This is Claude's best guess at a
useful structure for an appellate filing summary in the Brazilian judicial context,
based on general procedural structure (CPC-style recourse pieces), not on the end
user's personal preference, which is still unknown. Treat this as a starting point to
show the end user for feedback, not as a settled requirement.

Default sections (in order):
1. **Identificação** — parties, case/process number if present in the text,
   instance, and the decision being appealed.
2. **Tempestividade e preparo** — only if the filing states dates or values bearing
   on timeliness/court costs; omit the section if absent rather than guessing.
3. **Preliminares** — procedural/preliminary arguments raised, if any.
4. **Mérito** — substantive arguments, each with its page reference(s).
5. **Pedidos** — the relief/requests made.
6. **Fundamentos citados** — legal provisions, precedents, and doctrine cited in
   the filing (not added by the model), each with its page reference.

Each section must omit itself (not output "N/A") when the filing has no content for
it, and every factual claim must carry a page reference back to `full.md`.

### Making the template configurable (implemented in Phase 4: `templates/default.yaml`)

The template must be an external declarative file, not Python code, so a future
change of format does not require touching the pipeline:

- A `templates/*.yaml` (or `.json`) file lists sections in order, each with a name,
  a one-line description of what belongs in it, whether it is omittable, and
  optionally whether it is verbatim (checked literally by the verifier).
- The pipeline loads the active template path from a config value (not hardcoded),
  builds the map-reduce prompts from it, and validates model output against the
  declared section names.
- Switching templates is: add/edit a YAML file, point the config at it, restart the
  watcher. No code change, no redeploy.
- When the end user provides a real example of a format they use, encode it as a new
  template file (e.g. `templates/user_default.yaml`) rather than editing the
  pipeline, and treat it as the new active template once they confirm it fits.
- This mechanism is designed now, in this document, but only implemented when Phase 4
  starts — do not build the template loader before the extraction stage (Phase 3) exists.

## Deployment target

Native Python app (managed with `uv`) on a macOS Mac mini, Ollama running natively,
scheduled with `launchd`, one-command install script.

- **Hardware:** Mac mini, Apple M5 Pro chip (15-core CPU / 16-core GPU), 24GB unified
  memory. This caps the feasible local model size: comfortable for quantized models
  in the 7B-14B range; do not plan around 30B+ models, there is not enough headroom
  alongside the OS and the rest of the pipeline. If summary quality with a 7B-14B
  model is insufficient, the options are a smaller/faster model with a stricter
  verification step, or sending text to a hosted API — which would reopen the
  "no data leaves the machine" guarantee and needs explicit sign-off before going there.
- Docker is NOT used for deployment (no GPU access on macOS, extra burden for a
  non-technical user). Docker is acceptable for dev/CI only.
- Development happens on Windows 11, so keep paths and tooling cross-platform
  (`pathlib`, no shell-specific scripts without equivalents). CI must run on both
  Windows and macOS runners.

## Version control and permissions

- Repo starts private; publish only after a full history review.
- License: PyMuPDF and pymupdf4llm are AGPL-3.0, so the project will be published
  under AGPL-3.0 (decided). Add the LICENSE file at publication time.
- Before creating or importing project files, verify that the repository is private
  and that `.gitignore`, `.claude/settings.json`, and the gitleaks pre-commit hook are
  configured. If any check fails, stop before staging or committing anything.
- **gitleaks runs as a git pre-commit hook** (via `pre-commit`), so it fires even on
  manual commits, not only through you.
- `.claude/settings.local.json` is gitignored.
- Claude Code permission posture: ASK on `git add`, `git commit`, `git push`; DENY
  force-push, `git reset --hard`, and `--no-verify`. Never run with
  `--dangerously-skip-permissions`. Treat deny rules as a fence, not a guarantee.
- Commit with a GitHub `noreply` email. Keep commits small and descriptive.

## Conventions

- Language: code, identifiers, commits, docs and README in **English** (a Portuguese
  README may be added). **Prompts and summary output are in Portuguese** because the
  source documents are Brazilian Portuguese.
- Python 3.12+, `uv`, `ruff`, `pytest`, type hints, small modules.
- Tests rely on **synthetic** Portuguese legal-style PDFs generated with `reportlab`,
  including scanned-style pages and planted fake citations to test the verifier.
- Ask before adding a dependency. Prefer the standard library.

## Phases

0. Discovery and scope (outside the repo). Mac mini chip/RAM confirmed (see above).
   OPEN ITEM: validate the default summary template with the end user, or replace it
   with their actual preferred format.
1. Feasibility check of extraction/OCR on synthetic documents
2. Repo skeleton: structure, .gitignore, permissions, gitleaks, CI (Windows + macOS),
   synthetic PDF generator
3. Extraction stage (text + OCR fallback + page markers)
4. Summarization stage (Ollama, map-reduce, Portuguese prompts, template loader —
   see "Summary template" above)
5. Verification stage and evaluation set
6. Orchestration (SQLite state, watcher, `launchd`), packaging, install script
7. Hardening, docs, handover

Current phase: 7

## How to work with the developer

Raise security, privacy, and correctness risks before implementing a change. For
uncertain factual claims, use `[Certain]` for claims verified from available
evidence, `[Likely]` for supported but unconfirmed claims, and `[Guess]` for
hypotheses, and state how an uncertain claim could be checked. Explain important
tradeoffs briefly, and finish by noting relevant remaining risks or untested behavior.
