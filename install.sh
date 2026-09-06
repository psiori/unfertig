#!/bin/sh
set -eu
BOARD_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf '\nunfertig — first-time setup\n'
if command -v uv >/dev/null 2>&1; then
  BOARD_UV=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
  BOARD_UV="$HOME/.local/bin/uv"
elif [ -x "$HOME/.cargo/bin/uv" ]; then
  BOARD_UV="$HOME/.cargo/bin/uv"
else
  printf 'Installing uv for your user account from https://astral.sh/uv/install.sh\n'
  BOARD_INSTALLER=$(mktemp)
  trap 'rm -f "$BOARD_INSTALLER"' EXIT HUP INT TERM
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$BOARD_INSTALLER"
  elif command -v wget >/dev/null 2>&1; then
    wget -q https://astral.sh/uv/install.sh -O "$BOARD_INSTALLER"
  else
    printf 'Please install curl or wget with your system package manager, or install uv from https://docs.astral.sh/uv/getting-started/installation/\n'
    exit 1
  fi
  sh "$BOARD_INSTALLER"
  BOARD_UV="$HOME/.local/bin/uv"
  if [ ! -x "$BOARD_UV" ]; then
    printf 'uv was installed to a custom location. Reopen your terminal and run this installer again.\n'
    exit 1
  fi
fi
printf '\nPreparing Python 3.12…\n'
"$BOARD_UV" python install 3.12
"$BOARD_UV" run --no-project --python 3.12 --script "$BOARD_DIR/server.py" --check
printf '\nReady! Open start.command on macOS, or run start.sh on Linux.\n'
