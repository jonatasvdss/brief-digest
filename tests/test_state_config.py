import json
import os
import plistlib
from pathlib import Path

import pytest
from scripts import install

from brief_digest import state
from brief_digest.cli import main
from brief_digest.config import AppConfig, ConfigError, load_config, parse_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def base(tmp_path: Path, **extra: object) -> dict[str, object]:
    return {
        "data_dir": str(tmp_path / "data"),
        "template": str(tmp_path / "t.yaml"),
        "tessdata": str(tmp_path / "tess"),
        **extra,
    }


# --- state --------------------------------------------------------------------------


def test_second_connection_to_the_same_database_is_refused(tmp_path: Path) -> None:
    first = state.connect(tmp_path / "state.db")
    try:
        with pytest.raises(state.StateLocked):
            state.connect(tmp_path / "state.db")
    finally:
        first.close()
    state.connect(tmp_path / "state.db").close()  # free again once the holder is gone


def test_state_survives_a_restart(tmp_path: Path) -> None:
    conn = state.connect(tmp_path / "state.db")
    job_id = state.add(conn, "a", 1.0)
    state.start_attempt(conn, job_id, 2.0)
    conn.close()
    conn = state.connect(tmp_path / "state.db")
    try:
        (job,) = state.due(conn, now=1000.0, retry_s=10.0)
        assert (job.folder, job.status, job.attempts, job.updated_at) == ("a", "queued", 1, 2.0)
        assert state.due(conn, now=5.0, retry_s=10.0) == []  # retry delay not over
        assert state.counts(conn) == {"queued": 1}
    finally:
        conn.close()


def test_deleted_rows_leave_no_folder_name_in_the_database_file(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    conn = state.connect(path)
    job_id = state.add(conn, "nome-identificavel-da-peca", 1.0)
    state.delete(conn, job_id)
    conn.close()
    assert b"nome-identificavel" not in path.read_bytes()


# --- config -------------------------------------------------------------------------


def test_minimal_config_gets_the_documented_defaults(tmp_path: Path) -> None:
    config = parse_config(base(tmp_path))
    assert config.model == "gemma4:12b"
    assert config.ollama_host == "127.0.0.1"
    assert config.inbox == tmp_path / "data" / "Entrada"
    assert config.output == tmp_path / "data" / "Resumos"
    assert config.db.parent == config.log_dir.parent == tmp_path / "data" / ".state"
    assert config.extract_config().tessdata == tmp_path / "tess"
    assert config.extract_config().min_native_chars == 40
    assert config.extract_config().ocr_image_coverage == 0.5


@pytest.mark.parametrize(
    "extra",
    [
        {"ollama_host": "example.com"},
        {"ollama_host": "192.168.0.10"},
        {"ollama_host": "localhost"},
        {"model": "gemma4:12b-cloud"},
        {"ollama_port": 0},
        {"ollama_port": "11434"},
        {"max_attempts": 0},
        {"max_attempts": True},
        {"poll_seconds": 0},
        {"ocr_image_coverage": 0},
        {"retention_days": 30},
        {"data_dir": "relative/path"},
        {"template": ""},
        {"tessdata": 3},
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, extra: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_config(base(tmp_path, **extra))


def test_missing_or_broken_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("data_dir = ", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(broken)


def test_cli_reports_a_config_error_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "config error" in capsys.readouterr().err


def test_cli_refuses_a_non_loopback_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        install.config_text(tmp_path / "data", "gemma4:12b") + 'ollama_host = "10.0.0.5"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(path), "--once"]) == 2
    assert "loopback" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


# --- installer ----------------------------------------------------------------------


def test_installer_config_round_trips_through_the_loader(tmp_path: Path) -> None:
    data_dir = tmp_path / 'pasta com "aspas" e acentuação'
    path = tmp_path / "config.toml"
    path.write_text(install.config_text(data_dir, "qwen3.5:9b"), encoding="utf-8")
    config = load_config(path)
    assert config.data_dir == data_dir
    assert config.model == "qwen3.5:9b"
    assert config.template == REPO_ROOT / "templates" / "default.yaml"
    assert config.template.is_file()
    assert config.tessdata == REPO_ROOT / ".tessdata"


def test_agent_plist_runs_the_watcher_and_writes_no_launchd_logs(tmp_path: Path) -> None:
    plist = plistlib.loads(
        plistlib.dumps(install.agent_plist(tmp_path / "python", tmp_path / "config.toml"))
    )
    assert plist["ProgramArguments"] == [
        str(tmp_path / "python"),
        "-m",
        "brief_digest",
        "--config",
        str(tmp_path / "config.toml"),
    ]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert "StandardErrorPath" not in plist
    assert "StandardOutPath" not in plist


def test_cloud_setting_must_be_explicitly_true(tmp_path: Path) -> None:
    assert not install.cloud_disabled(tmp_path)
    settings = tmp_path / ".ollama" / "server.json"
    settings.parent.mkdir()
    for content, expected in [
        ("{}", False),
        ('{"disable_ollama_cloud": false}', False),
        ('{"disable_ollama_cloud": "true"}', False),
        ("not json", False),
        (json.dumps({"disable_ollama_cloud": True}), True),
    ]:
        settings.write_text(content, encoding="utf-8")
        assert install.cloud_disabled(tmp_path) is expected, content


def test_preflight_lists_everything_that_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = AppConfig(
        data_dir=home / "Documents" / "BriefDigest",
        template=tmp_path / "missing.yaml",
        tessdata=tmp_path / "missing",
        ollama_port=1,  # nothing listens here
    )
    problems = "\n".join(install.preflight(config, home, macos=True))
    assert "template" in problems
    assert "fetch_tessdata.py" in problems
    assert "disable_ollama_cloud" in problems
    assert "ollama_unavailable" in problems
    assert "Documents, Desktop or Downloads" in problems
    assert "Documents, Desktop" not in "\n".join(install.preflight(config, home, macos=False))


def test_backup_exclusion_reports_failure_when_the_tool_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install, "TMUTIL", str(tmp_path / "no-such-tool"))
    assert install.exclude_from_backups(tmp_path) is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_data_folder_is_made_owner_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o755)
    install.make_private(data_dir)
    assert data_dir.stat().st_mode & 0o777 == 0o700


def test_user_guide_states_the_draft_warning_and_the_deletion() -> None:
    guide = (REPO_ROOT / "docs" / install.USER_GUIDE).read_text(encoding="utf-8")
    assert "RASCUNHO" in guide
    assert "Confira sempre com a peça original" in guide
    assert "Sete dias" in guide
    assert "Entrada" in guide
    assert "Resumos" in guide
