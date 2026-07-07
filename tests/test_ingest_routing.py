"""Regression tests for email-only collection routing.

Both card types collect incrementally from email. Debit account alerts are parsed
by the shared AI parser and can produce either transactions or deposits.

Runnable with pytest or directly:  python tests/test_ingest_routing.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import account_alert_ai, ingest, store  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    return conn


def _expense_rec():
    return {"type": account_alert_ai.EXPENSE, "card_last4": "5678", "txn_time": "2026-06-25 12:00",
            "amount": 30.0, "counterparty": "示例商户", "channel": "财付通",
            "kind": "", "note": ""}


def _save_email_anchor(conn: sqlite3.Connection, mid: str) -> None:
    store.save_email(
        conn, gmail_msgid=mid, sender="notice@cmbchina.com",
        subject="一卡通账户变动通知", internal_ms=0,
        email_type="debit_alert", status="parsed",
    )


# ---- email parser gating -----------------------------------------------

def test_email_parsers_include_both_card_types():
    src = {"credit": {"channel": "email", "bank": "cmb"},
           "debit": {"channel": "email", "bank": "cmb"}}
    ps = ingest._email_parsers_for(src)
    assert [p.card_type for p in ps] == ["credit", "debit"]


def test_email_parsers_include_realtime_debit_email():
    src = {"credit": {"channel": "email", "bank": "cmb"},
           "debit": {"channel": "email", "bank": "cmb"}}
    assert [p.card_type for p in ingest._email_parsers_for(src)] == ["credit", "debit"]


def test_email_ai_expense_books_debit_transaction():
    conn = _db()
    stats = Counter()
    _save_email_anchor(conn, "email:1")
    ingest._save_ai_record(
        conn, "email:1", "account alert", None, _expense_rec(), stats, "debit"
    )
    row = conn.execute("SELECT card_type, amount FROM transactions").fetchone()
    assert row["card_type"] == "debit"
    assert row["amount"] == 30.0


def test_email_ai_income_goes_to_deposits_not_ledger():
    conn = _db()
    rec = {"type": account_alert_ai.INCOME, "card_last4": "1234", "txn_time": "2026-06-25 09:00",
           "amount": 8000.0, "counterparty": "公司", "channel": "", "kind": "工资", "note": ""}
    _save_email_anchor(conn, "email:2")
    ingest._save_ai_record(conn, "email:2", "account alert", None, rec, Counter(), "debit")
    assert conn.execute("SELECT count(*) c FROM transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT count(*) c FROM deposits").fetchone()["c"] == 1


def test_sms_ingest_entry_point_is_removed():
    assert not hasattr(ingest, "run_sms")


def _main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
