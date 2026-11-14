#!/bin/bash
# Double-click model switcher. All logic is in scripts/set_model.py.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/.brief-digest" || exit 1
uv run --locked python -m scripts.set_model "$@"
status=$?
if [ -t 0 ]; then
  echo
  read -r -p "Pressione Enter para fechar esta janela. "
fi
exit $status
