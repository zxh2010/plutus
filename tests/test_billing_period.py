"""Regression tests for the configurable billing-period start day (store.py).

Covers the period-bucketing engine that backs the monthly/annual views:
  - start day 1 must behave exactly like the old calendar-month logic;
  - a non-1 start day shifts which period a transaction counts in, both in the
    Python mirror (period_of) and in the SQL aggregates (months/summary/
    monthly_matrix/list_transactions), and drives merge attribution.

Runnable with pytest or directly:  python tests/test_billing_period.py
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


def _add(conn, txn_time, amount, *, direction="expense", merchant="m", category=None):
    """Insert a minimal valid transaction and return its id."""
    cur = conn.execute(
        """INSERT INTO transactions
           (fingerprint, card_last4, card_type, txn_time, amount, currency,
            direction, action, merchant_raw, merchant_key, category, status, voided)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (f"fp:{txn_time}:{amount}:{merchant}", "5678", "credit", txn_time, amount,
         "CNY", direction, "消费", merchant, merchant, category, "confirmed"),
    )
    conn.commit()
    return cur.lastrowid


def _reset():
    store.set_billing_start_day(1)


# ---- period_of (Python mirror) ------------------------------------------

def test_default_is_calendar_month():
    """Start day 1 == old substr(txn_time,1,7) behavior, byte for byte."""
    _reset()
    for ts in ("2026-06-01 00:00:00", "2026-06-14 23:59:59",
               "2026-06-15 12:00:00", "2026-12-31 23:00:01"):
        assert store.period_of(ts) == ts[:7], ts


def test_boundary_day():
    """Start day 15: the 14th still belongs to the previous period, the 15th
    opens the new one."""
    store.set_billing_start_day(15)
    try:
        assert store.period_of("2026-07-14 23:59:59") == "2026-06"
        assert store.period_of("2026-07-15 00:00:00") == "2026-07"
        assert store.period_of("2026-07-16 09:00:00") == "2026-07"
    finally:
        _reset()


def test_year_boundary():
    """Start day 15: early-January spend rolls back into the prior December
    period (and thus the prior year)."""
    store.set_billing_start_day(15)
    try:
        assert store.period_of("2027-01-10 08:00:00") == "2026-12"
        assert store.period_of("2027-01-20 08:00:00") == "2027-01"
    finally:
        _reset()


def test_clamp():
    """Garbage / out-of-range start days fall back into [1, 28]."""
    store.set_billing_start_day(0)
    assert store._BILLING_START_DAY == 1
    store.set_billing_start_day(31)
    assert store._BILLING_START_DAY == 28
    store.set_billing_start_day("nope")
    assert store._BILLING_START_DAY == 1
    store.set_billing_start_day(None)
    assert store._BILLING_START_DAY == 1
    _reset()


# ---- SQL aggregates honor the start day ---------------------------------

def test_matrix_buckets():
    """Two transactions straddling the 15th land in different periods in the
    monthly matrix, and summary(period) sees the right one."""
    store.set_billing_start_day(15)
    try:
        conn = _db()
        _add(conn, "2026-07-14 10:00:00", 100.0)  # -> 2026-06
        _add(conn, "2026-07-15 10:00:00", 200.0)  # -> 2026-07

        spend_by_period = {c["m"]: c["spend"] for c in store.monthly_matrix(conn)["cells"]}
        assert spend_by_period.get("2026-06") == 100.0, spend_by_period
        assert spend_by_period.get("2026-07") == 200.0, spend_by_period

        assert store.summary(conn, "2026-06")["spend"] == 100.0
        assert store.summary(conn, "2026-07")["spend"] == 200.0

        assert set(store.months(conn)) == {"2026-06", "2026-07"}

        ids_06 = {t["id"] for t in store.list_transactions(conn, month="2026-06")}
        assert len(ids_06) == 1
    finally:
        _reset()


def test_annual_bucket_follows_period():
    """Early-January spend with a mid-month start day counts in the prior
    year's column."""
    store.set_billing_start_day(15)
    try:
        conn = _db()
        _add(conn, "2027-01-10 10:00:00", 50.0)   # -> 2026-12 -> 2026
        _add(conn, "2027-01-20 10:00:00", 70.0)   # -> 2027-01 -> 2027
        years = {c["y"]: c["spend"] for c in store.annual_matrix(conn)["cells"]}
        assert years.get("2026") == 50.0, years
        assert years.get("2027") == 70.0, years
    finally:
        _reset()


def test_merge_uses_period():
    """A cross-period expense + refund merges into the earliest period."""
    store.set_billing_start_day(15)
    try:
        conn = _db()
        a = _add(conn, "2026-06-10 10:00:00", 300.0)   # -> 2026-05
        b = _add(conn, "2026-07-20 10:00:00", -120.0, direction="refund")  # -> 2026-07
        res = store.merge_transactions(conn, [a, b])
        assert res["ok"], res
        assert res["month"] == "2026-05", res          # earliest period wins
        assert res["net"] == 180.0, res
    finally:
        _reset()


# ---- DB persistence (live UI setting) -----------------------------------

def test_save_and_load_roundtrip():
    """Saving to the DB persists the value; loading into a fresh process state
    restores it (DB wins over the in-process default)."""
    _reset()
    conn = _db()
    assert store.save_billing_start_day(conn, 15) == 15
    store.set_billing_start_day(1)              # simulate a fresh process
    assert store.load_billing_start_day(conn) == 15
    assert store.get_billing_start_day() == 15
    _reset()


def test_save_clamps_then_persists():
    """An out-of-range value is clamped before it is stored, so a later load
    reads the clamped value."""
    _reset()
    conn = _db()
    assert store.save_billing_start_day(conn, 99) == 28
    store.set_billing_start_day(1)
    assert store.load_billing_start_day(conn) == 28
    _reset()


def test_load_absent_keeps_current():
    """With nothing persisted, load leaves the current (config default) value
    untouched instead of forcing it back to 1."""
    _reset()
    conn = _db()
    store.set_billing_start_day(7)
    assert store.load_billing_start_day(conn) == 7
    _reset()


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"ALL {len(tests)} BILLING-PERIOD TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
