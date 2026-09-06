#!/bin/sh
# macOS/Linux entry point; works from any current directory, including paths with spaces.
set -eu
BOARD_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v uv >/dev/null 2>&1; then
  BOARD_UV=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
  BOARD_UV="$HOME/.local/bin/uv"
elif [ -x "$HOME/.cargo/bin/uv" ]; then
  BOARD_UV="$HOME/.cargo/bin/uv"
else
  printf '\nFirst-time setup is needed.\nRun: sh "%s/install.sh"\nThen start again. See README.md for help.\n' "$BOARD_DIR"
  exit 1
fi
printf '\nOpening unfertig…\nThe first start may download Python. Future starts work offline.\n'
exec "$BOARD_UV" run --no-project --python 3.12 --script "$BOARD_DIR/server.py" "$@"
