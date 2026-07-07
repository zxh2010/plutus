"""Tests for the AI account-alert email parser.

The AI call is stubbed, so this runs without Gmail or the hermes agent.

Runnable with pytest or directly.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import account_alert_ai, classifier  # noqa: E402

RECEIVED = datetime(2026, 6, 26, 14, 20)


def _stub_ai(reply) -> None:
    """Make the AI return a fixed reply (dict -> JSON, or raw str) for the next call."""
    text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
    classifier._call_hermes = lambda *a, **k: text  # type: ignore


def test_parse_expense():
    _stub_ai({"type": "expense", "card_last4": "1234", "amount": 800.0,
              "time": "2026-06-26 09:57", "counterparty": "示例商户", "channel": "支付宝"})
    rec = account_alert_ai.parse("...", RECEIVED)
    assert rec["type"] == "expense" and rec["amount"] == 800.0
    assert rec["counterparty"] == "示例商户" and rec["channel"] == "支付宝"


def test_parse_transfer_out():
    _stub_ai({"type": "transfer_out", "card_last4": "1234", "amount": 1100.0,
              "time": "2026-06-26 14:20", "counterparty": "示例收款人"})
    rec = account_alert_ai.parse("...", RECEIVED)
    assert rec["type"] == "transfer_out" and rec["counterparty"] == "示例收款人"


def test_parse_income():
    _stub_ai({"type": "income", "card_last4": "1234", "amount": 1322.0,
              "time": "2026-06-25 10:16", "counterparty": "示例公司", "kind": "报销"})
    rec = account_alert_ai.parse("...", RECEIVED)
    assert rec["type"] == "income" and rec["kind"] == "报销" and rec["counterparty"] == "示例公司"


def test_parse_ignore_returns_none():
    _stub_ai({"type": "ignore"})
    assert account_alert_ai.parse("信用卡还款...", RECEIVED) is None


def test_parse_time_fallback():
    _stub_ai({"type": "expense", "card_last4": "1234", "amount": 50})
    rec = account_alert_ai.parse("...", RECEIVED)
    assert rec["txn_time"] == "2026-06-26 14:20"  # falls back to received


def test_prompt_describes_account_alert_email():
    seen = {}

    def fake_call(prompt, *args, **kwargs):
        seen["prompt"] = prompt
        return json.dumps({
            "type": "expense",
            "card_last4": "1234",
            "amount": 3.0,
            "time": "2026-06-26 14:20",
            "counterparty": "微信支付",
        }, ensure_ascii=False)

    classifier._call_hermes = fake_call
    account_alert_ai.parse("一卡通账户变动通知正文", RECEIVED)
    assert "一卡通账户变动通知邮件" in seen["prompt"]
    assert "通知接收时间" in seen["prompt"]


def test_parse_raises_on_bad_type():
    _stub_ai("（模型没好好返回 JSON）")
    try:
        account_alert_ai.parse("...", RECEIVED)
    except RuntimeError:
        return
    assert False, "expected RuntimeError on unparseable AI output"


def test_parse_raises_on_missing_amount():
    _stub_ai({"type": "expense", "card_last4": "1234"})  # no amount
    try:
        account_alert_ai.parse("...", RECEIVED)
    except RuntimeError:
        return
    assert False, "expected RuntimeError when amount is missing"


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"ALL {len(tests)} ACCOUNT_ALERT_AI TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
