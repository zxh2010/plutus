"""SQLite persistence: email log, transactions with two-level dedup, merchant
rules, and the sync watermark."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from .models import Transaction


def now_ms() -> int:
    return int(time.time() * 1000)


def get_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---- emails -------------------------------------------------------------

def email_exists(conn: sqlite3.Connection, gmail_msgid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM emails WHERE gmail_msg_id = ?", (gmail_msgid,)
    ).fetchone()
    return row is not None


def save_email(conn, *, gmail_msgid, sender, subject, internal_ms,
               email_type, status, error=None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO emails
           (gmail_msg_id, sender, subject, internal_date, email_type,
            status, error, fetched_at, parsed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (gmail_msgid, sender, subject, internal_ms, email_type,
         status, error, now_ms(), now_ms()),
    )


# ---- transactions -------------------------------------------------------

def save_transactions(conn: sqlite3.Connection, txns: list[Transaction]) -> dict:
    stats = {"inserted": 0, "dup": 0, "pending": 0}
    for t in txns:
        # Classification is fully AI-driven now: new transactions land as pending
        # and wait for an AI suggestion or a manual confirm (no rule matching).
        category, src, status = None, None, "pending"
        cur = conn.execute(
            """INSERT OR IGNORE INTO transactions
               (fingerprint, source_msg_id, card_last4, card_type, txn_time,
                amount, currency, direction, action, merchant_raw, merchant_key,
                channel, balance, avail_credit, points, category, category_src,
                status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.fingerprint, t.source_msg_id, t.card_last4, t.card_type, t.txn_time,
             t.amount, t.currency, t.direction, t.action, t.merchant_raw, t.merchant_key,
             t.channel, t.balance, t.avail_credit, t.points, category, src,
             status, now_ms(), now_ms()),
        )
        if cur.rowcount == 1:
            stats["inserted"] += 1
            stats["pending"] += 1
        else:
            stats["dup"] += 1
    return stats


# ---- refund offsetting --------------------------------------------------

def pair_offsetting_refunds(conn) -> int:
    """Void exact refund/consumption offsets so they stay out of the books.

    A 退货 (negative) is paired with the most recent earlier consumption on the
    same card and merchant for the same absolute amount that isn't already
    paired. Both rows are marked voided and point at each other. Refunds with no
    exact match stay as genuine (negative) refunds that net against spending.
    """
    refunds = conn.execute(
        "SELECT id, card_last4, merchant_key, amount, txn_time FROM transactions "
        "WHERE direction='refund' AND voided=0 AND merchant_key IS NOT NULL "
        "ORDER BY txn_time"
    ).fetchall()
    paired = 0
    for r in refunds:
        match = conn.execute(
            """SELECT id FROM transactions
               WHERE direction='expense' AND voided=0 AND card_last4=? AND merchant_key=?
                 AND ABS(amount - ?) < 0.005 AND txn_time <= ? AND id != ?
               ORDER BY txn_time DESC LIMIT 1""",
            (r["card_last4"], r["merchant_key"], abs(r["amount"]), r["txn_time"], r["id"]),
        ).fetchone()
        if not match:
            continue
        conn.execute(
            "UPDATE transactions SET voided=1, offset_of=?, updated_at=? WHERE id=?",
            (match["id"], now_ms(), r["id"]),
        )
        conn.execute(
            "UPDATE transactions SET voided=1, offset_of=?, updated_at=? WHERE id=?",
            (r["id"], now_ms(), match["id"]),
        )
        paired += 1
    conn.commit()
    return paired


# ---- watermark ----------------------------------------------------------

def get_watermark(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT v FROM sync_state WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else None


def set_watermark(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_state (k, v) VALUES (?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (key, str(value)),
    )


# ---- collection sources (DB-backed) ------------------------------------
# Each card type records its bank and email channel. Keeping this setting
# DB-backed preserves bank selection and repairs legacy SMS configurations
# without touching historical transactions.
_SETTING_SOURCES = "sources"
_DEFAULT_SOURCES = {
    "credit": {"channel": "email", "bank": "cmb"},
    "debit": {"channel": "email", "bank": "cmb"},
}
_SOURCE_CHANNELS = {
    "credit": ("email",),
    "debit": ("email",),
}


def _source_channel(card_type: str, channel, default: str) -> str:
    """Return a channel exposed for this card type, or its safe default."""
    return channel if channel in _SOURCE_CHANNELS[card_type] else default


def get_sources(conn: sqlite3.Connection) -> dict:
    """The configured collection sources, defaults merged in for any missing card
    type. Always returns both 'credit' and 'debit'."""
    out = {k: dict(v) for k, v in _DEFAULT_SOURCES.items()}
    raw = get_watermark(conn, _SETTING_SOURCES)
    if raw:
        try:
            saved = json.loads(raw)
            for ct, cfg in saved.items():
                if ct in out and isinstance(cfg, dict):
                    channel = _source_channel(ct, cfg.get("channel"), out[ct]["channel"])
                    out[ct] = {"channel": channel,
                               "bank": cfg.get("bank", out[ct]["bank"])}
        except (ValueError, TypeError):
            pass
    return out


def set_sources(conn: sqlite3.Connection, sources: dict) -> dict:
    """Validate and persist the source config. Unknown channels fall back to the
    default so collection never points at a non-existent channel. Returns the
    stored config."""
    clean = {}
    for ct, default in _DEFAULT_SOURCES.items():
        cfg = sources.get(ct) or {}
        channel = _source_channel(ct, cfg.get("channel"), default["channel"])
        clean[ct] = {"channel": channel, "bank": cfg.get("bank") or default["bank"]}
    set_watermark(conn, _SETTING_SOURCES, json.dumps(clean, ensure_ascii=False))
    conn.commit()
    return clean


# ---- deposits (进项) ----------------------------------------------------

def insert_deposit(conn, d: dict) -> bool:
    """Insert one AI-parsed income record. Idempotent on source_msg_id; returns
    True if a new row was added."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO deposits
           (source_msg_id, card_last4, txn_time, amount, kind, payer, note, raw_text, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (d["source_msg_id"], d.get("card_last4"), d["txn_time"], d["amount"],
         d.get("kind"), d.get("payer"), d.get("note"), d.get("raw_text"), now_ms()),
    )
    conn.commit()
    return cur.rowcount == 1


def list_deposits(conn, amount=None, since_days=None, limit: int = 500) -> list[dict]:
    """Income records, newest first. Optional exact-amount match (±0.01) and a
    recency window."""
    where, args = [], []
    if amount is not None:
        where.append("abs(amount - ?) < 0.01"); args.append(float(amount))
    if since_days is not None:
        import datetime as _dt
        since = (_dt.datetime.now() - _dt.timedelta(days=int(since_days))).strftime("%Y-%m-%d %H:%M")
        where.append("txn_time >= ?"); args.append(since)
    sql = "SELECT id, source_msg_id, card_last4, txn_time, amount, kind, payer, note, raw_text FROM deposits"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY txn_time DESC LIMIT ?"; args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---- queries for the web console ---------------------------------------

def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT name, key, descr FROM categories WHERE active = 1 ORDER BY sort"
    ).fetchall()
    return [dict(r) for r in rows]


def update_category(conn, key: str, name=None, descr=None) -> dict:
    """Edit a category's display name and/or description. Renaming the name is
    FK-safe: every reference (transactions, rules, knowledge) is migrated."""
    row = conn.execute("SELECT name FROM categories WHERE key=?", (key,)).fetchone()
    if not row:
        return {"ok": False, "error": "未找到该分类"}
    old = row["name"]
    if name and name != old:
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("UPDATE categories SET name=? WHERE key=?", (name, key))
        for t in ("transactions", "knowledge"):
            conn.execute(f"UPDATE {t} SET category=? WHERE category=?", (name, old))
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
    if descr is not None:
        conn.execute("UPDATE categories SET descr=? WHERE key=?", (descr, key))
        conn.commit()
    return {"ok": True}


def add_category(conn, name: str, descr: str = "") -> dict:
    import uuid
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "名称不能为空"}
    if conn.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone():
        return {"ok": False, "error": "已存在同名分类"}
    key = "u" + uuid.uuid4().hex[:8]
    nxt = conn.execute("SELECT COALESCE(MAX(sort),0)+1 FROM categories").fetchone()[0]
    conn.execute(
        "INSERT INTO categories (name, key, descr, active, sort) VALUES (?,?,?,1,?)",
        (name, key, descr or "", nxt),
    )
    conn.commit()
    return {"ok": True, "key": key}


def delete_category(conn, key: str) -> dict:
    """Delete a category, but only when no transaction (even voided ones) is
    filed under it. References in knowledge are cleared first so the FK holds;
    those notes keep their text, just lose the category tag."""
    row = conn.execute("SELECT name FROM categories WHERE key=?", (key,)).fetchone()
    if not row:
        return {"ok": False, "error": "未找到该分类"}
    name = row["name"]
    # Only live transactions count; voided rows (offset/merge/split leftovers) are
    # excluded everywhere else, so they shouldn't block deletion.
    n = conn.execute(
        "SELECT count(*) FROM transactions WHERE category=? AND voided=0", (name,)
    ).fetchone()[0]
    if n:
        return {"ok": False, "error": f"还有 {n} 笔交易归在「{name}」，不能删除"}
    # Clear the tag from voided rows + knowledge so the FK holds.
    conn.execute("UPDATE transactions SET category=NULL WHERE category=? AND voided=1", (name,))
    conn.execute("UPDATE knowledge SET category=NULL WHERE category=?", (name,))
    conn.execute("DELETE FROM categories WHERE key=?", (key,))
    conn.commit()
    return {"ok": True}


def delete_knowledge(conn, kid: int) -> None:
    conn.execute("DELETE FROM knowledge WHERE id=?", (kid,))
    conn.commit()


def update_knowledge(conn, kid: int, text: str, category=None) -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "内容不能为空"}
    conn.execute(
        "UPDATE knowledge SET text=?, category=?, updated_at=? WHERE id=?",
        (text, category or None, now_ms(), kid),
    )
    conn.commit()
    return {"ok": True}


def months(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {_period_sql()} m FROM transactions ORDER BY m DESC"
    ).fetchall()
    return [r["m"] for r in rows]


def list_transactions(conn, *, month=None, year=None, card=None, status=None,
                      category=None, q=None, merchant_key=None, limit=200, offset=0) -> list[dict]:
    where, args = [], []
    # Offsetting refund pairs are kept out of the ledger unless asked for.
    if status == "void":
        where.append("voided = 1")
    else:
        where.append("voided = 0")
        if status:
            where.append("status = ?"); args.append(status)
    if merchant_key:
        where.append("merchant_key = ?"); args.append(merchant_key)
    if month:
        where.append(f"{_month_expr()} = ?"); args.append(month)
    if year:
        where.append(f"substr({_month_expr()},1,4) = ?"); args.append(year)
    if card:
        where.append("card_type = ?"); args.append(card)
    if category == "__uncat__":
        where.append("category IS NULL")
    elif category:
        where.append("category = ?"); args.append(category)
    if q:
        where.append("merchant_raw LIKE ?"); args.append(f"%{q}%")
    sql = "SELECT * FROM transactions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY txn_time DESC, id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


_SPEND_DIRECTIONS = "('expense','refund','fee','withdraw')"

# Billing period start day. 1 = calendar month (default, fully back-compatible).
# A value like 15 means each period runs from the 15th to the 14th of next month.
# Clamped to [1, 28] so the start day exists in every month (Feb has no 29-31).
_BILLING_START_DAY = 1


def set_billing_start_day(day) -> None:
    """Configure the billing period start day. Out-of-range/garbage falls back
    to a clamped value so reporting never breaks on a bad config."""
    global _BILLING_START_DAY
    try:
        d = int(day)
    except (TypeError, ValueError):
        d = 1
    _BILLING_START_DAY = max(1, min(28, d))


def get_billing_start_day() -> int:
    """The effective (clamped) billing period start day."""
    return _BILLING_START_DAY


# Persisted in sync_state so the web UI can change it live (DB wins over the
# config.toml default once set).
_SETTING_BILLING = "billing_start_day"


def load_billing_start_day(conn) -> int:
    """Apply the DB-persisted start day to the in-process setting, if present.
    An absent key leaves the current value (e.g. the config.toml default) intact."""
    v = get_watermark(conn, _SETTING_BILLING)
    if v is not None:
        set_billing_start_day(v)
    return _BILLING_START_DAY


def save_billing_start_day(conn, day) -> int:
    """Clamp, persist to the DB, and update the in-process setting. Returns the
    effective (clamped) value so callers can reflect what was actually stored."""
    set_billing_start_day(day)
    set_watermark(conn, _SETTING_BILLING, str(_BILLING_START_DAY))
    conn.commit()
    return _BILLING_START_DAY


def _period_sql(col: str = "txn_time") -> str:
    """SQL fragment yielding a transaction's billing period as 'YYYY-MM'.
    With start day 1 this is identical to substr(col,1,7) (calendar month);
    otherwise the date is shifted back (start_day-1) days before bucketing."""
    if _BILLING_START_DAY <= 1:
        return f"substr({col},1,7)"
    return f"substr(date({col},'-{_BILLING_START_DAY - 1} days'),1,7)"


def _month_expr() -> str:
    """Which period a transaction counts in: an explicit override, else its own
    billing period."""
    return f"COALESCE(effective_month, {_period_sql()})"


def period_of(txn_time: str) -> str:
    """Python mirror of _period_sql: the billing period 'YYYY-MM' a timestamp
    ('YYYY-MM-DD ...') falls in. Used for cross-period detection and merge
    attribution so they match the SQL bucketing."""
    if _BILLING_START_DAY <= 1:
        return txn_time[:7]
    from datetime import datetime, timedelta
    d = datetime.strptime(txn_time[:10], "%Y-%m-%d") - timedelta(days=_BILLING_START_DAY - 1)
    return d.strftime("%Y-%m")


def summary(conn, month=None) -> dict:
    mw = f"AND {_month_expr()}=?" if month else ""
    a = [month] if month else []
    total = conn.execute(
        f"SELECT count(*) n FROM transactions WHERE voided=0 {mw}", a).fetchone()["n"]
    pending = conn.execute(
        f"SELECT count(*) n FROM transactions WHERE voided=0 "
        f"AND status IN ('pending','suggested') {mw}", a).fetchone()["n"]
    # signed sum: genuine refunds reduce spend; offsetting pairs are already voided.
    spend = conn.execute(
        f"SELECT round(sum(amount),2) s FROM transactions "
        f"WHERE voided=0 AND direction IN {_SPEND_DIRECTIONS} {mw}", a
    ).fetchone()["s"] or 0.0
    return {"total": total, "pending": pending, "spend": spend}


def category_stats(conn) -> dict:
    """All-time per-category {n, spend} over valid transactions only (voided
    rows — offsets/splits parents — excluded). Same signed-sum spend convention
    as the monthly view, so refunds net out. Keyed by category name."""
    rows = conn.execute(
        f"""SELECT category c, count(*) n, round(sum(amount),2) spend
            FROM transactions
            WHERE voided=0 AND direction IN {_SPEND_DIRECTIONS} AND category IS NOT NULL
            GROUP BY category"""
    ).fetchall()
    return {r["c"]: {"n": r["n"], "spend": r["spend"] or 0.0} for r in rows}


def monthly_matrix(conn) -> dict:
    rows = conn.execute(
        f"""SELECT {_month_expr()} m, COALESCE(category,'__uncat__') c,
                   round(sum(amount),2) spend, count(*) n
            FROM transactions
            WHERE voided=0 AND direction IN {_SPEND_DIRECTIONS}
            GROUP BY m, c ORDER BY m DESC"""
    ).fetchall()
    return {"cells": [dict(r) for r in rows]}


# Annual view starts here; earlier years are out of scope.
ANNUAL_FROM = "2026"


def annual_matrix(conn) -> dict:
    rows = conn.execute(
        f"""SELECT substr({_month_expr()},1,4) y, COALESCE(category,'__uncat__') c,
                   round(sum(amount),2) spend, count(*) n
            FROM transactions
            WHERE voided=0 AND direction IN {_SPEND_DIRECTIONS}
                  AND substr({_month_expr()},1,4) >= ?
            GROUP BY y, c ORDER BY y DESC""",
        (ANNUAL_FROM,),
    ).fetchall()
    return {"cells": [dict(r) for r in rows]}


def pending_merchants(conn) -> list[dict]:
    """Merchants still needing the user: untouched (pending) or AI-suggested
    (suggested). `suggested` carries the AI's guess for the user to confirm."""
    rows = conn.execute(
        """SELECT merchant_key,
                  count(*) n,
                  round(sum(CASE WHEN amount>0 THEN amount ELSE 0 END),2) spend,
                  max(merchant_raw) sample,
                  max(card_type) card,
                  max(CASE WHEN status='suggested' THEN category END) suggested
           FROM transactions
           WHERE status IN ('pending','suggested') AND voided=0 AND merchant_key IS NOT NULL
           GROUP BY merchant_key ORDER BY n DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def list_knowledge(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, scope, merchant_key, text, category FROM knowledge ORDER BY updated_at DESC"
    ).fetchall()]


# ---- writes from the console -------------------------------------------

def set_category(conn, txn_id: int, category: str) -> dict:
    """Set the category on a single transaction (a manual confirm). One-off — it
    touches only this row, never other rows or any rule."""
    row = conn.execute(
        "SELECT id FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": "not found"}
    conn.execute(
        """UPDATE transactions SET category=?, category_src='manual',
           status='confirmed', updated_at=? WHERE id=?""",
        (category, now_ms(), txn_id),
    )
    conn.commit()
    return {"ok": True}


def suggest_merchant(conn, merchant_key: str, category: str) -> int:
    """Record/refresh an AI suggestion for a merchant. Updates both untouched
    (pending) and previously-suggested rows so re-running the classifier applies
    the latest knowledge. Confirmed rows are never touched."""
    cur = conn.execute(
        """UPDATE transactions SET category=?, category_src='hermes', status='suggested',
           updated_at=? WHERE merchant_key=? AND status IN ('pending','suggested')""",
        (category, now_ms(), merchant_key),
    )
    conn.commit()
    return cur.rowcount


def clear_suggestion(conn, merchant_key: str) -> int:
    """Revert a prior AI suggestion back to pending (e.g. the AI now says it
    can't decide). Confirmed rows are untouched."""
    cur = conn.execute(
        """UPDATE transactions SET category=NULL, category_src=NULL, status='pending',
           updated_at=? WHERE merchant_key=? AND status='suggested'""",
        (now_ms(), merchant_key),
    )
    conn.commit()
    return cur.rowcount


def categorize_merchant(conn, merchant_key: str, category: str) -> dict:
    """Confirm a whole merchant: apply the category to every pending or
    AI-suggested transaction of that merchant. One-off — creates no rule."""
    cur = conn.execute(
        """UPDATE transactions SET category=?, category_src='manual', status='confirmed',
           updated_at=? WHERE merchant_key=? AND status IN ('pending','suggested')""",
        (category, now_ms(), merchant_key),
    )
    conn.commit()
    return {"ok": True, "applied": cur.rowcount}


def split_transaction(conn, txn_id: int, amounts: list) -> dict:
    """Split one merged charge into several child transactions whose amounts sum
    to the original. The parent is voided; children inherit card/time/merchant
    and start unclassified. Then refund auto-offset runs, so a child that equals
    a later refund (same card+merchant) cancels out."""
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "未找到交易"}
    if row["voided"]:
        return {"ok": False, "error": "该交易已作废/已拆分"}
    amounts = [round(float(a), 2) for a in amounts]
    if len(amounts) < 2:
        return {"ok": False, "error": "至少拆成 2 份"}
    if abs(sum(amounts) - round(row["amount"], 2)) > 0.01:
        return {"ok": False, "error": f"拆分合计 {sum(amounts):.2f} ≠ 原金额 {row['amount']:.2f}"}

    for i, amt in enumerate(amounts):
        conn.execute(
            """INSERT INTO transactions
               (fingerprint, source_msg_id, card_last4, card_type, txn_time, amount,
                currency, direction, action, merchant_raw, merchant_key, channel,
                status, note, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"{row['fingerprint']}#s{i}", row["source_msg_id"], row["card_last4"],
             row["card_type"], row["txn_time"], amt, row["currency"], row["direction"],
             row["action"], row["merchant_raw"], row["merchant_key"], row["channel"],
             "pending", f"拆自 #{txn_id}", now_ms(), now_ms()),
        )
    conn.execute(
        "UPDATE transactions SET voided=1, note=?, updated_at=? WHERE id=?",
        ((row["note"] or "") + " [已拆分]", now_ms(), txn_id),
    )
    conn.commit()
    paired = pair_offsetting_refunds(conn)
    return {"ok": True, "parts": len(amounts), "offset_pairs": paired}


def merge_candidates(conn, min_refund: float = 10.0) -> list[dict]:
    """Flag merges that actually change the books: a CROSS-MONTH expense + later
    refund at the same card+merchant (deposit return / a return refunded next
    month). Same-month partial refunds already net within their month/category,
    so they're not flagged; trivial refunds (< min_refund, e.g. 抹零) are skipped.
    The user confirms each before merging."""
    out = []
    refunds = conn.execute(
        "SELECT * FROM transactions WHERE direction='refund' AND voided=0 ORDER BY txn_time"
    ).fetchall()
    for r in refunds:
        if abs(r["amount"]) < min_refund:
            continue
        m = conn.execute(
            """SELECT * FROM transactions WHERE direction='expense' AND voided=0
               AND card_last4=? AND merchant_key=? AND txn_time<=? AND id!=?
               AND amount >= ? ORDER BY txn_time DESC LIMIT 1""",
            (r["card_last4"], r["merchant_key"], r["txn_time"], r["id"], abs(r["amount"])),
        ).fetchone()
        if not m or period_of(m["txn_time"]) == period_of(r["txn_time"]):  # only cross-period matters
            continue
        out.append({
            "merchant_key": r["merchant_key"],
            "expense": {"id": m["id"], "amount": m["amount"], "time": m["txn_time"]},
            "refund": {"id": r["id"], "amount": r["amount"], "time": r["txn_time"]},
            "net": round(m["amount"] + r["amount"], 2),
            "cross_month": True,
        })
    return out


def merge_transactions(conn, ids: list, category=None, note=None, month=None) -> dict:
    """Combine several transactions into one net transaction (e.g. deposit +
    cross-month refund). Originals are voided; the merged row carries the net
    amount and an effective_month so it counts in the chosen month."""
    import hashlib
    rows = []
    for i in ids:
        r = conn.execute("SELECT * FROM transactions WHERE id=? AND voided=0", (i,)).fetchone()
        if r:
            rows.append(r)
    if len(rows) < 2:
        return {"ok": False, "error": "至少选 2 笔未作废的交易"}

    net = round(sum(r["amount"] for r in rows), 2)

    if abs(net) < 0.01:  # exact offset (e.g. buy + full refund, even cross-merchant) — just void
        for r in rows:
            conn.execute(
                "UPDATE transactions SET voided=1, note=?, updated_at=? WHERE id=?",
                ((r["note"] or "") + " [已抵消]", now_ms(), r["id"]),
            )
        conn.commit()
        return {"ok": True, "merged_id": None, "net": 0.0, "month": None,
                "count": len(rows), "offset": True}

    first = min(rows, key=lambda r: r["txn_time"])          # earliest, for the record's time
    months = sorted({period_of(r["txn_time"]) for r in rows})
    eff = month or months[0]
    direction = "refund" if net < 0 else "expense"
    status = "confirmed" if category else "pending"
    src = "manual" if category else None
    fp = "merge:" + hashlib.sha1(
        ("|".join(map(str, sorted(ids))) + str(now_ms())).encode()).hexdigest()

    cur = conn.execute(
        """INSERT INTO transactions
           (fingerprint, source_msg_id, card_last4, card_type, txn_time, amount, currency,
            direction, action, merchant_raw, merchant_key, channel, category, category_src,
            status, note, effective_month, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fp, first["source_msg_id"], first["card_last4"], first["card_type"], first["txn_time"],
         net, first["currency"], direction, "合并", first["merchant_raw"], first["merchant_key"],
         first["channel"], category, src, status, note, eff, now_ms(), now_ms()),
    )
    merged_id = cur.lastrowid
    for r in rows:
        conn.execute(
            "UPDATE transactions SET voided=1, merged_into=?, note=?, updated_at=? WHERE id=?",
            (merged_id, (r["note"] or "") + " [已合并]", now_ms(), r["id"]),
        )
    conn.commit()
    return {"ok": True, "merged_id": merged_id, "net": net, "month": eff, "count": len(rows)}


def set_note(conn, txn_id: int, note: str) -> None:
    conn.execute("UPDATE transactions SET note=?, updated_at=? WHERE id=?",
                 (note, now_ms(), txn_id))
    conn.commit()


def confirm_txn(conn, txn_id: int) -> None:
    conn.execute("UPDATE transactions SET status='confirmed', updated_at=? WHERE id=?",
                 (now_ms(), txn_id))
    conn.commit()


def set_voided(conn, txn_id: int, voided: bool) -> None:
    """Soft-remove (voided=True) or restore (False) a single transaction. A
    voided row drops out of every spend stat (all queries filter voided=0) but
    stays in the table — viewable under the void filter and reversible. Used for
    things that shouldn't count as your spending: reimbursed expenses, transfers
    between your own accounts, mistakes."""
    conn.execute("UPDATE transactions SET voided=?, updated_at=? WHERE id=?",
                 (1 if voided else 0, now_ms(), txn_id))
    conn.commit()


def add_knowledge(conn, scope: str, text: str, merchant_key=None, category=None) -> None:
    conn.execute(
        """INSERT INTO knowledge (scope, merchant_key, text, category, created_at, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (scope, merchant_key, text, category, now_ms(), now_ms()),
    )
    conn.commit()
