"""Core data model shared by parsers and the store."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

# Raw action word (from the email) -> normalized ledger direction.
DIRECTION_BY_ACTION = {
    "消费": "expense",
    "退货": "refund",
    "撤销": "refund",
    "取现": "withdraw",
    "预借现金": "withdraw",
    "还款": "repayment",
    "快捷支付扣款": "expense",
    "快捷支付": "expense",
    "网上支付": "expense",
    "自助转账": "transfer",
    "跨行转账": "transfer",
    "转账": "transfer",
    "代扣": "expense",
    "扣款": "expense",
    "结息": "income",
    "利息": "income",
    "入账": "income",
    "工资": "income",
    "手续费": "fee",
    "费用": "fee",
    "分期": "installment",
}

# Known payment channels that prefix a merchant as "<channel>-<merchant>".
KNOWN_CHANNELS = ("支付宝", "财付通", "微信", "微信支付", "银联")


def split_merchant(merchant_raw: str) -> tuple[Optional[str], str]:
    """'支付宝-示例餐厅' -> ('支付宝', '示例餐厅'); '直连商户' -> (None, '直连商户').

    merchant_key (second item) drops the channel so the same shop reached via
    different channels maps to one rule.
    """
    if "-" in merchant_raw:
        channel, rest = merchant_raw.split("-", 1)
        channel, rest = channel.strip(), rest.strip()
        if channel in KNOWN_CHANNELS and rest:
            return channel, rest
    return None, merchant_raw.strip()


@dataclass
class Transaction:
    card_last4: str
    card_type: str            # debit / credit
    txn_time: str             # ISO local time 'YYYY-MM-DD HH:MM:SS'
    amount: float             # negative = refund / reversal
    currency: str             # CNY / HKD
    action: str               # raw action word from the email
    merchant_raw: str
    direction: str = ""
    channel: Optional[str] = None
    merchant_key: Optional[str] = None
    balance: Optional[float] = None
    avail_credit: Optional[float] = None
    points: Optional[int] = None
    source_msg_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.direction:
            self.direction = DIRECTION_BY_ACTION.get(self.action, "other")
        ch, key = split_merchant(self.merchant_raw)
        if self.channel is None:
            self.channel = ch
        if self.merchant_key is None:
            self.merchant_key = key

    @property
    def fingerprint(self) -> str:
        """Stable, idempotent id for transaction-level dedup."""
        raw = f"{self.card_last4}|{self.txn_time}|{self.amount:.2f}|{self.merchant_raw}|{self.action}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()
