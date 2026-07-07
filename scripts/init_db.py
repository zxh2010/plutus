#!/usr/bin/env python3
"""Initialize the Plutus SQLite database and seed the fixed category list.

Usage:
    python scripts/init_db.py [--db plutus.db]

Idempotent: safe to re-run. Uses only the standard library.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema.sql"
CATEGORIES = ROOT / "data" / "categories.json"


def init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        seed_categories(conn)
        conn.commit()
    finally:
        conn.close()


def seed_categories(conn: sqlite3.Connection) -> None:
    cats = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    now = int(time.time())
    for sort, c in enumerate(cats):
        # Upsert by stable key so renames of display name stay attached.
        conn.execute(
            """
            INSERT INTO categories (name, key, descr, active, sort)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(key) DO UPDATE SET
                name = excluded.name,
                descr = excluded.descr,
                sort = excluded.sort
            """,
            (c["name"], c["key"], c.get("descr", ""), sort),
        )
    _ = now  # reserved for future audit columns


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "plutus.db"))
    args = ap.parse_args()
    db_path = Path(args.db)
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    cats = conn.execute("SELECT key, name FROM categories ORDER BY sort").fetchall()
    conn.close()
    print(f"DB ready at {db_path}")
    print("Tables:", ", ".join(tables))
    print("Categories:")
    for key, name in cats:
        print(f"  - {key:12s} {name}")


if __name__ == "__main__":
    main()
