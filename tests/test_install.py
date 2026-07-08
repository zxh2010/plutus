"""Isolated tests for scripts/install.sh.

The repository is copied into a temporary HOME and all external integrations
are skipped. No real Hermes, launchd service, configuration, or database is
modified.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _copy_repo(home: Path) -> Path:
    target = home / ".plutus"
    shutil.copytree(
        ROOT,
        target,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", "*.pyc", "config.toml",
            "plutus.db", "plutus.db-*", "secrets", "fixtures",
        ),
    )
    return target


def _fake_hermes(home: Path) -> Path:
    path = home / "hermes"
    path.write_text(
        """#!/bin/bash
set -e
if [ "$1 $2" = "send --list" ]; then
  python3 - <<'PY'
import json, os
ids = [value for value in os.environ.get("FAKE_TARGETS", "user@example").split(",") if value]
print(json.dumps({"platforms": {"weixin": [{"id": value} for value in ids]}}))
PY
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run(root: Path, home: Path, *args: str, targets: str = "user@example"):
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "PLUTUS_HERMES_BIN": str(home / "hermes"),
        "PLUTUS_PYTHON_BIN": sys.executable,
        "FAKE_TARGETS": targets,
    })
    return subprocess.run(
        ["bash", str(root / "scripts" / "install.sh"), *args],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = _copy_repo(home)
        _fake_hermes(home)

        result = _run(root, home, "--dry-run")

        assert result.returncode == 0, result.stderr
        assert not (root / ".venv").exists()
        assert not (root / "config.toml").exists()
        assert not (root / "plutus.db").exists()


def test_dry_run_prefers_existing_virtualenv_python():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = _copy_repo(home)
        _fake_hermes(home)
        venv_python = root / ".venv" / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "PLUTUS_HERMES_BIN": str(home / "hermes"),
            "FAKE_TARGETS": "user@example",
        })
        env.pop("PLUTUS_PYTHON_BIN", None)

        result = subprocess.run(
            ["bash", str(root / "scripts" / "install.sh"), "--dry-run"],
            cwd=home,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr


def test_local_install_is_idempotent_and_preserves_data():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = _copy_repo(home)
        _fake_hermes(home)

        first = _run(root, home, "--skip-integrations")
        assert first.returncode == 0, first.stderr
        config = root / "config.toml"
        database = root / "plutus.db"
        assert config.exists() and database.exists()
        config.write_text(config.read_text(encoding="utf-8") + "\n# sentinel\n",
                          encoding="utf-8")
        config_before = config.read_bytes()
        database_before = database.read_bytes()

        second = _run(root, home, "--skip-integrations")

        assert second.returncode == 0, second.stderr
        assert config.read_bytes() == config_before
        assert database.read_bytes() == database_before


def test_multiple_targets_require_explicit_selection():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = _copy_repo(home)
        _fake_hermes(home)

        result = _run(root, home, "--skip-integrations",
                      targets="first@example,second@example")

        assert result.returncode != 0
        assert "multiple WeChat targets" in result.stderr
        assert not (root / "config.toml").exists()


def test_missing_hermes_fails_before_writes():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        root = _copy_repo(home)
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "PLUTUS_HERMES_BIN": str(home / "missing-hermes"),
            "PLUTUS_PYTHON_BIN": sys.executable,
        })

        result = subprocess.run(
            ["bash", str(root / "scripts" / "install.sh"),
             "--skip-integrations"],
            cwd=home,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode != 0
        assert "Hermes was not found" in result.stderr
        assert not (root / "config.toml").exists()


def test_documented_default_directory_and_url():
    guide = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")

    assert "~/.plutus" in guide
    assert (
        "https://raw.githubusercontent.com/zxh2010/plutus/main/docs/install.md"
        in guide
    )


def test_launchd_installer_retries_transient_bootstrap_failure():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        bin_dir = Path(tmp) / "bin"
        home.mkdir()
        bin_dir.mkdir()
        root = _copy_repo(home)
        venv_python = root / ".venv" / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)

        calls = Path(tmp) / "launchctl.calls"
        state = Path(tmp) / "launchctl.state"
        (bin_dir / "id").write_text("#!/bin/bash\necho 501\n", encoding="utf-8")
        (bin_dir / "plutil").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        (bin_dir / "launchctl").write_text(f"""#!/bin/bash
set -e
echo "$@" >> "{calls}"
if [ "$1" = "bootout" ]; then
  exit 0
fi
if [ "$1" = "bootstrap" ]; then
  count=0
  [ -f "{state}" ] && count="$(cat "{state}")"
  count=$((count + 1))
  echo "$count" > "{state}"
  if [ "$count" -eq 1 ]; then
    echo "Input/output error" >&2
    exit 5
  fi
  exit 0
fi
if [ "$1" = "print" ]; then
  exit 0
fi
exit 0
""", encoding="utf-8")
        for tool in ("id", "plutil", "launchctl"):
            (bin_dir / tool).chmod(0o755)
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        })

        result = subprocess.run(
            ["bash", str(root / "scripts" / "install_launchd.sh")],
            cwd=home,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        call_text = calls.read_text(encoding="utf-8")
        assert call_text.count("bootstrap gui/501") >= 2
        assert "retrying launchctl bootstrap" in result.stderr


def _main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"ALL {len(tests)} INSTALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
