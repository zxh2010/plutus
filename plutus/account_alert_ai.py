"""AI parsing of CMB account-alert emails into one exclusive taxonomy.

Every real-time account-alert email is handed to the local hermes agent, which
classifies it as income / debit-card expense / transfer-out / ignore and extracts
the fields without regex templates. The caller routes income to the deposits table,
expense/transfer_out -> transactions (debit card), ignore -> logged and dropped.

This supersedes the old split approach (deposits.ai_parse for income + the
parsers.debit_event regex for expense), whose templates only matched a couple of
real-world formats and silently dropped everything else.
"""
from __future__ import annotations

from datetime import datetime

from . import classifier

# Values of the "type" field the AI returns.
EXPENSE = "expense"            # debit-card spending (money leaves, to a merchant)
TRANSFER_OUT = "transfer_out"  # transfer/remittance from the debit card to another person
INCOME = "income"             # money arrives in your account (salary/reimburse/transfer in/...)
IGNORE = "ignore"             # everything else (credit repayment, self-transfer, FX, OTP, ads)
_TYPES = {EXPENSE, TRANSFER_OUT, INCOME, IGNORE}

_PROMPT = (
    "你是招商银行账户变动通知解析助手。下面是一封招行一卡通账户变动通知邮件。"
    "把它归入下列**一种**类型并抽取字段。\n"
    "类型定义（互斥，只能选一个）：\n"
    "- expense：本人借记卡(一卡通)消费/扣款，钱真正花出去。如「在【支付宝-X】发生快捷支付扣款」「消费」「网上支付」。\n"
    "- transfer_out：从本人借记卡向【他人】转账/汇款汇出。如「转账汇款…收款人:X」「通过招商银行转出…收款人:X」。\n"
    "- income：他人或机构付给你、钱进入你账户。如工资、报销、收到他人/公司汇款、理财赎回/分红/利息、退款。\n"
    "- ignore：其余一律忽略，包括：信用卡还款/关联信用卡扣款（如「向尾号XXXX的信用卡还款」「关联个人信用卡扣款」）、"
    "本人名下账户之间的划转（如内地账户汇到自己香港账户）、外币(美元/港币等)交易、验证码、营销/额度/逾期等通知。\n"
    "只统计【人民币(CNY)】；任何外币金额一律 type=ignore。\n"
    "counterparty 取值：expense=商户名；transfer_out=收款人姓名；income=付款方/来源；ignore=空。\n"
    "通知接收时间：{received}。正文若只有月日、没有年或时间，按接收时间补全。\n"
    "只输出一个 JSON 对象，不要多余文字或代码块标记：\n"
    '{{"type": "expense|transfer_out|income|ignore", "card_last4": "尾号4位，没有则空", '
    '"amount": 人民币数字, "time": "YYYY-MM-DD HH:MM", '
    '"counterparty": "见上，没有则空", "channel": "支付宝/微信/银联等渠道，没有则空", '
    '"kind": "income 时填 工资/报销/汇款/理财赎回/分红/利息/退款/其他，其余留空", '
    '"note": "备注，没有则空"}}\n\n'
    "通知正文：{text}"
)


def parse(text: str, received: datetime) -> dict | None:
    """Classify one account-alert email via AI.

    Returns a dict with a type key (expense/transfer_out/income) plus extracted
    fields, or None when the alert should be ignored. Transport failures and
    unusable responses raise so the caller can retry without silently dropping
    the source message.
    """
    prompt = _PROMPT.format(received=received.strftime("%Y-%m-%d %H:%M"), text=text)
    out = classifier._call_hermes(prompt)          # may raise (timeout/transport)
    res = classifier._extract_json(out)
    typ = res.get("type")
    if typ not in _TYPES:
        raise RuntimeError(f"AI 未返回有效 type：{out[:120]}")
    if typ == IGNORE:
        return None
    try:
        amount = round(float(res.get("amount")), 2)
    except (TypeError, ValueError):
        raise RuntimeError(f"AI {typ} 缺少有效金额：{out[:120]}")
    if amount <= 0:
        return None
    return {
        "type": typ,
        "card_last4": (res.get("card_last4") or "").strip(),
        "txn_time": (res.get("time") or received.strftime("%Y-%m-%d %H:%M")).strip(),
        "amount": amount,
        "counterparty": (res.get("counterparty") or "").strip(),
        "channel": (res.get("channel") or "").strip(),
        "kind": (res.get("kind") or "").strip(),
        "note": (res.get("note") or "").strip(),
    }
