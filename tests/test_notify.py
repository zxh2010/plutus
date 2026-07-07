"""Tests for Hermes-to-WeChat notification delivery checks.

Runnable with pytest or directly. subprocess.run is stubbed, so no real message
is sent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import notify  # noqa: E402


def _cfg(target: str = "weixin:user@example") -> dict:
    return {
        "notify": {
            "hermes_bin": "/fake/hermes",
            "weixin_target": target,
        }
    }


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _targets(*ids: str) -> str:
    return json.dumps({
        "platforms": {
            "weixin": [{"id": target_id} for target_id in ids],
        }
    })


def test_check_requires_complete_config():
    result = notify.check_wechat({"notify": {}})

    assert result["ok"] is False
    assert "Hermes" in result["error"]


def test_check_rejects_target_missing_from_hermes():
    original_exists = notify.os.path.isfile
    original_access = notify.os.access
    original_run = notify.subprocess.run
    calls = []
    notify.os.path.isfile = lambda path: True
    notify.os.access = lambda path, mode: True
    notify.subprocess.run = lambda args, **kwargs: (
        calls.append(args) or _result(stdout=_targets("another@example"))
    )
    try:
        result = notify.check_wechat(_cfg())
    finally:
        notify.os.path.isfile = original_exists
        notify.os.access = original_access
        notify.subprocess.run = original_run

    assert result["ok"] is False
    assert "目标" in result["error"]
    assert len(calls) == 1


def test_check_lists_target_then_sends_probe():
    original_exists = notify.os.path.isfile
    original_access = notify.os.access
    original_run = notify.subprocess.run
    calls = []
    replies = [
        _result(stdout=_targets("user@example")),
        _result(stdout='{"ok": true}'),
    ]
    notify.os.path.isfile = lambda path: True
    notify.os.access = lambda path, mode: True
    notify.subprocess.run = lambda args, **kwargs: (
        calls.append(args) or replies.pop(0)
    )
    try:
        result = notify.check_wechat(_cfg())
    finally:
        notify.os.path.isfile = original_exists
        notify.os.access = original_access
        notify.subprocess.run = original_run

    assert result["ok"] is True
    assert result["registered"] is True
    assert calls[0][1:] == ["send", "--list", "weixin", "--json"]
    assert calls[1][1:4] == ["send", "--to", "weixin:user@example"]
    assert "通道测试" in calls[1][4]


def test_check_surfaces_delivery_failure():
    original_exists = notify.os.path.isfile
    original_access = notify.os.access
    original_run = notify.subprocess.run
    replies = [
        _result(stdout=_targets("user@example")),
        _result(returncode=1, stderr="backend unavailable"),
    ]
    notify.os.path.isfile = lambda path: True
    notify.os.access = lambda path, mode: True
    notify.subprocess.run = lambda args, **kwargs: replies.pop(0)
    try:
        result = notify.check_wechat(_cfg())
    finally:
        notify.os.path.isfile = original_exists
        notify.os.access = original_access
        notify.subprocess.run = original_run

    assert result["ok"] is False
    assert "backend unavailable" in result["error"]


def test_check_handles_timeout():
    original_exists = notify.os.path.isfile
    original_access = notify.os.access
    original_run = notify.subprocess.run
    notify.os.path.isfile = lambda path: True
    notify.os.access = lambda path, mode: True
    notify.subprocess.run = lambda args, **kwargs: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(args, 15)
    )
    try:
        result = notify.check_wechat(_cfg())
    finally:
        notify.os.path.isfile = original_exists
        notify.os.access = original_access
        notify.subprocess.run = original_run

    assert result["ok"] is False
    assert "超时" in result["error"]


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"ALL {len(tests)} NOTIFY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
