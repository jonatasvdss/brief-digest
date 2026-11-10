"""Command line entry point: run the watcher.

Usage: brief-digest --config PATH [--once]

Nothing here prints or logs exception messages or tracebacks: they can carry file
names, which may hold case identifiers (hard rule 4). Error types and code
locations only.
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from brief_digest.config import AppConfig, ConfigError, load_config
from brief_digest.ollama import OllamaSummarizer
from brief_digest.pipeline import watch, where
from brief_digest.state import StateLocked

LOG_FILE = "brief-digest.log"
log = logging.getLogger("brief_digest")


def setup_logging(config: AppConfig) -> None:
    """Log only this package's records, to a small rotating file in the state folder."""
    config.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.log_dir / LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief-digest", description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="path to config.toml")
    parser.add_argument(
        "--once", action="store_true", help="scan twice, process what is ready, and exit"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:  # messages name config keys only
        sys.stderr.write(f"config error: {exc}\n")
        return 2
    try:
        setup_logging(config)
        summarizer = OllamaSummarizer(config.ollama_config())
        watch(config, summarizer, summarizer.ensure_local_model, cycles=2 if args.once else None)
    except StateLocked:
        sys.stderr.write("another brief-digest watcher is already using this data folder\n")
        return 3
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log.critical("fatal error_type=%s where=%s", type(exc).__name__, where(exc))
        sys.stderr.write(f"fatal error: {type(exc).__name__} at {where(exc)}\n")
        return 1
    return 0
