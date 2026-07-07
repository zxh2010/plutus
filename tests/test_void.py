"""Regression tests for single-transaction soft-remove (store.set_voided).

A voided row must drop out of every spend stat yet remain in the table,
reachable via the void filter and reversible. This backs the ledger's
"不计入 / 恢复" buttons (reimbursed expenses, own-account transfers, mistakes).

Runnable with pytest or directly:  python tests/test_void.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import store  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    return conn


def _add(conn, txn_time, amount, *, merchant="m"):
    cur = conn.execute(
        """INSERT INTO transactions
           (fingerprint, card_last4, card_type, txn_time, amount, currency,
            direction, action, merchant_raw, merchant_key, status, voided)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
        (f"fp:{txn_time}:{amount}:{merchant}", "5678", "credit", txn_time, amount,
         "CNY", "expense", "消费", merchant, merchant, "confirmed"),
    )
    conn.commit()
    return cur.lastrowid


def test_void_removes_from_stats():
    store.set_billing_start_day(1)
    conn = _db()
    keep = _add(conn, "2026-06-10 10:00:00", 100.0)
    drop = _add(conn, "2026-06-11 10:00:00", 200.0)   # the reimbursed meal

    assert store.summary(conn, "2026-06")["spend"] == 300.0
    store.set_voided(conn, drop, True)

    # gone from summary, the monthly matrix, and the default ledger list
    assert store.summary(conn, "2026-06")["spend"] == 100.0
    spend = {c["m"]: c["spend"] for c in store.monthly_matrix(conn)["cells"]}
    assert spend["2026-06"] == 100.0
    ids = {t["id"] for t in store.list_transactions(conn, month="2026-06")}
    assert ids == {keep}


def test_void_row_still_visible_under_void_filter():
    conn = _db()
    drop = _add(conn, "2026-06-11 10:00:00", 200.0)
    store.set_voided(conn, drop, True)
    voided = {t["id"] for t in store.list_transactions(conn, status="void")}
    assert voided == {drop}


def test_restore_brings_it_back():
    store.set_billing_start_day(1)
    conn = _db()
    drop = _add(conn, "2026-06-11 10:00:00", 200.0)
    store.set_voided(conn, drop, True)
    assert store.summary(conn, "2026-06")["spend"] == 0.0
    store.set_voided(conn, drop, False)               # restore
    assert store.summary(conn, "2026-06")["spend"] == 200.0
    ids = {t["id"] for t in store.list_transactions(conn, month="2026-06")}
    assert ids == {drop}


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"ALL {len(tests)} VOID TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
