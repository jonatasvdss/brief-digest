#!/bin/bash
# Double-click diagnostic report. All logic is in scripts/diagnose.py.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/.brief-digest" || exit 1
uv run --locked python -m scripts.diagnose "$@"
status=$?
if [ -t 0 ]; then
  echo
  read -r -p "Pressione Enter para fechar esta janela. "
fi
exit $status
