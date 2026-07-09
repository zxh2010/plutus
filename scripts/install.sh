#!/bin/bash
# Install Plutus from a clone located at ~/.plutus.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_ROOT="$HOME/.plutus"
DRY_RUN=0
SKIP_INTEGRATIONS=0
cd "$ROOT"

usage() {
  echo "usage: scripts/install.sh [--dry-run] [--skip-integrations]"
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --skip-integrations) SKIP_INTEGRATIONS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

fail() {
  echo "error: $*" >&2
  exit 1
}

note() {
  echo "==> $*"
}

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'dry-run:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

find_hermes() {
  if [ -n "${PLUTUS_HERMES_BIN:-}" ]; then
    printf '%s\n' "$PLUTUS_HERMES_BIN"
  elif command -v hermes >/dev/null 2>&1; then
    command -v hermes
  elif [ -x "$HOME/.local/bin/hermes" ]; then
    printf '%s\n' "$HOME/.local/bin/hermes"
  else
    return 1
  fi
}

python_is_supported() {
  [ -x "$1" ] && "$1" -c \
    'import sys; raise SystemExit(sys.version_info < (3, 10))' \
    >/dev/null 2>&1
}

find_python() {
  if [ -n "${PLUTUS_PYTHON_BIN:-}" ]; then
    python_is_supported "$PLUTUS_PYTHON_BIN" \
      && printf '%s\n' "$PLUTUS_PYTHON_BIN"
    return
  fi
  if python_is_supported "$ROOT/.venv/bin/python3"; then
    printf '%s\n' "$ROOT/.venv/bin/python3"
    return
  fi
  for name in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    candidate="$(command -v "$name" 2>/dev/null || true)"
    if [ -n "$candidate" ] && python_is_supported "$candidate"; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if python_is_supported "$candidate"; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  return 1
}

[ "$(uname -s)" = "Darwin" ] || fail "Plutus currently supports macOS only"
[ "$ROOT" = "$DEFAULT_ROOT" ] || note "non-default checkout: $ROOT (default: $DEFAULT_ROOT)"

PYTHON_BIN="$(find_python || true)"
[ -n "$PYTHON_BIN" ] || fail "Python 3.10 or newer was not found"

HERMES="$(find_hermes || true)"
[ -n "$HERMES" ] && [ -x "$HERMES" ] || fail "Hermes was not found"

note "checking Hermes WeChat targets"
TARGETS_JSON="$("$HERMES" send --list weixin --json)" \
  || fail "unable to list Hermes WeChat targets"
TARGET_IDS="$(printf '%s' "$TARGETS_JSON" | "$PYTHON_BIN" -c '
import json, sys
data = json.load(sys.stdin)
for item in data.get("platforms", {}).get("weixin", []):
    target = item.get("id") if isinstance(item, dict) else None
    if target:
        print(target)
')" || fail "Hermes returned invalid target JSON"
TARGET_COUNT="$(printf '%s\n' "$TARGET_IDS" | sed '/^$/d' | wc -l | tr -d ' ')"

if [ -n "${PLUTUS_WEIXIN_TARGET:-}" ]; then
  WEIXIN_TARGET="$PLUTUS_WEIXIN_TARGET"
  TARGET_ID="${WEIXIN_TARGET#weixin:}"
  printf '%s\n' "$TARGET_IDS" | grep -Fxq "$TARGET_ID" \
    || fail "PLUTUS_WEIXIN_TARGET is not registered in Hermes"
elif [ "$TARGET_COUNT" -eq 1 ]; then
  WEIXIN_TARGET="weixin:$TARGET_IDS"
elif [ "$TARGET_COUNT" -eq 0 ]; then
  fail "Hermes has no WeChat target; configure WeChat first"
else
  echo "Hermes has multiple WeChat targets:" >&2
  printf '  weixin:%s\n' $TARGET_IDS >&2
  fail "set PLUTUS_WEIXIN_TARGET to the target selected by the user"
fi

VENV_PY="$ROOT/.venv/bin/python3"
if [ ! -x "$VENV_PY" ]; then
  note "creating virtual environment"
  run "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  VENV_PY="$PYTHON_BIN"
fi

if [ ! -f "$ROOT/config.toml" ]; then
  note "creating local-only configuration"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: create $ROOT/config.toml"
  else
    mkdir -p "$ROOT/secrets"
    chmod 700 "$ROOT/secrets"
    cat > "$ROOT/config.toml" <<EOF
[mail]
provider = "qq"
email = ""
app_password_file = "secrets/mail_auth_code.txt"
query = "from:cmbchina.com newer_than:400d"

[proxy]
host = ""
port = 8118

[db]
path = "plutus.db"

[poll]
interval_seconds = 90

[web]
host = "127.0.0.1"
port = 8973

[stats]
billing_start_day = 1

[notify]
hermes_bin = "$HERMES"
weixin_target = "$WEIXIN_TARGET"
EOF
    chmod 600 "$ROOT/config.toml"
  fi
else
  note "preserving existing config.toml"
fi

if [ ! -f "$ROOT/plutus.db" ]; then
  note "initializing database"
  run "$VENV_PY" "$ROOT/scripts/init_db.py"
else
  note "preserving existing plutus.db"
fi

if [ "$DRY_RUN" -eq 0 ]; then
  "$VENV_PY" -m compileall -q "$ROOT/plutus"
  "$VENV_PY" -c '
import sqlite3
conn = sqlite3.connect("file:plutus.db?mode=ro", uri=True)
result = conn.execute("PRAGMA integrity_check").fetchone()[0]
conn.close()
raise SystemExit(0 if result == "ok" else result)
'
fi

if [ "$SKIP_INTEGRATIONS" -eq 1 ]; then
  note "skipping Hermes MCP and launchd integrations"
  note "local preparation complete"
  exit 0
fi

note "registering Plutus MCP with Hermes"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: hermes mcp add plutus"
else
  printf 'y\n' | "$HERMES" mcp add plutus \
    --command "$VENV_PY" \
    --env "PYTHONPATH=$ROOT" \
    --args -m plutus.mcp_server
  "$HERMES" gateway restart
fi

note "installing launchd services"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: bash $ROOT/scripts/install_launchd.sh"
else
  bash "$ROOT/scripts/install_launchd.sh"
fi

if [ "$DRY_RUN" -eq 0 ]; then
  note "waiting for web service"
  healthy=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if "$VENV_PY" -c '
import http.client
conn = http.client.HTTPConnection("127.0.0.1", 8973, timeout=2)
conn.request("GET", "/api/config")
response = conn.getresponse()
response.read()
conn.close()
raise SystemExit(0 if response.status == 200 else 1)
'; then
      healthy=1
      break
    fi
    sleep 1
  done
  [ "$healthy" -eq 1 ] || fail "web service did not become healthy"

  "$HERMES" mcp test plutus
  launchctl print "gui/$(id -u)/ai.plutus.web" >/dev/null
  launchctl print "gui/$(id -u)/ai.plutus.daemon" >/dev/null
  launchctl print "gui/$(id -u)/ai.plutus.updater" >/dev/null
fi

note "installation complete"
echo "Open http://127.0.0.1:8973/#config to authorize or check your mailbox."
