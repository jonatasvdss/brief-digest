"""SQLite job state. One row per document, deleted with the document (retention).

The connection holds an exclusive lock for its whole life, so a second watcher on
the same data folder fails at start instead of racing the first; the lock goes away
with the process. secure_delete overwrites deleted rows, so a purged document leaves
no folder name behind in the database file.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JobStatus = Literal["queued", "done", "incomplete", "rejected", "failed"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL UNIQUE,      -- output folder name; never logged
    status TEXT NOT NULL,
    failure_type TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,         -- claim, or start of the last attempt
    finished_at REAL                  -- set with a final status; retention counts from it
)
"""
_COLUMNS = "id, folder, status, failure_type, attempts, updated_at, finished_at"


class StateLocked(Exception):
    """Another process is using the state database."""


@dataclass(frozen=True)
class Job:
    id: int
    folder: str
    status: JobStatus
    failure_type: str | None
    attempts: int
    updated_at: float
    finished_at: float | None


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=1, isolation_level=None)  # autocommit
    try:
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(_SCHEMA)
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        conn.close()
        if "locked" in str(exc):
            raise StateLocked from None
        raise
    return conn


def _jobs(conn: sqlite3.Connection, where: str, *args: object) -> list[Job]:
    # `where` is always a literal from this module, never outside input.
    rows = conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE {where} ORDER BY id", args)  # noqa: S608
    return [Job(*row) for row in rows]


def add(conn: sqlite3.Connection, folder: str, now: float) -> int:
    cursor = conn.execute(
        "INSERT INTO jobs (folder, status, updated_at) VALUES (?, 'queued', ?)", (folder, now)
    )
    return int(cursor.lastrowid or 0)


def folders(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT folder FROM jobs")}


def due(conn: sqlite3.Connection, now: float, retry_s: float) -> list[Job]:
    """Queued jobs: new ones at once, retried ones after the retry delay."""
    return _jobs(conn, "status = 'queued' AND (attempts = 0 OR updated_at <= ?)", now - retry_s)


def start_attempt(conn: sqlite3.Connection, job_id: int, now: float) -> None:
    conn.execute(
        "UPDATE jobs SET attempts = attempts + 1, updated_at = ? WHERE id = ?", (now, job_id)
    )


def requeue(conn: sqlite3.Connection, job_id: int, failure_type: str) -> None:
    conn.execute("UPDATE jobs SET failure_type = ? WHERE id = ?", (failure_type, job_id))


def finish(
    conn: sqlite3.Connection, job_id: int, status: JobStatus, failure_type: str | None, now: float
) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, failure_type = ?, finished_at = ? WHERE id = ?",
        (status, failure_type, now, job_id),
    )


def expired(conn: sqlite3.Connection, cutoff: float) -> list[Job]:
    return _jobs(conn, "finished_at IS NOT NULL AND finished_at <= ?", cutoff)


def delete(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY status"))
