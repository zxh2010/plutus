"""Load config.toml. Prefers stdlib tomllib (3.11+) / tomli, and falls back to
a tiny reader for the simple flat config this project uses so it also runs on
the system Python 3.9 with zero dependencies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# UI-managed mail credentials saved from the settings page override config.toml.
# The HTTP route still uses the legacy /api/gmail_auth path for compatibility.
_MAIL_AUTH_FILE = "secrets/mail_auth.json"


def _load_toml(path: str) -> dict:
    try:
        import tomllib  # py3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ModuleNotFoundError:
        pass
    try:
        import tomli  # backport
        with open(path, "rb") as fh:
            return tomli.load(fh)
    except ModuleNotFoundError:
        return _minimal_toml(Path(path).read_text(encoding="utf-8"))


def _minimal_toml(text: str) -> dict:
    """Subset reader: [section] headers and `key = value` lines where value is a
    quoted string, int, float, or bool. Handles inline `# comments`. Sufficient
    for this project's config only."""
    out: dict[str, Any] = {}
    section = out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            section = out.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        section[key.strip()] = _parse_value(val.strip())
    return out


def _parse_value(v: str) -> Any:
    if v.startswith('"'):
        return v[1: v.index('"', 1)]
    v = v.split("#", 1)[0].strip()
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return v.strip('"')


def load(path: str = "config.toml") -> dict:
    cfg = _load_toml(path)
    gmail = cfg.setdefault("gmail", {})
    mail = cfg.setdefault("mail", {})
    if not mail:
        # Backward compatibility: old config.toml only had [gmail]. Internally
        # expose it as [mail] so provider-aware code has one place to read.
        mail.update(gmail)
        mail["provider"] = "gmail"

    pw_file = gmail.get("app_password_file")
    if pw_file and os.path.exists(pw_file):
        gmail["app_password"] = Path(pw_file).read_text(encoding="utf-8").strip()
    mail_pw_file = mail.get("app_password_file")
    if mail_pw_file and os.path.exists(mail_pw_file):
        mail["app_password"] = Path(mail_pw_file).read_text(encoding="utf-8").strip()

    # UI-saved credentials win over config.toml / the password file.
    if os.path.exists(_MAIL_AUTH_FILE):
        try:
            ui = json.loads(Path(_MAIL_AUTH_FILE).read_text(encoding="utf-8"))
            ui_provider = (ui.get("provider") or "").strip()
            if ui_provider:
                mail["provider"] = ui_provider
                if ui.get("email"):
                    mail["email"] = ui["email"].strip()
                if ui.get("app_password"):
                    mail["app_password"] = ui["app_password"].strip()
                if ui_provider == "gmail":
                    if ui.get("email"):
                        gmail["email"] = ui["email"].strip()
                    if ui.get("app_password"):
                        gmail["app_password"] = ui["app_password"].strip()
            else:
                if ui.get("email"):
                    gmail["email"] = ui["email"].strip()
                if ui.get("app_password"):
                    gmail["app_password"] = ui["app_password"].strip()
                # Legacy UI files had no provider and therefore meant Gmail.
                # Do not let such files override an explicit domestic provider
                # such as QQ or 163; provider-aware UI files write provider.
                if (mail.get("provider") or "gmail") == "gmail":
                    if ui.get("email"):
                        mail["email"] = ui["email"].strip()
                    if ui.get("app_password"):
                        mail["app_password"] = ui["app_password"].strip()
        except (ValueError, OSError):
            pass
    return cfg
