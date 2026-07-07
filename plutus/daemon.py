"""Plutus daemon: poll Gmail, ingest new emails, AI-classify brand-new merchants,
and push the new transactions to WeChat. Runs forever; one cycle per interval.

Confirmations happen on WeChat (the hermes agent calls the Plutus MCP tools) or
in the web console — the daemon only ingests, suggests, and notifies.

Run: python -m plutus.daemon   (kept alive by launchd for 7x24)
"""
from __future__ import annotations

import time
import traceback

from . import classifier, config, ingest, notify, store


def run_once(cfg: dict) -> None:
    # 1) Collect credit, debit, and income records from email.
    try:
        ingest.run(config_path="config.toml", incremental=True)
    except Exception as exc:  # IMAP/Gmail hiccups must not block notifications.
        print(f"[email] skipped: {exc}")
        traceback.print_exc()

    conn = store.get_conn(cfg["db"]["path"])
    try:
        # 2) AI-suggest only the brand-new pending merchants
        pend = conn.execute(
            "SELECT count(*) FROM transactions "
            "WHERE status='pending' AND voided=0 AND merchant_key IS NOT NULL"
        ).fetchone()[0]
        if pend:
            print(f"[classify] {pend} pending merchant-rows -> AI")
            classifier.suggest_pending(conn, include_suggested=False)
        # 3) push everything not yet notified
        res = notify.notify_new(conn, cfg)
        if res.get("notified"):
            print(f"[notify] pushed {res['notified']} new transactions to WeChat")
        elif res.get("error"):
            print(f"[notify] error: {res['error']}")
    finally:
        conn.close()


def main() -> None:
    cfg = config.load()
    interval = int(cfg.get("poll", {}).get("interval_seconds", 90))
    print(f"Plutus daemon started — polling every {interval}s")
    while True:
        try:
            run_once(cfg)
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


if __name__ == "__main__":
    main()
