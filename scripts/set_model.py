"""Switch the Ollama model the watcher uses. Run by `Trocar modelo.command`.

Lists the two evaluated models plus any other model installed locally, downloads the
chosen one if needed (an explicit, confirmed setup action: hard rule 5), writes it
to config.toml and restarts the watcher. Cloud models are never listed or accepted.
Text shown here is in Portuguese: it is read by the end user.

Usage: uv run --locked python -m scripts.set_model [--model NAME] [--data-dir DIR]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from scripts import install
from scripts.setup_mac import local_models, pull, say, server_up, start_ollama

from brief_digest.config import ConfigError, load_config
from brief_digest.ollama import OllamaSummarizer, check_local_model_name
from brief_digest.summarize import SummarizerError

# Compared on synthetic filings (scripts/eval_models.py); both fit the target machine.
EVALUATED = ("gemma4:12b", "qwen3.5:9b")


def choices(installed: list[str]) -> list[str]:
    """Evaluated models first, then the other locally installed ones."""
    return [*EVALUATED, *sorted(m for m in installed if m not in EVALUATED)]


def ask(options: list[str], installed: list[str], current: str) -> str | None:
    say(f"Modelo em uso: {current}")
    say()
    for number, name in enumerate(options, 1):
        notes = ["instalado" if name in installed else "será baixado (vários GB)"]
        if name in EVALUATED:
            notes.append("avaliado")
        if name == current:
            notes.append("EM USO")
        say(f"  {number}. {name}  ({', '.join(notes)})")
    say()
    say("Só os modelos marcados como 'avaliado' foram testados com este programa.")
    answer = input("Digite o número do modelo (ou Enter para não mudar): ").strip()
    if answer.isdecimal() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    return None


def main(argv: list[str]) -> int:
    home = Path.home()
    parser = argparse.ArgumentParser(description="Troca o modelo usado pelo brief-digest.")
    parser.add_argument("--data-dir", type=Path, default=install.default_data_dir(home))
    parser.add_argument("--model", help="escolhe o modelo sem perguntar")
    args = parser.parse_args(argv)
    config_path = args.data_dir.expanduser().resolve() / install.CONFIG_NAME
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        say(f"Não foi possível ler a configuração ({exc}). O programa está instalado?")
        return 1
    if not server_up() and not (sys.platform == "darwin" and start_ollama(home)):
        say("O Ollama não está em execução. Abra o Ollama e tente de novo.")
        return 1

    installed = local_models()
    model = args.model or ask(choices(installed), installed, config.model)
    if model is None or model == config.model:
        say("Nada foi alterado.")
        return 0
    try:
        check_local_model_name(model)
    except ValueError:
        say("O modelo precisa rodar neste computador; modelos de nuvem não são aceitos.")
        return 1
    if model not in installed:
        say(f"Baixando o modelo {model}. Não feche esta janela.")
        if not pull(model):
            say("ERRO: não foi possível baixar o modelo. Nada foi alterado.")
            return 1
    try:
        OllamaSummarizer(replace(config.ollama_config(), model=model)).ensure_local_model()
    except SummarizerError as exc:
        say(f"ERRO: o modelo não pode ser usado ({exc.failure_type}). Nada foi alterado.")
        return 1

    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(install.with_model(text, model), encoding="utf-8", newline="\n")
    if sys.platform == "darwin" and not install.start_agent(home, config_path):
        say("ERRO: o modelo foi gravado, mas o programa não reiniciou. Rode o instalador.")
        return 1
    say()
    say(f"PRONTO. Modelo em uso: {model}")
    say("Vale para os próximos PDFs. Para refazer um resumo com o novo modelo, copie o")
    say("arquivo original.pdf da pasta da peça de volta para a pasta Entrada.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
