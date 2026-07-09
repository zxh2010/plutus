#!/bin/bash
# Safely fast-forward an installed Plutus checkout and refresh integrations.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="$HOME/Library/Logs/plutus"
LOCKDIR="$LOGDIR/update.lock"
PENDING_FILE="$LOGDIR/update.pending"
VENV_PY="$ROOT/.venv/bin/python3"
EXPECTED_ORIGIN="${PLUTUS_EXPECTED_ORIGIN:-https://github.com/zxh2010/plutus.git}"
mkdir -p "$LOGDIR"

note() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

notify_wechat() {
  message="$1"
  [ -n "${HERMES_BIN:-}" ] && [ -x "$HERMES_BIN" ] && [ -n "${WEIXIN_TARGET:-}" ] || return 0
  "$HERMES_BIN" send --to "$WEIXIN_TARGET" "$message" >/dev/null 2>&1 || {
    note "warning: unable to send update notification"
    return 0
  }
}

finish() {
  rmdir "$LOCKDIR" 2>/dev/null || true
}

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  note "another update is already running; skipped"
  exit 0
fi
trap finish EXIT INT TERM

if [ -x "$VENV_PY" ] && [ -f "$ROOT/config.toml" ]; then
  SETTINGS="$(
    cd "$ROOT" && PYTHONPATH="$ROOT" "$VENV_PY" -c '
from plutus.config import load
notify = load().get("notify", {})
print(notify.get("hermes_bin", ""))
print(notify.get("weixin_target", ""))
' 2>/dev/null
  )" || SETTINGS=""
  HERMES_BIN="$(printf '%s\n' "$SETTINGS" | sed -n '1p')"
  WEIXIN_TARGET="$(printf '%s\n' "$SETTINGS" | sed -n '2p')"
fi

fail() {
  note "error: $*"
  notify_wechat "⚠️ Plutus 自动更新失败：$*。日志：~/Library/Logs/plutus/update.err.log"
  exit 1
}

refresh_installation() {
  old_revision="$1"
  new_revision="$2"
  note "refreshing installation for ${new_revision:0:7}"
  PLUTUS_WEIXIN_TARGET="${WEIXIN_TARGET:-}" \
  PLUTUS_SKIP_UPDATER_RELOAD=1 \
    bash "$ROOT/scripts/install.sh" || fail "installation refresh failed after update"
  rm -f "$PENDING_FILE"
  note "automatic update completed"
  notify_wechat "✅ Plutus 已自动更新：${old_revision:0:7} → ${new_revision:0:7}"
}

[ -d "$ROOT/.git" ] || fail "$ROOT is not a Git checkout"
[ "$(git -C "$ROOT" branch --show-current)" = "main" ] \
  || fail "installed checkout is not on main"
ACTUAL_ORIGIN="$(git -C "$ROOT" remote get-url origin 2>/dev/null)" \
  || fail "origin remote is missing"
[ "$ACTUAL_ORIGIN" = "$EXPECTED_ORIGIN" ] \
  || fail "origin does not match the trusted Plutus repository"

DIRTY="$(git -C "$ROOT" status --porcelain)"
[ -z "$DIRTY" ] || fail "local changes detected; update skipped"

note "checking origin/main"
git -C "$ROOT" fetch --quiet origin main || fail "git fetch failed"
LOCAL="$(git -C "$ROOT" rev-parse HEAD)" || fail "cannot read local revision"
REMOTE="$(git -C "$ROOT" rev-parse origin/main)" || fail "cannot read origin/main"

if [ "$LOCAL" = "$REMOTE" ]; then
  if [ -f "$PENDING_FILE" ]; then
    read -r PENDING_OLD PENDING_NEW < "$PENDING_FILE" \
      || fail "cannot read pending update state"
    refresh_installation "$PENDING_OLD" "$PENDING_NEW"
    exit 0
  fi
  note "already up to date at ${LOCAL:0:7}"
  exit 0
fi

git -C "$ROOT" merge-base --is-ancestor "$LOCAL" "$REMOTE" \
  || fail "origin/main cannot be fast-forwarded from local revision"
git -C "$ROOT" merge --ff-only --quiet "$REMOTE" || fail "fast-forward update failed"
printf '%s %s\n' "$LOCAL" "$REMOTE" > "$PENDING_FILE" \
  || fail "cannot persist pending update state"

note "updated ${LOCAL:0:7} -> ${REMOTE:0:7}"
refresh_installation "$LOCAL" "$REMOTE"
