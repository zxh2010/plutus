"""AI classification via the local hermes agent.

Classification is AI-driven, not hard string-matching: the category list with
descriptions plus everything the user has taught (merchant/keyword facts and
free knowledge) are handed to hermes as context, and hermes decides. The result
is only a *suggestion* — nothing is booked until the user confirms it in the
console or over WeChat. Uncertain merchants come back as "不确定" and are left
for the user.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from . import store

HERMES = os.path.expanduser("~/.local/bin/hermes")
UNCERTAIN = "不确定"


def build_context(conn) -> str:
    cats = conn.execute(
        "SELECT name, descr FROM categories WHERE active=1 ORDER BY sort"
    ).fetchall()
    parts = ["分类清单（含说明）：",
             *[f"- {r['name']}：{r['descr']}" for r in cats]]

    # Memory = only what the user has taught (the knowledge table). Confirmations
    # in 待分类 are ephemeral and never enter the context.
    facts = []
    for r in conn.execute("SELECT text, category FROM knowledge"):
        tail = f"（{r['category']}）" if r["category"] else ""
        facts.append(f"- {r['text']}{tail}")
    if facts:
        parts += ["", "已知事实（用户教过的，优先参考）：", *facts]
    return "\n".join(parts)


def _call_hermes(prompt: str, timeout: int = 300) -> str:
    r = subprocess.run([HERMES, "-z", prompt], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def amount_hint(amounts: list[float]) -> str:
    """A short money hint per merchant so amount-dependent rules can apply."""
    ds = sorted({round(a, 2) for a in amounts})
    n = len(amounts)
    if len(ds) == 1:
        return f"{n}笔，金额固定{ds[0]:.2f}"
    if len(ds) <= 4:
        return f"{n}笔，金额{'/'.join(f'{x:.2f}' for x in ds)}"
    return f"{n}笔，金额{ds[0]:.2f}~{ds[-1]:.2f}不等"


def classify_merchants(context: str, items: list) -> dict:
    """items: list of (name, hint). Returns {name: category}."""
    lines = "\n".join(f"- {name}（{hint}）" if hint else f"- {name}" for name, hint in items)
    prompt = (
        "你是记账分类助手。根据下面的分类清单和已知事实，为每个商户判断它属于哪个分类。\n"
        "每个商户后括号里是它的笔数/金额提示，请结合它判断（有些规则依赖金额，如某固定金额）。\n"
        f'只输出一个 JSON 对象：键为商户名（括号前的名称原文），值为分类名（必须从清单里精确选一个）；'
        f'若无法有把握判断，值填 "{UNCERTAIN}"。不要输出任何多余文字或代码块标记。\n\n'
        f"{context}\n\n待分类商户：\n{lines}\n"
    )
    return _extract_json(_call_hermes(prompt))


def _pick(res: dict, name: str):
    """Tolerant lookup: the model should key by name, but accept a key that the
    name is a prefix of (in case it echoed the amount hint)."""
    if name in res:
        return res[name]
    for k, v in res.items():
        if k.startswith(name) or name.startswith(k):
            return v
    return None


def suggest_pending(conn, batch_size: int = 40, log=None, include_suggested: bool = True) -> dict:
    """Have the AI suggest a category for unconfirmed merchants and store the
    suggestions for the user to confirm. `log(line)` streams a terminal-style
    trace. include_suggested=True re-evaluates already-suggested merchants too
    (manual "让 AI 评估"); the daemon uses False to only touch brand-new pending."""
    import math
    log = log or (lambda s: None)

    from collections import defaultdict
    amap = defaultdict(list)
    statuses = "('pending','suggested')" if include_suggested else "('pending')"
    for r in conn.execute(
        "SELECT merchant_key, amount FROM transactions "
        f"WHERE status IN {statuses} AND voided=0 AND merchant_key IS NOT NULL"
    ):
        amap[r["merchant_key"]].append(r["amount"])
    merchants = list(amap.keys())
    n = len(merchants)
    if not n:
        log("没有待分类的商户。")
        return {"merchants": 0, "suggested": 0, "uncertain": 0, "decisions": {}}

    nb = math.ceil(n / batch_size)
    log(f"$ plutus classify  —  {n} 个待分类商户，分 {nb} 批")
    context = build_context(conn)
    log(f"# 已载入知识库 context（{len(context)} 字）")
    valid = {x["name"] for x in store.list_categories(conn)}

    decisions: dict[str, str] = {}
    for bi, i in enumerate(range(0, n, batch_size), 1):
        chunk = merchants[i:i + batch_size]
        items = [(mk, amount_hint(amap[mk])) for mk in chunk]
        log("")
        log(f"[{bi}/{nb}] $ hermes -z  ({len(chunk)} 个商户)")
        log("  → " + "、".join(f"{mk}（{amount_hint(amap[mk])}）" for mk in chunk[:5])
            + (" …" if len(chunk) > 5 else ""))
        log("  ⟳ 调用 hermes 推理中…")
        res = classify_merchants(context, items)
        log("  ← " + json.dumps(res, ensure_ascii=False)[:800])
        ok = 0
        for mk in chunk:
            cat = _pick(res, mk)
            decisions[mk] = cat
            if cat in valid:
                ok += 1
        log(f"  ✓ 本批建议 {ok}，不确定 {len(chunk) - ok}")

    suggested = uncertain = 0
    for mk in merchants:
        cat = decisions.get(mk)
        if cat in valid:
            store.suggest_merchant(conn, mk, cat)
            suggested += 1
        else:
            store.clear_suggestion(conn, mk)  # revert any stale suggestion to pending
            uncertain += 1
    log("")
    log(f"✓ 全部完成：建议 {suggested} 个，不确定 {uncertain} 个")
    return {"merchants": n, "suggested": suggested,
            "uncertain": uncertain, "decisions": decisions}


if __name__ == "__main__":
    from .config import load
    conn = store.get_conn(load()["db"]["path"])
    res = suggest_pending(conn)
    print(f"商户 {res['merchants']} 个 -> AI 建议 {res['suggested']} 个，不确定 {res['uncertain']} 个")
