"""One-step setup for the end user's Mac. Run by `Instalar.command`.

This is the explicit setup action of hard rule 5: after one confirmation it downloads
what is missing (Ollama, the OCR data, the model), turns Ollama's cloud features
off, installs the watcher (scripts/install.py), and proves the install with a
synthetic filing that stays in the output folder as an example. The app itself
never downloads anything.

Text shown here is in Portuguese: it is read by the end user.

Usage: uv run --locked python -m scripts.setup_mac [--yes] [--model NAME] [--data-dir DIR]
"""

from __future__ import annotations

import argparse
import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from scripts import fetch_tessdata, install

from brief_digest.config import AppConfig, ConfigError, load_config
from brief_digest.ollama import check_local_model_name
from brief_digest.summarize import BANNER, SUMMARY_MD

OLLAMA_DMG = "https://ollama.com/download/Ollama.dmg"
OLLAMA_TEAM_ID = "3MU9H2V9Y9"  # Apple Developer ID team that signs Ollama.app
OLLAMA_HOST, OLLAMA_PORT = "127.0.0.1", 11434
EXAMPLE_PDF = "EXEMPLO - peça fictícia.pdf"
EXAMPLE_DOC = "appeal_signed_scan"  # has scanned pages, so the self-test covers OCR
SELF_TEST_TIMEOUT_S = 900


def say(text: str = "") -> None:
    print(text, flush=True)


def run(*command: str) -> int:
    try:
        return subprocess.run(command, check=False, capture_output=True).returncode  # noqa: S603
    except OSError:
        return 127


# --- Ollama on loopback -------------------------------------------------------------


def api(path: str, body: dict[str, object] | None = None, timeout: float = 10) -> dict | None:
    """One JSON request to the local Ollama server; None when it cannot be reached."""
    conn = http.client.HTTPConnection(OLLAMA_HOST, OLLAMA_PORT, timeout=timeout)
    try:
        payload = None if body is None else json.dumps(body)
        conn.request("GET" if body is None else "POST", path, body=payload)
        response = conn.getresponse()
        data = json.loads(response.read())
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        conn.close()
    return data if response.status == 200 and isinstance(data, dict) else None


def server_up() -> bool:
    return api("/api/version", timeout=3) is not None


def wait_for(condition: bool, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while server_up() != condition:
        if time.monotonic() > deadline:
            return False
        time.sleep(1)
    return True


def local_models() -> list[str]:
    """Names of installed models that run on this machine (cloud-backed ones left out)."""
    models = (api("/api/tags") or {}).get("models") or []
    return [
        m["name"]
        for m in models
        if isinstance(m, dict)
        and isinstance(m.get("name"), str)
        and m.get("size")
        and not m.get("remote_host")
        and not m.get("remote_model")
        and "cloud" not in m["name"].casefold()
    ]


def pull(model: str) -> bool:
    """Ask the local Ollama server to download a model, showing progress."""
    conn = http.client.HTTPConnection(OLLAMA_HOST, OLLAMA_PORT, timeout=600)
    shown = -1
    try:
        conn.request("POST", "/api/pull", body=json.dumps({"model": model, "stream": True}))
        response = conn.getresponse()
        if response.status != 200:
            return False
        for line in response:
            event = json.loads(line)
            if event.get("error"):
                return False
            total, completed = event.get("total"), event.get("completed")
            if total and completed is not None:
                percent = int(100 * completed / total)
                if percent >= shown + 5:
                    shown = percent
                    say(f"    {percent}% de {total / 1e9:.1f} GB")
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        conn.close()
    return model in local_models()


def disable_cloud(home: Path) -> bool:
    """Set disable_ollama_cloud in Ollama's settings, keeping its other settings.
    Returns whether the file changed (a running server must then be restarted)."""
    if install.cloud_disabled(home):
        return False
    path = home / ".ollama" / "server.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = None
    if not isinstance(settings, dict):
        if path.exists():  # unreadable: keep a copy before replacing it
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        settings = {}
    settings["disable_ollama_cloud"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return True


def ollama_app(home: Path) -> Path | None:
    for folder in (Path("/Applications"), home / "Applications"):
        if (folder / "Ollama.app").is_dir():
            return folder / "Ollama.app"
    return None


def signature_check(app: Path) -> tuple[str, ...]:
    """Command that succeeds only for an intact app signed with Ollama's Apple
    Developer ID. The download is the current release, so it cannot be pinned by
    checksum; nothing fetched here passes through Gatekeeper either."""
    requirement = (
        "anchor apple generic"
        " and certificate 1[field.1.2.840.113635.100.6.2.6] exists"
        " and certificate leaf[field.1.2.840.113635.100.6.1.13] exists"
        f' and certificate leaf[subject.OU] = "{OLLAMA_TEAM_ID}"'
    )
    return ("/usr/bin/codesign", "--verify", "--deep", "--strict", f"-R={requirement}", str(app))


def install_ollama_app(home: Path) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        dmg, mount = Path(tmp) / "Ollama.dmg", Path(tmp) / "mount"
        with urllib.request.urlopen(OLLAMA_DMG, timeout=120) as response, dmg.open("wb") as out:
            shutil.copyfileobj(response, out)
        if run(
            "/usr/bin/hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            str(mount),
            str(dmg),
        ):
            return False
        try:
            if run(*signature_check(mount / "Ollama.app")):
                say("    O arquivo baixado não tem a assinatura do Ollama; não foi instalado.")
                return False
            for folder in (Path("/Applications"), home / "Applications"):
                folder.mkdir(exist_ok=True)
                if (
                    run("/usr/bin/ditto", str(mount / "Ollama.app"), str(folder / "Ollama.app"))
                    == 0
                ):
                    return True
            return False
        finally:
            run("/usr/bin/hdiutil", "detach", str(mount))


def start_ollama(home: Path) -> bool:
    """Run the Ollama server as a launchd agent of our own (see install.ollama_plist),
    using the program inside the Ollama app. Starts at login from then on."""
    app = ollama_app(home)
    if app is None:
        return False
    binary = app / "Contents" / "Resources" / "ollama"
    return (
        binary.is_file()
        and install.load_agent(home, install.ollama_plist(binary))
        and wait_for(True, 120)
    )


def restart_ollama(home: Path) -> bool:
    """Restart a running server so it reads the changed settings file."""
    if install.agent_loaded(install.OLLAMA_LABEL):
        install.stop_agent(install.OLLAMA_LABEL)
    else:  # started by the Ollama desktop app
        run("/usr/bin/killall", "Ollama")
        run("/usr/bin/killall", "ollama")
    return wait_for(False, 30) and start_ollama(home)


def ensure_ollama(home: Path) -> str | None:
    """Ollama installed, cloud features off, server running. Returns a problem or None."""
    changed = disable_cloud(home)
    if ollama_app(home) is None and not server_up():
        say("    Baixando o Ollama (cerca de 230 MB)...")
        if not install_ollama_app(home):
            return "não foi possível instalar o Ollama"
    if changed and server_up():
        if not restart_ollama(home):
            return "não foi possível reiniciar o Ollama"
    elif not server_up() and not start_ollama(home):
        return "o Ollama não iniciou"
    return None


# --- self-test ----------------------------------------------------------------------


def self_test(config: AppConfig, timeout_s: float = SELF_TEST_TIMEOUT_S) -> bool:
    """Drop a synthetic filing in the inbox and wait for the watcher to summarize it."""
    from tests.fixtures.synth import documents, write_pdf  # needs the dev dependencies

    before = {p.name for p in config.output.iterdir()}
    hidden = config.inbox / ".exemplo.pdf"  # dot files are ignored until renamed
    write_pdf(documents()[EXAMPLE_DOC], hidden)
    hidden.replace(config.inbox / EXAMPLE_PDF)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for folder in config.output.iterdir():
            summary = folder / SUMMARY_MD
            if folder.name not in before and summary.is_file():
                text = summary.read_text(encoding="utf-8")
                return text.startswith(BANNER) and "\n## " in text
        time.sleep(2)
    return False


# --- main ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    home = Path.home()
    parser = argparse.ArgumentParser(description="Instala o brief-digest neste Mac.")
    parser.add_argument("--data-dir", type=Path, default=install.default_data_dir(home))
    parser.add_argument("--model", help="modelo do Ollama (padrão: o já configurado)")
    parser.add_argument("--yes", action="store_true", help="não pedir confirmação")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        say("Este instalador é só para macOS. Em outros sistemas: scripts/install.py")
        return 1

    data_dir = args.data_dir.expanduser().resolve()
    config_path = data_dir / install.CONFIG_NAME
    model = args.model
    if config_path.is_file():
        try:
            current = load_config(config_path).model
        except ConfigError as exc:
            say(f"O arquivo de configuração tem um erro: {exc}")
            return 1
        if model and model != current:
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(install.with_model(text, model), encoding="utf-8")
        model = model or current
    model = model or AppConfig.model
    try:
        check_local_model_name(model)
    except ValueError:
        say("O modelo precisa rodar neste computador; modelos de nuvem não são aceitos.")
        return 1

    say("INSTALAÇÃO DO BRIEF-DIGEST")
    say()
    say("Este instalador vai, se ainda não estiverem neste Mac:")
    say("  - baixar e instalar o Ollama (cerca de 230 MB);")
    say("  - baixar os dados de leitura de páginas digitalizadas (poucos MB);")
    say(f"  - baixar o modelo {model} (vários GB; pode levar bastante tempo).")
    say("Também desliga os recursos de nuvem do Ollama, para que nada saia deste Mac,")
    say(f"e cria a pasta {data_dir}.")
    say()
    if not args.yes and input("Continuar? [S/n] ").strip().casefold() in {"n", "nao", "não"}:
        say("Nada foi instalado.")
        return 1

    say("[1/5] Ollama")
    problem = ensure_ollama(home)
    if problem:
        say(f"ERRO: {problem}.")
        return 1
    say("[2/5] Dados de leitura de páginas digitalizadas")
    if fetch_tessdata.main([]) != 0:
        say("ERRO: não foi possível baixar os dados. Verifique a internet e tente de novo.")
        return 1
    say(f"[3/5] Modelo {model}")
    if model not in local_models():
        say("    Baixando o modelo. Não feche esta janela.")
        if not pull(model):
            say("ERRO: não foi possível baixar o modelo. Verifique a internet e tente de novo.")
            return 1
    say("[4/5] Programa")
    if install.main(["--data-dir", str(data_dir), "--model", model]) != 0:
        say("ERRO: a instalação não foi concluída (detalhes acima, em inglês).")
        return 1
    for launcher in sorted((install.REPO / "mac").glob("*.command")):
        target = data_dir / launcher.name  # so the user finds them next to the documents
        shutil.copyfile(launcher, target)
        target.chmod(0o755)
    say("[5/5] Teste com uma peça fictícia (pode levar alguns minutos)")
    if not self_test(load_config(config_path)):
        say("ERRO: o teste não gerou um resumo. Na pasta do programa, dê dois cliques em")
        say("'Diagnostico.command' e envie o arquivo gerado a quem instalou o programa.")
        return 1

    say()
    say("PRONTO. O programa está instalado e funcionando.")
    say(f"  Coloque os PDFs em:   {data_dir / 'Entrada'}")
    say(f"  Os resumos saem em:   {data_dir / 'Resumos'}")
    say("  Há um resumo de exemplo (peça fictícia) na pasta Resumos.")
    say("  Leia o arquivo LEIA-ME.txt, que está na mesma pasta.")
    say("  Para trocar o modelo: dois cliques em 'Trocar modelo.command', na mesma pasta.")
    run("/usr/bin/open", str(data_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
