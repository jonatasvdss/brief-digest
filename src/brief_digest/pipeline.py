"""Orchestration: inbox -> one output folder per PDF -> extract, summarize, verify.

A PDF is claimed by moving it into its own folder under the output directory; the
stages then work in that folder, and retention deletes the folder and its database
row together. Logs identify documents by job number only: folder names come from
file names, which may carry case identifiers (hard rule 4).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from brief_digest import state
from brief_digest.config import AppConfig
from brief_digest.extract import EXTRACT_JSON, extract
from brief_digest.summarize import (
    BANNER,
    SUMMARY_MD,
    SummarizeConfig,
    Summarizer,
    SummarizerError,
    summarize,
)
from brief_digest.verify import verify

log = logging.getLogger(__name__)

SOURCE_PDF = "original.pdf"
RETENTION_S = 7 * 24 * 3600
PURGE_EVERY_S = 3600
_MAX_FOLDER_CHARS = 100

Seen = dict[str, tuple[int, int]]  # inbox file name -> (size, mtime) at the last scan
Clock = Callable[[], float]


def where(exc: BaseException) -> str:
    """Last line of this package on the way to the error: locates a failure without
    its message, which could carry a file name."""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if Path(f.filename).parent == Path(__file__).parent]
    frame = (ours or frames or [None])[-1]
    return f"{Path(frame.filename).name}:{frame.lineno}" if frame else "unknown"


def folder_name(stem: str, taken: Callable[[str], bool]) -> str:
    base = stem[:_MAX_FOLDER_CHARS].strip().rstrip(". ") or "documento"
    name, n = base, 1
    while taken(name):
        n += 1
        name = f"{base} ({n})"
    return name


def _claim(config: AppConfig, conn: sqlite3.Connection, pdf: Path, now: float) -> bool:
    known = state.folders(conn)
    folder = folder_name(pdf.stem, lambda name: name in known or (config.output / name).exists())
    job_dir = config.output / folder
    job_dir.mkdir()
    job_id = state.add(conn, folder, now)
    try:
        pdf.replace(job_dir / SOURCE_PDF)
    except OSError as exc:  # e.g. still open in another program: try again next scan
        state.delete(conn, job_id)
        job_dir.rmdir()
        log.warning("claim failed error_type=%s", type(exc).__name__)
        return False
    log.info("claimed job=%d", job_id)
    return True


def claim_stable(config: AppConfig, conn: sqlite3.Connection, seen: Seen, now: float) -> int:
    """Claim inbox PDFs whose size and mtime did not change since the previous scan,
    so a file still being downloaded or copied is left alone."""
    current: Seen = {}
    claimed = 0
    for path in sorted(config.inbox.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() != ".pdf":
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        signature = (stat.st_size, stat.st_mtime_ns)
        if stat.st_size and seen.get(path.name) == signature and _claim(config, conn, path, now):
            claimed += 1
        else:
            current[path.name] = signature
    seen.clear()
    seen.update(current)
    return claimed


def render_not_summarized(failure_type: str) -> str:
    return (
        f"{BANNER}\n\n**Resumo não gerado.** Não foi possível resumir esta peça "
        f"automaticamente (motivo: `{failure_type}`). Consulte a peça original.\n"
    )


def _run_stages(
    config: AppConfig, job_dir: Path, summarizer: Summarizer
) -> tuple[state.JobStatus, str | None, bool]:
    """Status, failure type, and whether trying again could help: yes when the model
    server failed, no when the document itself could not be handled."""
    try:
        if not (job_dir / EXTRACT_JSON).is_file():  # written last: extraction finished
            extract(job_dir / SOURCE_PDF, job_dir, config.extract_config())
        result = summarize(job_dir, summarizer, SummarizeConfig(config.template))
        if result.status != "ok":
            return result.status, result.failure_type, result.status == "failed"
        verify(job_dir, config.template)
    except Exception as exc:  # one bad document must not stop the watcher
        return "failed", f"{type(exc).__name__}@{where(exc)}", False
    return "done", None, False


def _finalize(
    conn: sqlite3.Connection,
    job: state.Job,
    job_dir: Path,
    status: state.JobStatus,
    failure_type: str | None,
    now: float,
) -> None:
    if status in ("rejected", "failed"):
        try:  # also replaces a summary that was written but could not be verified
            (job_dir / SUMMARY_MD).write_text(
                render_not_summarized(failure_type or status), encoding="utf-8", newline="\n"
            )
        except OSError as exc:
            log.warning("notice not written job=%d error_type=%s", job.id, type(exc).__name__)
    state.finish(conn, job.id, status, failure_type, now)
    log.info("finished job=%d status=%s failure_type=%s", job.id, status, failure_type)


def process(
    config: AppConfig,
    conn: sqlite3.Connection,
    job: state.Job,
    summarizer: Summarizer,
    clock: Clock = time.time,
) -> None:
    """One attempt. Model server failures are retried up to max_attempts; everything
    else is final: rejected output (the model is deterministic), an incomplete
    extraction, and a document that raised an error."""
    job_dir = config.output / job.folder
    if job.attempts >= config.max_attempts:  # every earlier attempt died with the process
        _finalize(conn, job, job_dir, "failed", "too_many_attempts", clock())
        return
    state.start_attempt(conn, job.id, clock())
    start = time.monotonic()
    status, failure_type, retry = _run_stages(config, job_dir, summarizer)
    attempt = job.attempts + 1
    log.info(
        "attempt job=%d n=%d duration_ms=%d", job.id, attempt, (time.monotonic() - start) * 1e3
    )
    if retry and attempt < config.max_attempts:
        state.requeue(conn, job.id, failure_type or "failed")
        log.warning("will retry job=%d failure_type=%s", job.id, failure_type)
        return
    _finalize(conn, job, job_dir, status, failure_type, clock())


def _delete_job_dir(config: AppConfig, job_dir: Path) -> None:
    """Delete one document folder, and nothing that is not directly inside output."""
    if job_dir.parent != config.output or job_dir.name in {"", ".", ".."}:
        raise ValueError("job folder outside the output directory")
    if job_dir.is_symlink():
        job_dir.unlink()
    elif job_dir.exists():
        shutil.rmtree(job_dir)


def purge_expired(config: AppConfig, conn: sqlite3.Connection, now: float) -> int:
    """Delete the folder and database row of every job finished 7 or more days ago."""
    purged = 0
    for job in state.expired(conn, now - RETENTION_S):
        try:
            _delete_job_dir(config, config.output / job.folder)
        except (OSError, ValueError) as exc:  # keep the row: retried at the next purge
            log.warning("purge failed job=%d error_type=%s", job.id, type(exc).__name__)
            continue
        state.delete(conn, job.id)
        log.info("purged job=%d", job.id)
        purged += 1
    return purged


def run_once(
    config: AppConfig,
    conn: sqlite3.Connection,
    summarizer: Summarizer,
    seen: Seen,
    ready: Callable[[], bool] = lambda: True,
    clock: Clock = time.time,
) -> None:
    claim_stable(config, conn, seen, clock())
    jobs = state.due(conn, clock(), config.retry_minutes * 60)
    if jobs and ready():
        for job in jobs:
            process(config, conn, job, summarizer, clock)


def watch(
    config: AppConfig,
    summarizer: Summarizer,
    check: Callable[[], None],
    cycles: int | None = None,
) -> None:
    """Poll the inbox forever (or `cycles` times). `check` raises SummarizerError while
    the model server is not usable; documents then wait instead of using up attempts.
    Retention runs regardless."""
    for folder in (config.inbox, config.output, config.state_dir):
        folder.mkdir(parents=True, exist_ok=True)
    conn = state.connect(config.db)
    was_ready = True

    def ready() -> bool:
        nonlocal was_ready
        try:
            check()
        except SummarizerError as exc:
            if was_ready:
                log.warning("summarizer not ready failure_type=%s", exc.failure_type)
            was_ready = False
            return False
        if not was_ready:
            log.info("summarizer ready")
        was_ready = True
        return True

    try:
        log.info("watching jobs=%s", state.counts(conn))
        seen: Seen = {}
        next_purge = 0.0
        done = 0
        while cycles is None or done < cycles:
            if done:
                time.sleep(config.poll_seconds)
            run_once(config, conn, summarizer, seen, ready)
            if time.time() >= next_purge:
                purge_expired(config, conn, time.time())
                next_purge = time.time() + PURGE_EVERY_S
            done += 1
    finally:
        conn.close()
