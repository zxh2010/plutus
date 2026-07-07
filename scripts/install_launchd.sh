#!/bin/bash
# Install the launchd jobs (daemon + web) from the deploy templates using the
# project's virtual-environment interpreter. Run again after moving the project
# or recreating the virtual environment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXEC="$ROOT/.venv/bin/python3"
[ -x "$EXEC" ] || { echo "venv python not found at $EXEC" >&2; exit 1; }
LOGDIR="$HOME/Library/Logs/plutus"
UID_="$(id -u)"
mkdir -p "$LOGDIR"

for label in ai.plutus.daemon ai.plutus.web; do
  src="$ROOT/deploy/$label.plist"
  dst="$HOME/Library/LaunchAgents/$label.plist"
  sed -e "s#__EXEC__#$EXEC#g" -e "s#__ROOT__#$ROOT#g" \
      -e "s#__LOGDIR__#$LOGDIR#g" "$src" > "$dst"
  launchctl bootout "gui/$UID_/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_" "$dst"
  echo "installed $label -> $EXEC"
done
echo "done."
