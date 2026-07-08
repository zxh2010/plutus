"""Collect transactions from email and store them. Idempotent and resumable:
emails are deduped by message id, transactions by fingerprint, and the highest
processed UID is the resume watermark.

Which channel feeds which card type is configured per user (store.get_sources);
email is parsed by a per-bank registry.

Usage:
    python -m plutus.ingest --since 2026/01/01          # email backfill
    python -m plutus.ingest                              # email incremental
"""
from __future__ import annotations

import argparse
from collections import Counter

from . import account_alert_ai, config, email_router, gmail_client, store
from .models import Transaction
from .parsers import registry


def _build_query(cfg: dict, since: str | None, incremental: bool, senders: list) -> str:
    # The from-addresses come from the active email parsers, so the search widens
    # automatically as banks are registered.
    base = "(" + " OR ".join(f"from:{s}" for s in senders) + ")"
    if since:
        return f"{base} after:{since}"
    if incremental:
        return base  # UID watermark does the windowing
    return (cfg.get("mail") or {}).get("query") or (cfg.get("gmail") or {}).get("query", base)


def _email_parsers_for(sources: dict) -> list:
    """Email parsers for the card types configured to collect from email. A card
    pointed at email with no registered parser is skipped (the UI flags it)."""
    out = []
    for ct, cfg in sources.items():
        if cfg.get("channel") == "email":
            p = registry.get_email(ct, cfg.get("bank"))
            if p is not None:
                out.append(p)
    return out


def run(config_path: str = "config.toml", since: str | None = None,
        limit: int | None = None, incremental: bool = False) -> None:
    cfg = config.load(config_path)
    conn = store.get_conn(cfg["db"]["path"])
    parsers = _email_parsers_for(store.get_sources(conn))
    if not parsers:
        print("没有卡种配置为邮箱采集，跳过邮件采集。")
        conn.close()
        return
    m = gmail_client.connect(cfg)
    try:
        query = _build_query(cfg, since, incremental, registry.senders_of(parsers))
        uids = gmail_client.search_uids(m, query)

        last_uid = int(store.get_watermark(conn, "last_uid") or 0)
        if incremental:
            uids = [u for u in uids if u > last_uid]
        if limit:
            uids = uids[-limit:]
        print(f"query: {query}\nuids to process: {len(uids)}")

        types = Counter()
        totals = Counter()
        max_uid = last_uid
        for n, uid in enumerate(uids, 1):
            fe = gmail_client.fetch(m, uid)
            max_uid = max(max_uid, uid)
            if store.email_exists(conn, fe.gmail_msgid):
                types["already_seen"] += 1
                continue

            etype = email_router.classify(fe.sender, fe.subject)
            raw = registry.RawEmail(html=fe.html, text=fe.text,
                                    source_msg_id=fe.gmail_msgid,
                                    sender=fe.sender, subject=fe.subject,
                                    received=fe.received)
            txns, ai_rec, status, error = [], None, "skipped", None
            # Dispatch to the first configured email parser that claims this mail.
            # Only configured email parsers participate in dispatch.
            for p in parsers:
                if p.match(raw):
                    parsed = p.parse(raw)
                    if p.output == "ai_record":
                        ai_rec = parsed
                        status = "parsed" if ai_rec else "skipped"
                    else:
                        txns = parsed
                        status = "parsed" if txns else "error"
                        error = None if txns else f"{p.label} yielded 0 transactions"
                    break

            store.save_email(
                conn, gmail_msgid=fe.gmail_msgid, sender=fe.sender,
                subject=fe.subject, internal_ms=fe.internal_ms,
                email_type=etype, status=status, error=error,
            )
            types[f"{etype}:{status}"] += 1
            if txns:
                s = store.save_transactions(conn, txns)
                for k, v in s.items():
                    totals[k] += v
            elif ai_rec:
                _save_ai_record(
                    conn, fe.gmail_msgid, fe.text, fe.received,
                    ai_rec, totals, p.card_type,
                )
            if n % 25 == 0:
                conn.commit()
                print(f"  ...{n}/{len(uids)}")

        store.set_watermark(conn, "last_uid", max_uid)
        conn.commit()
        paired = store.pair_offsetting_refunds(conn)

        print(f"\noffsetting refund pairs voided: {paired}")
        print("=== email types ===")
        for k, v in sorted(types.items()):
            print(f"  {k}: {v}")
        print("=== transactions ===")
        for k in ("inserted", "pending", "dup"):
            print(f"  {k}: {totals[k]}")
        print(f"watermark last_uid -> {max_uid}")
    finally:
        try:
            m.logout()
        except Exception:
            pass
        conn.close()


def _save_ai_record(conn, mid: str, raw_text: str, received: datetime,
                    rec: dict, stats: Counter, card_type: str | None) -> None:
    """Route one source-neutral AI result to deposits or the spending ledger.

    The caller owns the email processing anchor and writes it first so the
    transaction foreign key is satisfied.
    """
    if rec["type"] == account_alert_ai.INCOME:
        store.insert_deposit(conn, {
            "source_msg_id": mid, "raw_text": raw_text,
            "card_last4": rec["card_last4"], "txn_time": rec["txn_time"],
            "amount": rec["amount"], "kind": rec["kind"],
            "payer": rec["counterparty"], "note": rec["note"],
        })
        stats["income"] += 1
        return

    if card_type is None:
        stats["unrouted"] += 1
        return
    if rec["type"] == account_alert_ai.TRANSFER_OUT:
        action, merchant_raw = "转账", rec["counterparty"]
    else:
        action = "消费"
        merchant_raw = (f"{rec['channel']}-{rec['counterparty']}"
                        if rec["channel"] else rec["counterparty"])
    txn = Transaction(
        card_last4=rec["card_last4"], card_type=card_type,
        txn_time=rec["txn_time"], amount=rec["amount"], currency="CNY",
        action=action, merchant_raw=merchant_raw, direction="expense",
        source_msg_id=mid,
    )
    s = store.save_transactions(conn, [txn])
    for k, v in s.items():
        stats[k] += v
    stats[rec["type"]] += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--since", help="mail search date YYYY/MM/DD for backfill")
    ap.add_argument("--limit", type=int, help="process only the newest N matches")
    ap.add_argument("--incremental", action="store_true", help="only UIDs above watermark")
    args = ap.parse_args()
    run(config_path=args.config, since=args.since, limit=args.limit,
        incremental=args.incremental)


if __name__ == "__main__":
    main()
