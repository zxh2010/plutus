"""Storage tests for structured Hermes operation suggestions."""
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


def _add(conn, amount: float, direction: str = "expense") -> int:
    cur = conn.execute(
        """INSERT INTO transactions
           (fingerprint, txn_time, amount, currency, direction, merchant_raw,
            merchant_key, status, voided)
           VALUES (?,?,?,?,?,?,?,?,0)""",
        (f"fp:{amount}:{direction}", "2026-07-09 10:00:00", amount, "CNY",
         direction, "merchant", "merchant", "pending"),
    )
    conn.commit()
    return cur.lastrowid


def test_save_and_read_structured_suggestion():
    conn = _db()
    expense = _add(conn, 100.0)
    refund = _add(conn, -100.0, "refund")

    result = store.save_operation_suggestion(
        conn, expense, "offset", [expense, refund], "Exact refund match"
    )

    assert result["ok"] is True
    suggestion = store.get_operation_suggestion(conn, expense)
    assert suggestion == {
        "transaction_id": expense,
        "operation": "offset",
        "related_transaction_ids": [expense, refund],
        "reason": "Exact refund match",
    }


def test_rejects_unknown_operation_and_transaction_ids():
    conn = _db()
    txn_id = _add(conn, 100.0)

    assert store.save_operation_suggestion(
        conn, txn_id, "delete", [txn_id], "Bad operation"
    )["ok"] is False
    assert store.save_operation_suggestion(
        conn, txn_id, "merge", [txn_id, 9999], "Missing transaction"
    )["ok"] is False


def test_none_clears_existing_suggestion():
    conn = _db()
    txn_id = _add(conn, 100.0)
    store.save_operation_suggestion(conn, txn_id, "void", [txn_id], "Ignore transfer")

    result = store.save_operation_suggestion(conn, txn_id, "none", [], "")

    assert result["ok"] is True
    assert store.get_operation_suggestion(conn, txn_id) is None


def test_rejects_voided_related_transaction():
    conn = _db()
    txn_id = _add(conn, 100.0)
    related_id = _add(conn, -40.0, "refund")
    store.set_voided(conn, related_id, True)

    result = store.save_operation_suggestion(
        conn, txn_id, "merge", [txn_id, related_id], "Partial refund"
    )

    assert result["ok"] is False


def test_expected_offset_rejects_nonzero_net_without_mutation():
    conn = _db()
    expense = _add(conn, 100.0)
    refund = _add(conn, -40.0, "refund")

    result = store.merge_transactions(
        conn, [expense, refund], expected_operation="offset"
    )

    assert result["ok"] is False
    live = conn.execute(
        "SELECT count(*) FROM transactions WHERE voided=0"
    ).fetchone()[0]
    assert live == 2


def test_pending_merchants_exposes_operation_advice():
    conn = _db()
    txn_id = _add(conn, 100.0)
    store.save_operation_suggestion(
        conn, txn_id, "void", [txn_id], "Reimbursed expense"
    )

    merchant = store.pending_merchants(conn)[0]

    assert merchant["suggested_operation"] == "void"
    assert merchant["suggested_related_ids"] == f"[{txn_id}]"
    assert merchant["suggested_operation_reason"] == "Reimbursed expense"


def _main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"ALL {len(tests)} OPERATION-SUGGESTION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
