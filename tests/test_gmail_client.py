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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import gmail_client  # noqa: E402

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


def _main() -> int:
    test_resolve_picks_all_flagged_folder_regardless_of_name()
    test_resolve_falls_back_when_no_all_flag()
    test_select_uses_resolved_localized_folder()
    test_select_honors_config_override()
    test_select_raises_clear_error_on_failure()
    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
