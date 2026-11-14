"""Write a report about the installation that is safe to send to the developer.
Run by `Diagnostico.command`.

The report holds program state, counts, failure types and the app log. It never
reads documents, never lists file or folder names, and replaces the home folder
path (which carries the user name) with `~`. The app log is content-free by design
(hard rule 4).

Usage: uv run --locked python -m scripts.diagnose [--data-dir DIR]
"""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts import install
from scripts.setup_mac import api, local_models

from brief_digest import __version__
from brief_digest.cli import LOG_FILE
from brief_digest.config import ConfigError, load_config
from brief_digest.summarize import SUMMARY_MD

REPORT_NAME = "diagnostico.txt"
LOG_LINES = 200
_FINISHED = re.compile(r"finished job=\d+ status=(\S+) failure_type=(\S+)")


def _output(*command: str) -> tuple[int, str]:
    try:
        done = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603
    except OSError:
        return 127, ""
    return done.returncode, done.stdout


def agent_state() -> str:
    if sys.platform != "darwin":
        return "not macOS"
    import os

    code, out = _output(install.LAUNCHCTL, "print", f"gui/{os.getuid()}/{install.LABEL}")
    if code != 0:
        return "not loaded"
    state = re.search(r"^\s*state = (.+)$", out, re.MULTILINE)
    return state.group(1) if state else "loaded"


def outcomes(log_text: str) -> Counter[str]:
    """How documents ended, by status and failure type, counted from the app log."""
    return Counter(f"{status} {failure}" for status, failure in _FINISHED.findall(log_text))


def report(data_dir: Path, home: Path) -> str:
    lines = [
        "brief-digest diagnostic report",
        f"time: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"version: {__version__}",
        f"system: {platform.system()} {platform.release()} {platform.machine()}",
        f"python: {platform.python_version()}",
        f"watcher: {agent_state()}",
    ]
    version = api("/api/version", timeout=3)
    lines.append(f"ollama: {version.get('version') if version else 'not reachable'}")
    lines.append(f"ollama cloud disabled: {install.cloud_disabled(home)}")
    lines.append(f"ollama local models: {', '.join(local_models()) or 'none'}")
    try:
        config = load_config(data_dir / install.CONFIG_NAME)
    except ConfigError as exc:
        return "\n".join([*lines, f"config: ERROR {exc}"]) + "\n"
    lines += [
        f"config model: {config.model}",
        f"config template found: {config.template.is_file()}",
        f"ocr data found: {(config.tessdata / 'por.traineddata').is_file()}",
    ]
    if sys.platform == "darwin":
        _, out = _output(install.TMUTIL, "isexcluded", str(data_dir))
        lines.append(f"time machine excluded: {'[Excluded]' in out}")
    if config.inbox.is_dir() and config.output.is_dir():
        folders = [p for p in config.output.iterdir() if p.is_dir()]
        lines += [
            f"pdfs waiting in inbox: {sum(1 for p in config.inbox.glob('*.[pP][dD][fF]'))}",
            f"document folders: {len(folders)}",
            f"document folders without summary: "
            f"{sum(1 for p in folders if not (p / SUMMARY_MD).is_file())}",
        ]
    log_path = config.log_dir / LOG_FILE
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    lines.append("document outcomes in the current log file:")
    lines += [f"  {count} x {outcome}" for outcome, count in sorted(outcomes(log_text).items())]
    lines.append(f"last {LOG_LINES} log lines:")
    lines += [f"  {line}" for line in log_text.splitlines()[-LOG_LINES:]]
    return ("\n".join(lines) + "\n").replace(str(home), "~")


def main(argv: list[str]) -> int:
    home = Path.home()
    parser = argparse.ArgumentParser(description="Gera um relatório de diagnóstico.")
    parser.add_argument("--data-dir", type=Path, default=install.default_data_dir(home))
    args = parser.parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    text = report(data_dir, home)
    print(text)
    if data_dir.is_dir():
        path = data_dir / REPORT_NAME
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"Relatório gravado em: {path}")
        print("Ele não contém texto nem nomes das peças. Envie-o a quem instalou o programa.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
