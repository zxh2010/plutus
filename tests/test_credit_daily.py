"""Regression tests for the credit-card daily-digest parser.

All samples are synthetic and safe to publish. Runnable with pytest or directly:

    python tests/test_credit_daily.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus.parsers import credit_daily  # noqa: E402

SYNTHETIC_DIGEST = """
<div>截至昨日最后一笔交易，您的额度和积分信息如下：</div>
<div>￥12,345.67</div>
<div>1,234</div>
<div>可用额度</div>
<div>积分余额</div>
<div>2026/06/19 您的消费明细如下：</div>
<div>09:00:00</div>
<div>CNY 10.00</div>
<div>尾号5678 消费 支付宝-示例商户</div>
<div>18:35:52</div>
<div>CNY -25.50</div>
<div>尾号5678 退货 财付通-示例餐厅</div>
"""


def _transactions():
    return credit_daily.parse(SYNTHETIC_DIGEST, source_msg_id="synthetic-digest")


def test_synthetic_digest_yields_valid_transactions():
    txns = _transactions()

    assert len(txns) == 2
    assert {txn.card_last4 for txn in txns} == {"5678"}
    assert {txn.card_type for txn in txns} == {"credit"}
    assert {txn.currency for txn in txns} == {"CNY"}
    assert len({txn.fingerprint for txn in txns}) == 2
    assert all(len(txn.txn_time) == 19 for txn in txns)


def test_refund_and_header_fields():
    txns = _transactions()
    expense, refund = txns

    assert (expense.txn_time, expense.amount, expense.action,
            expense.merchant_raw) == (
        "2026-06-19 09:00:00", 10.0, "消费", "支付宝-示例商户",
    )
    assert refund.amount == -25.5
    assert refund.action == "退货"
    assert refund.direction == "refund"
    assert refund.channel == "财付通"
    assert refund.merchant_key == "示例餐厅"
    assert refund.avail_credit == 12345.67
    assert refund.points == 1234


def test_amount_with_one_decimal_place():
    html = """
    <div>2026/07/01 您的消费明细如下：</div>
    <div>03:01:56</div>
    <div>CNY 3.0</div>
    <div>尾号5678 消费 增值服务使用费-示例服务</div>
    """
    txns = credit_daily.parse(html, source_msg_id="one-decimal")

    assert len(txns) == 1
    assert txns[0].amount == 3.0
    assert txns[0].merchant_raw == "增值服务使用费-示例服务"


def test_missing_business_date_yields_nothing():
    assert credit_daily.parse("<div>没有交易日期</div>") == []


def _main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"ALL {len(tests)} CREDIT DAILY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
