"""Tests for structured operation advice in Hermes classification."""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import classifier  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.execute(
        "INSERT INTO categories (name, key, descr, active, sort) VALUES ('购物','shopping','Goods',1,1)"
    )
    return conn


def _add(conn, amount: float, direction: str) -> int:
    cur = conn.execute(
        """INSERT INTO transactions
           (fingerprint, card_last4, card_type, txn_time, amount, currency,
            direction, merchant_raw, merchant_key, status, voided)
           VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
        (f"fp:{amount}:{direction}", "1234", "credit", "2026-07-09 10:00:00",
         amount, "CNY", direction, "Shop", "Shop", "pending"),
    )
    conn.commit()
    return cur.lastrowid


def test_prompt_always_contains_operation_knowledge_and_transaction_ids():
    seen = []
    original = classifier._call_hermes
    classifier._call_hermes = lambda prompt: seen.append(prompt) or "{}"
    try:
        classifier.classify_merchants(
            "context",
            [("Shop", [{"id": 12, "amount": 100.0, "txn_time": "2026-07-09 10:00:00",
                        "direction": "expense", "card_last4": "1234"}])],
        )
    finally:
        classifier._call_hermes = original

    prompt = seen[0]
    assert "merge" in prompt
    assert "offset" in prompt
    assert "void" in prompt
    assert "split" in prompt
    assert '"id": 12' in prompt


def test_suggest_pending_persists_valid_structured_advice():
    conn = _db()
    expense = _add(conn, 100.0, "expense")
    refund = _add(conn, -100.0, "refund")
    original = classifier.classify_merchants
    classifier.classify_merchants = lambda context, items: {
        "Shop": {
            "category": "购物",
            "operation": "offset",
            "related_transaction_ids": [expense, refund],
            "reason": "Exact full refund",
        }
    }
    try:
        result = classifier.suggest_pending(conn)
    finally:
        classifier.classify_merchants = original

    assert result["suggested"] == 1
    suggestion = conn.execute(
        "SELECT operation, related_transaction_ids FROM operation_suggestions WHERE transaction_id=?",
        (expense,),
    ).fetchone()
    assert suggestion["operation"] == "offset"
    assert str(refund) in suggestion["related_transaction_ids"]


def test_invalid_operation_is_ignored_without_losing_category():
    conn = _db()
    txn_id = _add(conn, 20.0, "expense")
    original = classifier.classify_merchants
    classifier.classify_merchants = lambda context, items: {
        "Shop": {
            "category": "购物",
            "operation": "delete",
            "related_transaction_ids": [txn_id],
            "reason": "Unsupported",
        }
    }
    try:
        result = classifier.suggest_pending(conn)
    finally:
        classifier.classify_merchants = original

    assert result["suggested"] == 1
    assert conn.execute("SELECT count(*) FROM operation_suggestions").fetchone()[0] == 0


def _main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"ALL {len(tests)} CLASSIFIER-OPERATION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
