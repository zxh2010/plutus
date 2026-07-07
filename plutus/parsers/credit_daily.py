"""Parser for the credit-card "每日信用管家" daily digest email.

Flattened layout (one digest):

    截至昨日最后一笔交易，您的额度和积分信息如下：
    ￥12,345.67                      <- available credit
    1,234                           <- points
    可用额度
    积分余额
    2026/06/19 您的消费明细如下：     <- business date
    09:00:00                        <- time   ┐
    CNY 10.00                       <- amount ├ one transaction
    尾号5678 消费 支付宝-示例商户     <- detail ┘
    ...

Refunds appear as a negative amount with action 退货. Merchant text may itself
contain spaces (e.g. "支付宝-App Store _ Apple Mu").
"""
from __future__ import annotations

import re
from typing import Optional

from ..htmltext import html_to_lines
from ..models import Transaction

CARD_TYPE = "credit"

_RE_AVAIL = re.compile(r"^￥\s*([\d,]+\.\d{2})$")
_RE_POINTS = re.compile(r"^([\d,]+)$")
_RE_DATE = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s*您的消费明细")
_RE_TIME = re.compile(r"^(\d{2}:\d{2}:\d{2})$")
_RE_AMOUNT = re.compile(r"^CNY\s*(-?[\d,]+\.\d{1,2})$")
_RE_DETAIL = re.compile(r"^尾号(\d{4})\s+(\S+)\s+(.+)$")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse(html_body: str, source_msg_id: Optional[str] = None) -> list[Transaction]:
    lines = html_to_lines(html_body)

    avail: Optional[float] = None
    points: Optional[int] = None
    date_iso: Optional[str] = None

    # Header: available credit, points, then the business date.
    for ln in lines:
        if avail is None:
            m = _RE_AVAIL.match(ln)
            if m:
                avail = _num(m.group(1))
                continue
        if avail is not None and points is None:
            m = _RE_POINTS.match(ln)
            if m:
                points = int(m.group(1).replace(",", ""))
        m = _RE_DATE.search(ln)
        if m:
            date_iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            break

    txns: list[Transaction] = []
    if date_iso is None:
        return txns

    # Walk the body collecting time / amount / detail triples.
    i, n = 0, len(lines)
    while i < n:
        mt = _RE_TIME.match(lines[i])
        if not mt:
            i += 1
            continue

        amount_str: Optional[str] = None
        detail: Optional[re.Match] = None
        j = i + 1
        while j < n and j <= i + 3:
            if amount_str is None:
                ma = _RE_AMOUNT.match(lines[j])
                if ma:
                    amount_str = ma.group(1)
                    j += 1
                    continue
            if amount_str is not None:
                md = _RE_DETAIL.match(lines[j])
                if md:
                    detail = md
                    break
            j += 1

        if amount_str is not None and detail is not None:
            txns.append(
                Transaction(
                    card_last4=detail.group(1),
                    card_type=CARD_TYPE,
                    txn_time=f"{date_iso} {mt.group(1)}",
                    amount=_num(amount_str),
                    currency="CNY",
                    action=detail.group(2),
                    merchant_raw=detail.group(3).strip(),
                    avail_credit=avail,
                    points=points,
                    source_msg_id=source_msg_id,
                )
            )
            i = j + 1
        else:
            i += 1

    return txns
