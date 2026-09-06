#!/bin/sh
BOARD_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$BOARD_DIR/start.sh" "$@"
BOARD_EXIT=$?
if [ "$BOARD_EXIT" -ne 0 ]; then
  printf '\nCould not start. Read the message above or open README.md.\nPress Return to close. '
  read -r BOARD_REPLY
fi
exit "$BOARD_EXIT"
