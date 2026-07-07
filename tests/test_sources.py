"""Tests for the email parser registry and DB-backed source config.

Email is the format-specific channel and has a per-(card_type, bank) registry;
store.get_sources persists a per-card {channel, bank} mapping. Legacy SMS
configuration is repaired to email without changing historical records.

Runnable with pytest or directly:  python tests/test_sources.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import ingest, store  # noqa: E402
from plutus.parsers import registry  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    return conn


# ---- email parser registry ---------------------------------------------

def test_cmb_credit_email_parser_registered():
    assert registry.get_email("credit", "cmb") is not None


def test_cmb_debit_email_parser_uses_ai():
    parser = registry.get_email("debit", "cmb")
    assert parser is not None
    received = datetime(2026, 7, 5, 17, 33, 33)
    raw = registry.RawEmail(
        html=None,
        text="您账户1234于07月05日17:32在财付通-微信支付快捷支付3.00元，余额100.00",
        source_msg_id="debit-1",
        sender="95555@message.cmbchina.com",
        subject="一卡通账户变动通知",
        received=received,
    )
    assert parser.match(raw) is True

    original_parse = registry.account_alert_ai.parse
    seen = {}

    def fake_parse(text, message_received):
        seen["args"] = (text, message_received)
        return {
            "type": "expense",
            "card_last4": "1234",
            "txn_time": "2026-07-05 17:32",
            "amount": 3.0,
            "counterparty": "微信支付",
            "channel": "财付通",
            "kind": "",
            "note": "",
        }

    registry.account_alert_ai.parse = fake_parse
    try:
        result = parser.parse(raw)
    finally:
        registry.account_alert_ai.parse = original_parse

    assert result["type"] == "expense"
    assert seen["args"] == (raw.text, received)

    wrong_subject = registry.RawEmail(
        html=None,
        text=raw.text,
        source_msg_id="debit-2",
        sender=raw.sender,
        subject="香港一卡通月結單通知",
        received=received,
    )
    assert parser.match(wrong_subject) is False


def test_credit_email_matches_only_the_daily_digest():
    p = registry.get_email("credit", "cmb")
    digest = registry.RawEmail(html="<p>...</p>", text="", source_msg_id="g1",
                               sender="ccsvc@message.cmbchina.com", subject="每日信用管家")
    statement = registry.RawEmail(html=None, text="", source_msg_id="g2",
                                  sender="ccsvc@message.cmbchina.com", subject="信用卡账单")
    assert p.match(digest) is True
    assert p.match(statement) is False  # statement must not parse as transactions


def test_has_parser_registered_combos_only():
    # Both CMB card types support email; SMS is no longer a collection channel.
    assert registry.has_parser("debit", "sms", "cmb") is False
    assert registry.has_parser("credit", "sms", "cmb") is False
    assert registry.has_parser("credit", "email", "cmb") is True
    assert registry.has_parser("debit", "email", "cmb") is True
    assert registry.has_parser("credit", "carrier", "cmb") is False


# ---- store: source config ----------------------------------------------

def test_get_sources_defaults_to_email_only():
    conn = _db()
    s = store.get_sources(conn)
    assert s["credit"] == {"channel": "email", "bank": "cmb"}
    assert s["debit"] == {"channel": "email", "bank": "cmb"}


def test_source_options_match_card_policy():
    conn = _db()
    store.set_sources(conn, {"credit": {"channel": "sms", "bank": "cmb"},
                             "debit": {"channel": "email", "bank": "cmb"}})
    s = store.get_sources(conn)
    assert s["credit"]["channel"] == "email"
    assert s["debit"]["channel"] == "email"


def test_set_sources_rejects_bad_channel():
    conn = _db()
    saved = store.set_sources(conn, {"credit": {"channel": "carrier-pigeon"},
                                     "debit": {"channel": "sms"}})
    assert saved["credit"]["channel"] == "email"  # fell back to default
    assert saved["debit"]["channel"] == "email"


def test_get_sources_merges_defaults_for_missing_card():
    conn = _db()
    store.set_sources(conn, {"credit": {"channel": "email", "bank": "cmb"}, "debit": {}})
    s = store.get_sources(conn)
    assert s["credit"]["channel"] == "email"
    assert s["debit"] == {"channel": "email", "bank": "cmb"}  # default kept


def test_get_sources_repairs_legacy_sms_config_for_both_cards():
    conn = _db()
    store.set_watermark(conn, "sources", """{
        "credit": {"channel": "sms", "bank": "cmb"},
        "debit": {"channel": "sms", "bank": "cmb"}
    }""")
    s = store.get_sources(conn)
    assert s["credit"] == {"channel": "email", "bank": "cmb"}
    assert s["debit"] == {"channel": "email", "bank": "cmb"}


def test_debit_email_self_check_uses_only_debit_sender():
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            Path("config.toml").write_text(
                '[db]\npath = "test.db"\n[web]\nport = 8973\n',
                encoding="utf-8",
            )
            conn = sqlite3.connect("test.db")
            conn.execute("CREATE TABLE sync_state (k TEXT PRIMARY KEY, v TEXT)")
            conn.commit()
            conn.close()

            from plutus.web import server

            class FakeIMAP:
                def __init__(self):
                    self.query = ""

                def uid(self, command, charset, key, query):
                    self.query = query
                    return "OK", [b"1"]

                def logout(self):
                    return None

            fake_imap = FakeIMAP()
            original_connect = server.gmail_client.connect
            original_resolve = server.gmail_client.resolve_all_mail
            server.gmail_client.connect = lambda cfg: fake_imap
            server.gmail_client.resolve_all_mail = lambda m: '"[Gmail]/All Mail"'
            try:
                result = server._gmail_self_check("debit", "cmb")
            finally:
                server.gmail_client.connect = original_connect
                server.gmail_client.resolve_all_mail = original_resolve
        finally:
            os.chdir(old_cwd)

    assert result["ok"] is True
    assert "95555@message.cmbchina.com" in fake_imap.query
    assert "ccsvc@message.cmbchina.com" not in fake_imap.query


def test_mail_self_check_supports_domestic_provider():
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            Path("config.toml").write_text(
                '[mail]\nprovider = "qq"\nemail = "u@qq.com"\napp_password = "pw"\n'
                '[db]\npath = "test.db"\n[web]\nport = 8973\n',
                encoding="utf-8",
            )
            conn = sqlite3.connect("test.db")
            conn.execute("CREATE TABLE sync_state (k TEXT PRIMARY KEY, v TEXT)")
            conn.commit()
            conn.close()

            from plutus.web import server

            class FakeIMAP:
                _plutus_provider = "qq"

                def __init__(self):
                    self.calls = []

                def uid(self, command, charset, *criteria):
                    self.calls.append(criteria)
                    return "OK", [b"8 9"]

                def logout(self):
                    return None

            fake_imap = FakeIMAP()
            original_connect = server.gmail_client.connect
            server.gmail_client.connect = lambda cfg: fake_imap
            try:
                result = server._mail_self_check("debit", "cmb")
            finally:
                server.gmail_client.connect = original_connect
        finally:
            os.chdir(old_cwd)

    assert result["ok"] is True
    assert result["provider"] == "qq"
    assert result["provider_label"] == "QQ 邮箱"
    assert len(fake_imap.calls) == 1
    assert fake_imap.calls[0][0] == "SINCE"
    assert fake_imap.calls[0][2:] == ("FROM", "95555@message.cmbchina.com")


def test_mail_provider_options_include_domestic_mailboxes():
    from plutus.web import server

    keys = {x["key"] for x in server._mail_provider_options()}
    assert {"gmail", "qq", "163"}.issubset(keys)


def test_web_server_exposes_no_sms_self_check():
    from plutus.web import server

    assert not hasattr(server, "_sms_self_check")


def test_ai_email_records_route_to_ledger_and_deposits():
    conn = _db()
    received = datetime(2026, 7, 5, 17, 33, 33)

    store.save_email(
        conn,
        gmail_msgid="expense-email",
        sender="95555@message.cmbchina.com",
        subject="一卡通账户变动通知",
        internal_ms=0,
        email_type="debit_event",
        status="parsed",
    )
    expense = {
        "type": "expense",
        "card_last4": "1234",
        "txn_time": "2026-07-05 17:32",
        "amount": 3.0,
        "counterparty": "微信支付",
        "channel": "财付通",
        "kind": "",
        "note": "",
    }
    stats = Counter()
    ingest._save_ai_record(
        conn, "expense-email", "raw expense", received, expense, stats, "debit"
    )
    txn = conn.execute(
        "SELECT card_type, amount, merchant_raw FROM transactions"
    ).fetchone()
    assert tuple(txn) == ("debit", 3.0, "财付通-微信支付")

    store.save_email(
        conn,
        gmail_msgid="income-email",
        sender="95555@message.cmbchina.com",
        subject="一卡通账户变动通知",
        internal_ms=0,
        email_type="debit_event",
        status="parsed",
    )
    income = dict(expense)
    income.update({
        "type": "income",
        "amount": 88.0,
        "counterparty": "示例付款方",
        "kind": "报销",
    })
    ingest._save_ai_record(
        conn, "income-email", "raw income", received, income, stats, "debit"
    )
    deposit = conn.execute(
        "SELECT amount, kind, payer FROM deposits"
    ).fetchone()
    assert tuple(deposit) == (88.0, "报销", "示例付款方")


def _main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
