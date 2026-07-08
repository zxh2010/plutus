"""Registry of email transaction parsers, keyed by (card_type, bank).

When a card type is configured to collect from email (store.get_sources), the
daemon dispatches each email to the matching parser here. Adding a bank's email
support means one parse fn + one register_email() call — nothing else changes.

This module only wires the existing 招商银行 email parser behind the registry; it
does not change parsing logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .. import account_alert_ai, email_router
from ..models import Transaction
from . import credit_daily


@dataclass
class RawEmail:
    """A fetched email handed to an EmailParser's match/parse. `html` is the
    email HTML when present, `text` the de-tagged fallback; `source_msg_id` is the
    stable mail message id used for dedup and the transactions FK."""
    html: Optional[str]
    text: str
    source_msg_id: str
    sender: str = ""
    subject: str = ""
    received: Optional[datetime] = None


@dataclass(frozen=True)
class EmailParser:
    card_type: str                 # debit | credit
    bank: str                      # cmb | ...
    label: str                     # human-readable, shown in the settings UI
    senders: tuple                 # from-addresses this bank mails from
    match: Callable[[RawEmail], bool]
    parse: Callable[[RawEmail], object]
    output: str = "transactions"   # transactions | ai_record

    @property
    def key(self) -> tuple:
        return (self.card_type, self.bank)


_EMAIL: dict[tuple, EmailParser] = {}


def register_email(p: EmailParser) -> None:
    _EMAIL[p.key] = p


def get_email(card_type: str, bank: str) -> Optional[EmailParser]:
    return _EMAIL.get((card_type, bank))


def email_parsers() -> list[EmailParser]:
    return list(_EMAIL.values())


def senders_of(parsers) -> list:
    """Sorted, de-duplicated from-addresses across the given email parsers — the
    single source of truth for the mail search query and the connectivity
    self-check, so adding a bank's parser automatically widens both."""
    return sorted({s for p in parsers for s in p.senders})


def email_senders() -> list:
    """From-addresses across all registered email parsers."""
    return senders_of(_EMAIL.values())


def has_parser(card_type: str, channel: str, bank: str) -> bool:
    """Whether the email source has a registered parser."""
    return channel == "email" and get_email(card_type, bank) is not None


# ---- built-in 招商银行 sources -----------------------------------------

register_email(EmailParser(
    card_type="credit", bank="cmb",
    label="招商银行 · 每日信用管家（邮件）",
    senders=(email_router.CREDIT_DAILY_SENDER,),
    match=lambda m: email_router.classify(m.sender, m.subject) == email_router.CREDIT_DAILY,
    parse=lambda m: credit_daily.parse(m.html or m.text, source_msg_id=m.source_msg_id),
))


def _parse_cmb_debit_email(raw: RawEmail):
    """Parse one real-time debit alert with the shared AI transaction taxonomy."""
    if raw.received is None:
        raise ValueError("debit email is missing its received timestamp")
    return account_alert_ai.parse(raw.text, raw.received)


register_email(EmailParser(
    card_type="debit", bank="cmb",
    label="招商银行 · 一卡通账户变动通知（邮件）",
    senders=(email_router.DEBIT_SENDER,),
    match=lambda m: (
        email_router.DEBIT_SENDER in (m.sender or "").lower()
        and (m.subject or "").strip() == "一卡通账户变动通知"
    ),
    parse=_parse_cmb_debit_email,
    output="ai_record",
))
