"""Tests for All Mail folder resolution and select error handling.

Regression: Gmail localizes the All Mail display name (e.g. "[Gmail]/所有邮件")
when the account UI language changes. The old hardcoded "[Gmail]/All Mail"
then SELECTs NO, the unchecked return left the connection in AUTH state, and
the next SEARCH died with "illegal in state AUTH" — silently breaking ingestion.

Runnable with pytest or directly: python tests/test_gmail_client.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import config, gmail_client  # noqa: E402

# A localized LIST reply: All Mail is named in modified UTF-7, identifiable only
# by its \All flag (the same bytes a live Chinese-language account returns).
_LOCALIZED_ROWS = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
    b'(\\All \\HasNoChildren) "/" "[Gmail]/&YkBnCZCuTvY-"',
    b'(\\Sent \\HasNoChildren) "/" "[Gmail]/&XfJT0ZCuTvY-"',
    b'(\\Trash \\HasNoChildren) "/" "[Gmail]/&XfJSIJZkkK5O9g-"',
]
_ALL_MAIL_NAME = '"[Gmail]/&YkBnCZCuTvY-"'


class _FakeIMAP:
    def __init__(self, list_rows, select_ok_for=None):
        self._rows = list_rows
        self._ok = select_ok_for          # mailbox name that SELECTs OK
        self.selected = None

    def list(self, *a, **k):
        return "OK", self._rows

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        if mailbox == self._ok:
            return "OK", [b"11466"]
        return "NO", [b"[NONEXISTENT] Unknown Mailbox: %s (Failure)" % mailbox.encode()]


class _FakeSearchIMAP:
    def __init__(self, provider="qq"):
        self._plutus_provider = provider
        self.calls = []

    def uid(self, command, charset, *criteria):
        self.calls.append((command, charset, criteria))
        if criteria and criteria[0] == "X-GM-RAW":
            return "OK", [b"9 7"]
        sender = criteria[-1]
        if sender == "a@example.com":
            return "OK", [b"3 1"]
        if sender == "b@example.com":
            return "OK", [b"2 3"]
        return "OK", [b""]


class _FakeFetchIMAP:
    _plutus_provider = "qq"

    def uid(self, command, uid, fields):
        raw = (
            b"From: Bank <notice@example.com>\r\n"
            b"Subject: =?utf-8?b?5rWL6K+V?=\r\n"
            b"Message-ID: <mail-123@example.com>\r\n"
            b"Date: Tue, 07 Jul 2026 10:00:00 +0800\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"hello"
        )
        return "OK", [(b"1 (INTERNALDATE \"07-Jul-2026 10:00:00 +0800\" RFC822 {1}", raw)]


def test_resolve_picks_all_flagged_folder_regardless_of_name():
    m = _FakeIMAP(_LOCALIZED_ROWS)
    assert gmail_client.resolve_all_mail(m) == _ALL_MAIL_NAME


def test_resolve_falls_back_when_no_all_flag():
    m = _FakeIMAP([b'(\\HasNoChildren) "/" "INBOX"'])
    assert gmail_client.resolve_all_mail(m) == gmail_client.ALL_MAIL


def test_select_uses_resolved_localized_folder():
    m = _FakeIMAP(_LOCALIZED_ROWS, select_ok_for=_ALL_MAIL_NAME)
    assert gmail_client.select_all_mail(m) == _ALL_MAIL_NAME
    assert m.selected == _ALL_MAIL_NAME


def test_select_honors_config_override():
    m = _FakeIMAP(_LOCALIZED_ROWS, select_ok_for='"My Label"')
    assert gmail_client.select_all_mail(m, '"My Label"') == '"My Label"'
    assert m.selected == '"My Label"'


def test_select_raises_clear_error_on_failure():
    # Old bug: hardcoded English name no longer exists -> SELECT NO -> silent.
    m = _FakeIMAP([b'(\\HasNoChildren) "/" "INBOX"'])  # no \All -> fallback name
    try:
        gmail_client.select_all_mail(m)
    except RuntimeError as exc:
        assert "SELECT" in str(exc) and "failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when SELECT fails")


def test_gmail_search_keeps_x_gm_raw():
    m = _FakeSearchIMAP("gmail")
    assert gmail_client.search_uids(m, "from:a@example.com newer_than:7d") == [9, 7]
    assert m.calls[0][2][0] == "X-GM-RAW"


def test_standard_imap_search_unions_senders_and_since_date():
    m = _FakeSearchIMAP("qq")
    uids = gmail_client.search_uids(
        m, "(from:a@example.com OR from:b@example.com) after:2026/07/01"
    )
    assert uids == [1, 2, 3]
    assert m.calls[0][2] == ("SINCE", "01-Jul-2026", "FROM", "a@example.com")
    assert m.calls[1][2] == ("SINCE", "01-Jul-2026", "FROM", "b@example.com")


def test_standard_imap_search_requires_sender():
    m = _FakeSearchIMAP("163")
    try:
        gmail_client.search_uids(m, "newer_than:7d")
    except RuntimeError as exc:
        assert "from:" in str(exc)
    else:
        raise AssertionError("expected RuntimeError without from: sender")


def test_fetch_standard_imap_uses_message_id_as_stable_id():
    fe = gmail_client.fetch(_FakeFetchIMAP(), 1)
    assert fe.gmail_msgid == "<mail-123@example.com>"
    assert fe.sender == "Bank <notice@example.com>"
    assert fe.subject == "测试"
    assert fe.text == "hello"


def test_config_maps_legacy_gmail_to_mail_provider():
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            p = Path("config.toml")
            p.write_text(
                '[gmail]\nemail = "u@gmail.com"\napp_password = "pw"\n',
                encoding="utf-8",
            )
            cfg = config.load(str(p))
        finally:
            os.chdir(old_cwd)
    assert cfg["mail"]["provider"] == "gmail"
    assert cfg["mail"]["email"] == "u@gmail.com"


def test_config_keeps_explicit_domestic_mail_provider():
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            p = Path("config.toml")
            secret = Path("pw.txt")
            secret.write_text("auth-code", encoding="utf-8")
            p.write_text(
                '[mail]\nprovider = "qq"\nemail = "u@qq.com"\napp_password_file = "'
                + str(secret)
                + '"\n',
                encoding="utf-8",
            )
            cfg = config.load(str(p))
        finally:
            os.chdir(old_cwd)
    assert cfg["mail"]["provider"] == "qq"
    assert cfg["mail"]["email"] == "u@qq.com"
    assert cfg["mail"]["app_password"] == "auth-code"


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"ALL {len(tests)} ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
