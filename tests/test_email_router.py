"""Routing tests, incl. the regression: the monthly statement and the daily
digest share one sender (ccsvc@message.cmbchina.com) and must be told apart by
subject so the statement is never parsed as transactions.

Runnable with pytest or directly: python tests/test_email_router.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plutus import email_router as r  # noqa: E402

CCSVC = "招商银行信用卡 <ccsvc@message.cmbchina.com>"


def test_daily_digest_routes_to_credit_daily():
    assert r.classify(CCSVC, "每日信用管家") == r.CREDIT_DAILY


def test_monthly_statement_is_not_credit_daily():
    # Regression: same sender as the daily digest, different subject.
    assert r.classify(CCSVC, "招商银行信用卡电子账单") == r.CREDIT_STATEMENT
    assert r.classify(CCSVC, "招商银行信用卡电子账单") != r.CREDIT_DAILY


def test_unknown_ccsvc_mail_is_not_parsed_as_daily():
    assert r.classify(CCSVC, "您有一条新消息") == r.OTHER


def test_marketing_is_skipped():
    assert r.classify("活动 <promo@cmbchina.com>", "618优惠分期专享，拒收请回复R") == r.MARKETING


def _main() -> int:
    test_daily_digest_routes_to_credit_daily()
    test_monthly_statement_is_not_credit_daily()
    test_unknown_ccsvc_mail_is_not_parsed_as_daily()
    test_marketing_is_skipped()
    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
