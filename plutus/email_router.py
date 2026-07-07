"""Classify a CMB email by sender + subject. Used for the stored email_type
label and as the credit-daily parser's match gate (parsers.registry).

Anchors are intentionally conservative; non-transaction mail is skipped and
logged rather than guessed at. Debit account-change emails are routed to the
AI-backed parser registered in parsers.registry.
"""
from __future__ import annotations

CREDIT_DAILY_SENDER = "ccsvc@message.cmbchina.com"
DEBIT_SENDER = "95555@message.cmbchina.com"

# Email type constants.
CREDIT_DAILY = "credit_daily"
DEBIT_EVENT = "debit_event"
CREDIT_STATEMENT = "credit_statement"
HK_STATEMENT = "hk_statement"
MARKETING = "marketing"
OTHER = "other"


def classify(sender: str, subject: str) -> str:
    s = (sender or "").lower()
    subj = subject or ""

    # NOTE: the daily digest AND the monthly statement both come from
    # ccsvc@message.cmbchina.com, so the subject is what distinguishes them.
    # Routing ccsvc mail to credit_daily by sender alone would parse the
    # statement as transactions and double-count the month.
    if CREDIT_DAILY_SENDER in s:
        if "每日信用管家" in subj:
            return CREDIT_DAILY
        if "账单" in subj or "帳單" in subj:
            return CREDIT_STATEMENT
        return OTHER

    # Debit / 一卡通 notifications. The mainland card (1234, CNY) and the Hong
    # Kong card (9012, foreign currency) share this sender; only the mainland
    # account-change email carries transactions we book. HK mail (月結單,
    # 资料维护, 养老金, OTP, ...) is skipped.
    if DEBIT_SENDER in s:
        if "账户变动" in subj and "香港" not in subj:
            return DEBIT_EVENT
        return OTHER

    if "cmbchina" in s:
        if "月結單" in subj or "月结单" in subj:
            return HK_STATEMENT
        if "账单" in subj or "帳單" in subj:
            return CREDIT_STATEMENT

    if any(k in subj for k in ("拒收", "退订", "优惠", "分期", "额度", "礼包")):
        return MARKETING

    return OTHER
