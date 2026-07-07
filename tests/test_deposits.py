"""Tests for the deposits store and the find_deposits MCP tool.

AI-based email parsing is covered by test_account_alert_ai.py; this file covers
persistence and the MCP read path only.

Runnable with pytest or directly.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import mcp_server, store  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    return conn


# ---- deposits store -----------------------------------------------------

def _rec(msgid, amount, t="2026-06-25 14:30", kind="报销", payer="示例付款方"):
    return {"source_msg_id": msgid, "card_last4": "1234", "txn_time": t,
            "amount": amount, "kind": kind, "payer": payer, "note": "", "raw_text": "x"}


def test_insert_dedup_and_list():
    conn = _db()
    assert store.insert_deposit(conn, _rec("sms:1", 1322.00)) is True
    assert store.insert_deposit(conn, _rec("sms:1", 1322.00)) is False  # dedup
    assert store.insert_deposit(conn, _rec("sms:2", 283.97, kind="分红")) is True
    rows = store.list_deposits(conn)
    assert len(rows) == 2
    hit = store.list_deposits(conn, amount=1322.00)
    assert len(hit) == 1 and hit[0]["payer"] == "示例付款方"


def test_list_since_days_window():
    conn = _db()
    store.insert_deposit(conn, _rec("sms:1", 100, t="2019-10-08 10:16"))  # old
    store.insert_deposit(conn, _rec("sms:2", 200, t=datetime.now().strftime("%Y-%m-%d %H:%M")))
    recent = store.list_deposits(conn, since_days=30)
    assert {r["amount"] for r in recent} == {200}


# ---- find_deposits MCP tool (HTTP client over /api/deposits) ------------

def test_tool_text_and_miss():
    sample = {"txn_time": "2026-06-25 14:30", "card_last4": "1234", "kind": "报销",
              "payer": "示例公司", "amount": 1322.0}
    mcp_server._get = lambda path: {"rows": [sample]} if "amount=1322" in path else {"rows": []}
    hit = mcp_server.call_tool("find_deposits", {"amount": 1322.00})
    assert "报销" in hit and "示例公司" in hit and "1322.00" in hit
    miss = mcp_server.call_tool("find_deposits", {"amount": 999.99})
    assert "没有" in miss


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"ALL {len(tests)} DEPOSIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
