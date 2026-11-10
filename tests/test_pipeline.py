import logging
import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from brief_digest import state
from brief_digest.config import AppConfig
from brief_digest.extract import EXTRACT_JSON, FULL_MD
from brief_digest.pipeline import (
    RETENTION_S,
    SOURCE_PDF,
    claim_stable,
    folder_name,
    purge_expired,
    run_once,
    watch,
)
from brief_digest.summarize import BANNER, SUMMARY_MD, SummarizerError
from brief_digest.verify import NOTICE_PREFIX
from tests.test_summarize import DEFAULT_TEMPLATE, FakeSummarizer, per_page

DAY = 24 * 3600


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        data_dir=tmp_path / "data",
        template=DEFAULT_TEMPLATE,
        tessdata=tmp_path / "no-tessdata",
        poll_seconds=0.01,
    )
    for folder in (config.inbox, config.output, config.state_dir):
        folder.mkdir(parents=True)
    return config


@pytest.fixture
def conn(config: AppConfig) -> Iterator[sqlite3.Connection]:
    conn = state.connect(config.db)
    yield conn
    conn.close()


def drop(config: AppConfig, pdf: Path, name: str = "peça de teste.pdf") -> Path:
    return Path(shutil.copy(pdf, config.inbox / name))


def unavailable(prompt: str, pages: list[int]) -> str:
    raise SummarizerError("ollama_unavailable")


def all_jobs(conn: sqlite3.Connection) -> list[state.Job]:
    return state._jobs(conn, "1 = 1")


def run_twice(
    config: AppConfig, conn: sqlite3.Connection, summarizer: FakeSummarizer, **kw
) -> None:
    seen: dict[str, tuple[int, int]] = {}
    run_once(config, conn, summarizer, seen, **kw)
    run_once(config, conn, summarizer, seen, **kw)


# --- claiming -----------------------------------------------------------------------


def test_folder_name_is_made_unique_and_never_empty() -> None:
    taken = {"a", "a (2)"}
    assert folder_name("a", taken.__contains__) == "a (3)"
    assert folder_name(" . ", taken.__contains__) == "documento"
    assert folder_name("x" * 300 + ". ", taken.__contains__) == "x" * 100


def test_pdf_is_claimed_only_once_it_stops_changing(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    pdf = drop(config, synthetic_pdfs["appeal_native"])
    seen: dict[str, tuple[int, int]] = {}
    assert claim_stable(config, conn, seen, 0) == 0  # first sighting
    with pdf.open("ab") as handle:  # still being written
        handle.write(b"%more")
    assert claim_stable(config, conn, seen, 0) == 0
    assert claim_stable(config, conn, seen, 0) == 1
    assert not pdf.exists()
    assert (config.output / "peça de teste" / SOURCE_PDF).is_file()
    assert [job.status for job in all_jobs(conn)] == ["queued"]


def test_only_real_non_empty_pdfs_are_claimed(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    source = synthetic_pdfs["appeal_native"]
    drop(config, source, "UPPER.PDF")
    drop(config, source, ".hidden.pdf")
    drop(config, source, "partial.pdf.crdownload")
    drop(config, source, "notes.txt")
    (config.inbox / "empty.pdf").touch()
    (config.inbox / "folder.pdf").mkdir()
    seen: dict[str, tuple[int, int]] = {}
    claim_stable(config, conn, seen, 0)
    assert claim_stable(config, conn, seen, 0) == 1
    assert [p.name for p in config.output.iterdir()] == ["UPPER"]
    assert len(list(config.inbox.iterdir())) == 5


def test_same_file_name_twice_gets_two_folders(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    for _ in range(2):
        drop(config, synthetic_pdfs["appeal_native"], "a.pdf")
        seen: dict[str, tuple[int, int]] = {}
        claim_stable(config, conn, seen, 0)
        claim_stable(config, conn, seen, 0)
    assert sorted(p.name for p in config.output.iterdir()) == ["a", "a (2)"]


# --- processing ---------------------------------------------------------------------


def test_dropped_pdf_becomes_a_verified_summary(
    config: AppConfig,
    conn: sqlite3.Connection,
    synthetic_pdfs: dict[str, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    drop(config, synthetic_pdfs["appeal_native"])
    with caplog.at_level(logging.INFO):
        run_twice(config, conn, FakeSummarizer(per_page))

    job_dir = config.output / "peça de teste"
    summary = (job_dir / SUMMARY_MD).read_text(encoding="utf-8")
    assert summary.startswith(BANNER)
    assert NOTICE_PREFIX in summary
    assert (job_dir / FULL_MD).is_file()
    assert (job_dir / SOURCE_PDF).is_file()
    assert list(config.inbox.iterdir()) == []
    (job,) = all_jobs(conn)
    assert (job.status, job.attempts, job.failure_type) == ("done", 1, None)
    assert job.finished_at is not None

    assert "job=1" in caplog.text
    assert "peça de teste" not in caplog.text  # file names may carry case identifiers
    assert "identificacao" not in caplog.text


def test_rejected_output_is_final_and_leaves_a_notice(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    drop(config, synthetic_pdfs["appeal_native"])
    summarizer = FakeSummarizer(lambda prompt, pages: "not json")
    run_twice(config, conn, summarizer)
    run_once(config, conn, summarizer, {})

    (job,) = all_jobs(conn)
    assert (job.status, job.attempts, job.failure_type) == ("rejected", 1, "invalid_json")
    summary = (config.output / job.folder / SUMMARY_MD).read_text(encoding="utf-8")
    assert summary.startswith(BANNER)
    assert "Resumo não gerado" in summary
    assert "invalid_json" in summary


def test_failures_are_retried_after_a_delay_then_given_up(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    drop(config, synthetic_pdfs["appeal_native"])
    clock = Clock()
    summarizer = FakeSummarizer(unavailable)
    run_twice(config, conn, summarizer, clock=clock)
    (job,) = all_jobs(conn)
    assert (job.status, job.attempts, job.failure_type) == ("queued", 1, "ollama_unavailable")
    job_dir = config.output / job.folder
    assert not (job_dir / SUMMARY_MD).exists()
    extracted_at = (job_dir / EXTRACT_JSON).stat().st_mtime_ns

    run_once(config, conn, summarizer, {}, clock=clock)  # too soon
    assert all_jobs(conn)[0].attempts == 1

    for _ in range(2):
        clock.now += config.retry_minutes * 60
        run_once(config, conn, summarizer, {}, clock=clock)
    (job,) = all_jobs(conn)
    assert (job.status, job.attempts, job.failure_type) == ("failed", 3, "ollama_unavailable")
    assert job.finished_at == clock.now
    assert "Resumo não gerado" in (job_dir / SUMMARY_MD).read_text(encoding="utf-8")
    assert (job_dir / EXTRACT_JSON).stat().st_mtime_ns == extracted_at  # not extracted again


def test_retry_succeeds_once_the_model_is_back(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    drop(config, synthetic_pdfs["appeal_native"])
    clock = Clock()
    run_twice(config, conn, FakeSummarizer(unavailable), clock=clock)
    clock.now += config.retry_minutes * 60
    run_once(config, conn, FakeSummarizer(per_page), {}, clock=clock)
    (job,) = all_jobs(conn)
    assert (job.status, job.attempts) == ("done", 2)


def test_unreadable_pdf_fails_without_leaking_its_name(
    config: AppConfig, conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    (config.inbox / "segredo.pdf").write_bytes(b"this is not a pdf")
    summarizer = FakeSummarizer(per_page)
    with caplog.at_level(logging.INFO):
        run_twice(config, conn, summarizer)
    (job,) = all_jobs(conn)
    assert (job.status, job.attempts) == ("failed", 1)  # retrying cannot fix the file
    assert job.failure_type is not None
    assert job.failure_type.endswith("@extract.py:" + job.failure_type.rsplit(":", 1)[1])
    assert "segredo" not in job.failure_type
    assert "segredo" not in caplog.text
    assert summarizer.calls == []
    assert (config.output / "segredo" / SUMMARY_MD).is_file()


def test_job_whose_attempts_all_crashed_is_given_up(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    drop(config, synthetic_pdfs["appeal_native"])
    clock = Clock()
    seen: dict[str, tuple[int, int]] = {}
    claim_stable(config, conn, seen, clock())
    claim_stable(config, conn, seen, clock())
    for _ in range(config.max_attempts):  # attempts that never reported back
        state.start_attempt(conn, 1, clock())
    clock.now += config.retry_minutes * 60
    summarizer = FakeSummarizer(per_page)
    run_once(config, conn, summarizer, {}, clock=clock)
    (job,) = all_jobs(conn)
    assert (job.status, job.failure_type) == ("failed", "too_many_attempts")
    assert summarizer.calls == []


def test_documents_wait_while_the_model_server_is_not_ready(
    config: AppConfig, conn: sqlite3.Connection, synthetic_pdfs: dict[str, Path]
) -> None:
    drop(config, synthetic_pdfs["appeal_native"])
    summarizer = FakeSummarizer(per_page)
    run_twice(config, conn, summarizer, ready=lambda: False)
    (job,) = all_jobs(conn)
    assert (job.status, job.attempts) == ("queued", 0)
    assert summarizer.calls == []
    assert (config.output / job.folder / SOURCE_PDF).is_file()


# --- retention ----------------------------------------------------------------------


def finished_job(config: AppConfig, conn: sqlite3.Connection, folder: str, at: float) -> Path:
    job_dir = config.output / folder
    job_dir.mkdir()
    (job_dir / SOURCE_PDF).write_bytes(b"x")
    (job_dir / "sub").mkdir()
    (job_dir / "sub" / "note.txt").write_text("x", encoding="utf-8")
    state.finish(conn, state.add(conn, folder, at), "done", None, at)
    return job_dir


def test_purge_deletes_folder_and_row_after_seven_days(
    config: AppConfig, conn: sqlite3.Connection
) -> None:
    now = 100 * DAY
    old = finished_job(config, conn, "old", now - RETENTION_S)
    recent = finished_job(config, conn, "recent", now - RETENTION_S + 60)
    queued = config.output / "queued"
    queued.mkdir()
    state.add(conn, "queued", now - 30 * DAY)
    stranger = config.output / "not-ours"
    stranger.mkdir()

    assert RETENTION_S == 7 * DAY
    assert purge_expired(config, conn, now) == 1
    assert not old.exists()
    assert recent.is_dir()
    assert queued.is_dir()
    assert stranger.is_dir()
    assert sorted(job.folder for job in all_jobs(conn)) == ["queued", "recent"]


def test_failed_jobs_are_purged_seven_days_after_the_last_attempt(
    config: AppConfig, conn: sqlite3.Connection
) -> None:
    now = 100 * DAY
    job_dir = config.output / "failed"
    job_dir.mkdir()
    state.finish(conn, state.add(conn, "failed", 0), "failed", "x", now - RETENTION_S)
    assert purge_expired(config, conn, now) == 1
    assert not job_dir.exists()


@pytest.mark.parametrize("folder", ["../outside", "..", "a/b", ""])
def test_purge_never_deletes_outside_the_output_folder(
    config: AppConfig, conn: sqlite3.Connection, folder: str
) -> None:
    outside = config.data_dir / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    nested = config.output / "a" / "b"
    nested.mkdir(parents=True)
    state.finish(conn, state.add(conn, folder, 0), "done", None, 0)

    assert purge_expired(config, conn, 100 * DAY) == 0
    assert (outside / "keep.txt").is_file()
    assert nested.is_dir()
    assert config.inbox.is_dir()
    assert len(all_jobs(conn)) == 1  # kept, and reported in the log


def test_purge_absolute_folder_is_refused(config: AppConfig, conn: sqlite3.Connection) -> None:
    outside = config.data_dir / "outside"
    outside.mkdir()
    state.finish(conn, state.add(conn, str(outside), 0), "done", None, 0)
    assert purge_expired(config, conn, 100 * DAY) == 0
    assert outside.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_purge_removes_a_symlink_not_its_target(
    config: AppConfig, conn: sqlite3.Connection
) -> None:
    target = config.data_dir / "target"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    (config.output / "link").symlink_to(target, target_is_directory=True)
    state.finish(conn, state.add(conn, "link", 0), "done", None, 0)
    assert purge_expired(config, conn, 100 * DAY) == 1
    assert (target / "keep.txt").is_file()
    assert not (config.output / "link").exists()


def test_purge_with_missing_folder_still_drops_the_row(
    config: AppConfig, conn: sqlite3.Connection
) -> None:
    state.finish(conn, state.add(conn, "gone", 0), "done", None, 0)
    assert purge_expired(config, conn, 100 * DAY) == 1
    assert all_jobs(conn) == []


# --- watcher ------------------------------------------------------------------------


def test_watch_processes_a_pdf_and_creates_its_folders(
    tmp_path: Path, synthetic_pdfs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        template=DEFAULT_TEMPLATE,
        tessdata=tmp_path / "none",
        poll_seconds=0.01,
    )
    config.inbox.mkdir(parents=True)
    drop(config, synthetic_pdfs["appeal_native"])
    checks = []
    watch(config, FakeSummarizer(per_page), lambda: checks.append(1), cycles=2)
    assert (config.output / "peça de teste" / SUMMARY_MD).is_file()
    assert checks == [1]  # the model server is only checked when there is work


def test_watch_keeps_documents_queued_while_the_model_is_down(
    tmp_path: Path, synthetic_pdfs: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        template=DEFAULT_TEMPLATE,
        tessdata=tmp_path / "none",
        poll_seconds=0.01,
    )
    config.inbox.mkdir(parents=True)
    drop(config, synthetic_pdfs["appeal_native"])

    def down() -> None:
        raise SummarizerError("ollama_unavailable")

    with caplog.at_level(logging.INFO):
        watch(config, FakeSummarizer(per_page), down, cycles=4)
    assert caplog.text.count("summarizer not ready") == 1  # logged once, not every poll
    conn = state.connect(config.db)
    try:
        assert [(j.status, j.attempts) for j in all_jobs(conn)] == [("queued", 0)]
    finally:
        conn.close()


def test_watch_warns_about_folders_retention_will_never_delete(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        template=DEFAULT_TEMPLATE,
        tessdata=tmp_path / "none",
        poll_seconds=0.01,
    )
    (config.output / "pasta-desconhecida").mkdir(parents=True)
    with caplog.at_level(logging.INFO):
        watch(config, FakeSummarizer(per_page), lambda: None, cycles=1)
    (record,) = [r for r in caplog.records if "untracked_folders" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "untracked_folders=1" in record.getMessage()
    assert "pasta-desconhecida" not in caplog.text
    assert (config.output / "pasta-desconhecida").is_dir()
