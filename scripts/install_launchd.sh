#!/bin/bash
# Install the launchd jobs (daemon + web) from the deploy templates using the
# project's virtual-environment interpreter. Run again after moving the project
# or recreating the virtual environment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXEC="$ROOT/.venv/bin/python3"
[ -x "$EXEC" ] || { echo "venv python not found at $EXEC" >&2; exit 1; }
LOGDIR="$HOME/Library/Logs/plutus"
AGENTDIR="$HOME/Library/LaunchAgents"
UID_="$(id -u)"
mkdir -p "$LOGDIR"
mkdir -p "$AGENTDIR"

bootstrap_job() {
  label="$1"
  dst="$2"
  domain="gui/$UID_"
  launchctl bootout "$domain/$label" 2>/dev/null || true
  if launchctl bootstrap "$domain" "$dst"; then
    launchctl print "$domain/$label" >/dev/null
    return 0
  fi
  echo "retrying launchctl bootstrap for $label" >&2
  launchctl bootout "$domain/$label" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "$domain" "$dst"
  launchctl print "$domain/$label" >/dev/null
}

for label in ai.plutus.daemon ai.plutus.web ai.plutus.updater; do
  if [ "$label" = "ai.plutus.updater" ] \
     && [ "${PLUTUS_SKIP_UPDATER_RELOAD:-0}" = "1" ]; then
    echo "preserved running $label"
    continue
  fi
  src="$ROOT/deploy/$label.plist"
  dst="$AGENTDIR/$label.plist"
  sed -e "s#__EXEC__#$EXEC#g" -e "s#__ROOT__#$ROOT#g" \
      -e "s#__LOGDIR__#$LOGDIR#g" "$src" > "$dst"
  plutil -lint "$dst" >/dev/null
  bootstrap_job "$label" "$dst"
  echo "installed $label -> $EXEC"
done
echo "done."
