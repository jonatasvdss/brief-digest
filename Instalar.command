#!/bin/bash
# Double-click installer for macOS. Copies the program to ~/.brief-digest (macOS does
# not let a background program run from Downloads, Desktop or Documents), makes sure
# uv is available, then hands over to scripts/setup_mac.py, which does everything else.
APP="$HOME/.brief-digest"
UV_VERSION="0.13.1"

finish() {
  if [ -t 0 ]; then
    echo
    read -r -p "Pressione Enter para fechar esta janela. "
  fi
  exit "$1"
}

cd "$(dirname "$0")" || finish 1
if [ "$(pwd -P)" != "$APP" ]; then
  echo "Copiando o programa para $APP ..."
  mkdir -p "$APP" || finish 1
  rsync -a --delete --exclude .venv --exclude .tessdata --exclude .git ./ "$APP/" || finish 1
  cd "$APP" || finish 1
fi

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Instalando o gerenciador de pacotes uv ..."
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERRO: não foi possível instalar o uv. Verifique a internet e tente de novo."
  finish 1
fi

uv run --locked python -m scripts.setup_mac "$@"
finish $?
