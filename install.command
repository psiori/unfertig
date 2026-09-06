#!/bin/sh
BOARD_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$BOARD_DIR/install.sh"
BOARD_EXIT=$?
if [ "$BOARD_EXIT" -ne 0 ]; then
  printf '\nSetup did not finish. Read the error above or open README.md.\n'
fi
printf '\nPress Return to close. '
read -r BOARD_REPLY
exit "$BOARD_EXIT"
