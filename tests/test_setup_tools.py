"""The end-user setup tools: pure parts only. The macOS steps themselves (Ollama
install, launchd, Time Machine) run in the manual `install-smoke` CI workflow."""

import json
from pathlib import Path

import pytest
from scripts import diagnose, install, set_model, setup_mac

from brief_digest.cli import LOG_FILE
from brief_digest.config import INSTRUCTIONS_NAME, load_config
from brief_digest.summarize import INSTRUCTIONS_MAX_CHARS, SUMMARY_MD, load_instructions

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- config model line --------------------------------------------------------------


def test_with_model_replaces_only_the_model_line(tmp_path: Path) -> None:
    original = install.config_text(tmp_path / "data", "gemma4:12b") + "poll_seconds = 9\n"
    changed = install.with_model(original, "qwen3.5:9b")
    assert changed.replace('"qwen3.5:9b"', '"gemma4:12b"') == original
    path = tmp_path / "config.toml"
    path.write_text(changed, encoding="utf-8")
    config = load_config(path)
    assert (config.model, config.poll_seconds) == ("qwen3.5:9b", 9)


def test_with_model_adds_the_line_when_missing() -> None:
    assert install.with_model('data_dir = "/x"', "m:1") == 'data_dir = "/x"\nmodel = "m:1"\n'
    assert install.with_model('model="a"\nmodel_x = 1\n', "b") == 'model = "b"\nmodel_x = 1\n'


# --- Ollama settings ----------------------------------------------------------------


def test_disable_cloud_keeps_other_settings_and_reports_changes(tmp_path: Path) -> None:
    path = tmp_path / ".ollama" / "server.json"
    path.parent.mkdir()
    path.write_text('{"other": 1, "disable_ollama_cloud": false}', encoding="utf-8")
    assert setup_mac.disable_cloud(tmp_path) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "other": 1,
        "disable_ollama_cloud": True,
    }
    assert setup_mac.disable_cloud(tmp_path) is False  # already off: file untouched
    assert install.cloud_disabled(tmp_path)


def test_disable_cloud_creates_the_file_and_backs_up_an_unreadable_one(tmp_path: Path) -> None:
    assert setup_mac.disable_cloud(tmp_path) is True
    path = tmp_path / ".ollama" / "server.json"
    assert install.cloud_disabled(tmp_path)
    assert not path.with_name("server.json.bak").exists()

    path.write_text("not json", encoding="utf-8")
    assert setup_mac.disable_cloud(tmp_path) is True
    assert install.cloud_disabled(tmp_path)
    assert path.with_name("server.json.bak").read_text(encoding="utf-8") == "not json"


def test_local_models_leaves_out_cloud_and_remote_models(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = {
        "models": [
            {"name": "gemma4:12b", "size": 8},
            {"name": "big:cloud", "size": 1},
            {"name": "remote:1b", "size": 1, "remote_host": "https://example.invalid"},
            {"name": "proxy:1b", "size": 1, "remote_model": "x"},
            {"name": "stub:1b", "size": 0},
            "garbage",
        ]
    }
    monkeypatch.setattr(setup_mac, "api", lambda path, **kw: tags)
    assert setup_mac.local_models() == ["gemma4:12b"]
    monkeypatch.setattr(setup_mac, "api", lambda path, **kw: None)  # server down
    assert setup_mac.local_models() == []


# --- model switcher -----------------------------------------------------------------


def test_model_choices_put_the_evaluated_models_first() -> None:
    assert set_model.choices(["zeta:1b", "qwen3.5:9b", "alpha:2b"]) == [
        "gemma4:12b",
        "qwen3.5:9b",
        "alpha:2b",
        "zeta:1b",
    ]


def test_model_switch_refuses_cloud_models_and_keeps_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(install.config_text(tmp_path, "gemma4:12b"), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(set_model, "server_up", lambda: True)
    monkeypatch.setattr(set_model, "local_models", lambda: ["gemma4:12b"])
    assert set_model.main(["--data-dir", str(tmp_path), "--model", "huge:cloud"]) == 1
    assert path.read_text(encoding="utf-8") == before
    assert "nuvem" in capsys.readouterr().out


def test_model_switch_keeps_the_config_when_the_download_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(install.config_text(tmp_path, "gemma4:12b"), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(set_model, "server_up", lambda: True)
    monkeypatch.setattr(set_model, "local_models", lambda: ["gemma4:12b"])
    monkeypatch.setattr(set_model, "pull", lambda model: False)
    assert set_model.main(["--data-dir", str(tmp_path), "--model", "qwen3.5:9b"]) == 1
    assert path.read_text(encoding="utf-8") == before


# --- diagnostic report --------------------------------------------------------------


def test_outcomes_are_counted_from_the_log() -> None:
    log = (
        "t INFO brief_digest.pipeline finished job=1 status=done failure_type=None\n"
        "t INFO brief_digest.pipeline finished job=2 status=rejected failure_type=invalid_json\n"
        "t INFO brief_digest.pipeline finished job=3 status=done failure_type=None\n"
        "t INFO brief_digest.pipeline claimed job=4\n"
    )
    assert diagnose.outcomes(log) == {"done None": 2, "rejected invalid_json": 1}


def test_report_has_counts_but_no_names_paths_or_document_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "nome-do-usuario"
    data_dir = home / "BriefDigest"
    data_dir.mkdir(parents=True)
    (data_dir / install.CONFIG_NAME).write_text(
        install.config_text(data_dir, "gemma4:12b"), encoding="utf-8"
    )
    config = load_config(data_dir / install.CONFIG_NAME)
    for folder in (config.inbox, config.output, config.log_dir):
        folder.mkdir(parents=True)
    (config.inbox / "processo-sigiloso.pdf").write_bytes(b"x")
    done = config.output / "apelacao-sigilosa"
    done.mkdir()
    (done / SUMMARY_MD).write_text("texto sigiloso do resumo", encoding="utf-8")
    (config.output / "outra-sigilosa").mkdir()
    (config.log_dir / LOG_FILE).write_text(
        f"t INFO finished job=1 status=done failure_type=None\nt INFO opened {home}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnose, "api", lambda path, **kw: None)
    monkeypatch.setattr(diagnose, "local_models", lambda: [])

    text = diagnose.report(data_dir, home)
    assert "pdfs waiting in inbox: 1" in text
    assert "document folders: 2" in text
    assert "document folders without summary: 1" in text
    assert "1 x done None" in text
    assert "config model: gemma4:12b" in text
    assert "custom instructions: no" in text
    config.instructions.write_text("Destaque o texto sigiloso.", encoding="utf-8")
    again = diagnose.report(data_dir, home)
    assert "custom instructions: yes" in again
    assert "sigilos" not in again  # in use, but never what it says
    assert "ollama: not reachable" in text
    for secret in ("sigilos", "nome-do-usuario", "texto"):
        assert secret not in text
    assert "t INFO opened ~" in text  # the home path, were it ever logged, is masked


# --- the user's guidance file -------------------------------------------------------


def test_shipped_guidance_file_explains_itself_and_changes_nothing() -> None:
    shipped = REPO_ROOT / "docs" / INSTRUCTIONS_NAME
    assert load_instructions(shipped) == ""  # comments only: the default prompt
    text = shipped.read_text(encoding="utf-8")
    assert str(INSTRUCTIONS_MAX_CHARS) in text
    assert "instructions_too_long" in text


def test_installer_adds_the_guidance_file_and_never_overwrites_it(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    install.main(["--data-dir", str(data_dir)])  # result depends on Ollama; files do not
    path = data_dir / INSTRUCTIONS_NAME
    assert path.read_bytes() == (REPO_ROOT / "docs" / INSTRUCTIONS_NAME).read_bytes()
    path.write_text("Escreva itens curtos.", encoding="utf-8")
    install.main(["--data-dir", str(data_dir)])
    assert path.read_text(encoding="utf-8") == "Escreva itens curtos."


# --- launchers ----------------------------------------------------------------------


def test_launchers_exist_and_only_hand_over_to_the_python_tools() -> None:
    installer = (REPO_ROOT / "Instalar.command").read_text(encoding="utf-8")
    assert installer.startswith("#!/bin/bash\n")
    assert "python -m scripts.setup_mac" in installer
    assert "--exclude .tessdata" in installer  # an update must not delete the OCR data
    assert "uv run --locked" in installer
    for name, module in [
        ("Trocar modelo.command", "scripts.set_model"),
        ("Diagnostico.command", "scripts.diagnose"),
    ]:
        launcher = (REPO_ROOT / "mac" / name).read_text(encoding="utf-8")
        assert launcher.startswith("#!/bin/bash\n")
        assert f"uv run --locked python -m {module}" in launcher


def test_installer_refuses_to_mirror_a_folder_that_is_not_the_program() -> None:
    installer = (REPO_ROOT / "Instalar.command").read_text(encoding="utf-8")
    assert installer.index("[ ! -f pyproject.toml ]") < installer.index("rsync ")


def test_downloaded_ollama_must_carry_the_ollama_developer_id(tmp_path: Path) -> None:
    command = setup_mac.signature_check(tmp_path / "Ollama.app")
    assert command[:4] == ("/usr/bin/codesign", "--verify", "--deep", "--strict")
    assert command[-1] == str(tmp_path / "Ollama.app")
    assert "anchor apple generic" in command[4]
    assert f'subject.OU] = "{setup_mac.OLLAMA_TEAM_ID}"' in command[4]
    source = (REPO_ROOT / "scripts" / "setup_mac.py").read_text(encoding="utf-8")
    body = source[source.index("def install_ollama_app") :]
    assert body.index("signature_check(") < body.index("/usr/bin/ditto")  # checked, then copied


def test_ollama_agent_serves_on_loopback_with_cloud_off_and_no_log_files(tmp_path: Path) -> None:
    plist = install.ollama_plist(tmp_path / "ollama")
    assert plist["ProgramArguments"] == [str(tmp_path / "ollama"), "serve"]
    assert plist["EnvironmentVariables"] == {
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_HOST": "127.0.0.1:11434",
    }
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert "StandardErrorPath" not in plist
    assert "StandardOutPath" not in plist
    assert plist["Label"] != install.LABEL
