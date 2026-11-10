"""Set up brief-digest on this machine: data folders, config file and, on macOS, a
launchd agent that keeps the watcher running.

Usage: uv run --locked --no-dev python scripts/install.py [--data-dir DIR] [--model NAME]
       uv run --locked --no-dev python scripts/install.py --uninstall

This script downloads nothing (hard rule 5). It checks that the OCR data and the
model are already installed and that Ollama's cloud features are off, and stops
with instructions if not. It makes the data folder private to the current user and,
on macOS, excludes it from Time Machine, so backups do not outlive the 7-day
retention. Uninstalling removes the agent and leaves the data folder.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from brief_digest.config import AppConfig, ConfigError, load_config
from brief_digest.ollama import OllamaSummarizer
from brief_digest.summarize import SummarizerError

REPO = Path(__file__).resolve().parent.parent
LABEL = "local.brief-digest.watcher"
CONFIG_NAME = "config.toml"
LAUNCHCTL = "/bin/launchctl"
TMUTIL = "/usr/bin/tmutil"
USER_GUIDE = "LEIA-ME.txt"
# macOS asks for per-app permission to read these; a background agent cannot answer.
PROTECTED = ("Documents", "Desktop", "Downloads")


def config_text(data_dir: Path, model: str) -> str:
    def quoted(value: object) -> str:  # JSON string escapes are valid TOML ones
        return json.dumps(str(value), ensure_ascii=False)

    return (
        "# brief-digest configuration. Restart the watcher after editing.\n"
        f"data_dir = {quoted(data_dir)}\n"
        f"template = {quoted(REPO / 'templates' / 'default.yaml')}\n"
        f"tessdata = {quoted(REPO / '.tessdata')}\n"
        f"model = {quoted(model)}\n"
    )


def cloud_disabled(home: Path) -> bool:
    try:
        settings = json.loads((home / ".ollama" / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(settings, dict) and settings.get("disable_ollama_cloud") is True


def preflight(config: AppConfig, home: Path, macos: bool) -> list[str]:
    problems = []
    if not config.template.is_file():
        problems.append("Summary template not found; check `template` in the config file.")
    if not (config.tessdata / "por.traineddata").is_file():
        problems.append("Portuguese OCR data missing. Run: uv run python scripts/fetch_tessdata.py")
    if not cloud_disabled(home):
        problems.append(
            'Ollama cloud features are not disabled. Put {"disable_ollama_cloud": true} '
            "in ~/.ollama/server.json and restart Ollama."
        )
    try:
        OllamaSummarizer(config.ollama_config()).ensure_local_model()
    except SummarizerError as exc:
        problems.append(
            f"Ollama check failed ({exc.failure_type}). Start Ollama and, if the model is "
            f"missing, run: ollama pull {config.model}"
        )
    if macos:
        for path in (REPO, config.data_dir):
            if any(path.is_relative_to(home / name) for name in PROTECTED):
                problems.append(
                    f"{path} is inside Documents, Desktop or Downloads, which a background "
                    "agent cannot read on macOS. Move it elsewhere in the home folder."
                )
    return problems


def agent_plist(python: Path, config_path: Path) -> dict[str, object]:
    """No log paths: the app writes its own log, and stderr could carry tracebacks."""
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "brief_digest", "--config", str(config_path)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
    }


def _plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _run(*command: str) -> int:
    try:
        return subprocess.run(command, check=False, capture_output=True).returncode  # noqa: S603
    except OSError:
        return 127


def _launchctl(*args: str) -> int:
    return _run(LAUNCHCTL, *args)


def make_private(data_dir: Path) -> None:
    """Owner-only access to the data folder and so to everything under it."""
    if os.name == "posix":
        data_dir.chmod(0o700)


def exclude_from_backups(data_dir: Path) -> bool:
    """Sticky Time Machine exclusion: it follows the folder if it is moved."""
    return _run(TMUTIL, "addexclusion", str(data_dir)) == 0


def stop_agent() -> None:
    target = f"gui/{os.getuid()}/{LABEL}"
    _launchctl("bootout", target)
    for _ in range(20):  # bootout returns before the service is gone
        if _launchctl("print", target) != 0:
            return
        time.sleep(0.5)


def start_agent(home: Path, config_path: Path) -> bool:
    path = _plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(agent_plist(Path(sys.executable), config_path)))
    stop_agent()
    return _launchctl("bootstrap", f"gui/{os.getuid()}", str(path)) == 0


def main(argv: list[str]) -> int:
    home = Path.home()
    macos = sys.platform == "darwin"
    parser = argparse.ArgumentParser(description="Install or uninstall brief-digest.")
    parser.add_argument("--data-dir", type=Path, default=home / "BriefDigest")
    parser.add_argument("--model", default=AppConfig.model)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    config_path = data_dir / CONFIG_NAME

    if args.uninstall:
        if macos:
            stop_agent()
            _plist_path(home).unlink(missing_ok=True)
        print(f"Watcher removed. Documents and settings were left in {data_dir}")
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    if config_path.is_file():
        print(f"Keeping existing config: {config_path}")
    else:
        config_path.write_text(config_text(data_dir, args.model), encoding="utf-8", newline="\n")
        print(f"Wrote config: {config_path}")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 1
    for folder in (config.inbox, config.output, config.state_dir):
        folder.mkdir(parents=True, exist_ok=True)
    make_private(config.data_dir)
    shutil.copyfile(REPO / "docs" / USER_GUIDE, config.data_dir / USER_GUIDE)

    problems = preflight(config, home, macos)
    if problems:
        print("Not installed. Fix these and run the installer again:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    command = f'"{sys.executable}" -m brief_digest --config "{config_path}"'
    if not macos:
        print(f"Checks passed. No launchd on this system; run the watcher by hand:\n  {command}")
        return 0
    if not exclude_from_backups(config.data_dir):
        print("Not installed: could not exclude the data folder from Time Machine. Check with:")
        print(f'  tmutil addexclusion "{config.data_dir}"')
        return 1
    if not start_agent(home, config_path):
        print(f"launchd refused the agent. Run the watcher by hand to see why:\n  {command}")
        return 1
    print("Installed and running.")
    print(f"  Drop PDFs in: {config.inbox}")
    print(f"  Read results in: {config.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
