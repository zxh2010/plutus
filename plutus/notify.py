"""Push newly-ingested transactions to WeChat via the hermes gateway.

A transaction is "new" until it has been notified (notify_status set). The
backfilled history is marked 'backfill' so it is never pushed. After a successful
push the rows are marked 'sent'. Replies/confirmations happen on WeChat via the
hermes agent calling the Plutus MCP tools.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from . import store

UNCERTAIN_STATUSES = {"pending", "suggested"}
WECHAT_PROBE_MESSAGE = "Plutus 微信通道测试成功。"
OPERATION_LABELS = {
    "merge": "合并",
    "offset": "抵消",
    "void": "不计入",
    "split": "拆分",
}


def _card(ct: str, last4: str) -> str:
    return f"{'信用卡' if ct == 'credit' else '借记卡'}{last4}"


def format_message(rows: list) -> str:
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["card_type"], r["card_last4"]), []).append(r)

    lines = [f"📒 Plutus · 新交易 {len(rows)} 笔"]
    need_confirm = 0
    shown_advice = set()
    for (ct, last4), items in groups.items():
        lines.append(f"\n{_card(ct, last4)}")
        for r in items:
            uncertain = r["status"] in UNCERTAIN_STATUSES
            if r["category"]:
                tag = f"→ {r['category']}" + ("（AI建议，请确认）" if uncertain else "")
            else:
                tag = "→ ❓待分类"
            if uncertain:
                need_confirm += 1
            merchant = (r["merchant_raw"] or "")[:18]
            lines.append(f" #{r['id']} ¥{r['amount']:.2f} {merchant} {tag}")
            operation = r["suggested_operation"] if "suggested_operation" in r.keys() else None
            related_json = r["suggested_related_ids"] if "suggested_related_ids" in r.keys() else None
            if operation in OPERATION_LABELS and related_json:
                try:
                    related_ids = [int(value) for value in json.loads(related_json)]
                except (TypeError, ValueError, json.JSONDecodeError):
                    related_ids = []
                advice_key = (operation, tuple(related_ids))
                if related_ids and advice_key not in shown_advice:
                    shown_advice.add(advice_key)
                    id_text = " ".join(f"#{value}" for value in related_ids)
                    reason = r["suggested_reason"] or "Hermes 识别到可能的账务操作"
                    lines.append(
                        f"   💡 建议{OPERATION_LABELS[operation]} {id_text}：{reason}"
                    )
                    lines.append(
                        f"   如需执行，请回复“确认{OPERATION_LABELS[operation]} {id_text}”"
                    )
    if need_confirm:
        lines.append(f"\n其中 {need_confirm} 笔需你确认。回复即可改，例：「#{rows[0]['id']} 改成孩子相关」")
    return "\n".join(lines)


def send_wechat(cfg: dict, text: str) -> bool:
    n = cfg.get("notify", {})
    hermes = os.path.expanduser(n.get("hermes_bin", "~/.local/bin/hermes"))
    target = n["weixin_target"]
    r = subprocess.run([hermes, "send", "--to", target, text],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _command_error(result) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    return detail[-400:] if detail else f"Hermes exited with code {result.returncode}"


def check_wechat(cfg: dict) -> dict:
    """Verify the configured Hermes WeChat target and send one probe message."""
    started = time.monotonic()
    n = cfg.get("notify", {})
    raw_bin = (n.get("hermes_bin") or "").strip()
    target = (n.get("weixin_target") or "").strip()
    if not raw_bin:
        return {"ok": False, "error": "Hermes 可执行文件未配置",
                "elapsed_ms": _elapsed_ms(started)}
    if not target:
        return {"ok": False, "error": "微信目标未配置",
                "elapsed_ms": _elapsed_ms(started)}

    hermes = os.path.expanduser(raw_bin)
    if not os.path.isfile(hermes) or not os.access(hermes, os.X_OK):
        return {"ok": False, "error": f"Hermes 不可执行：{hermes}",
                "elapsed_ms": _elapsed_ms(started)}

    try:
        listed = subprocess.run(
            [hermes, "send", "--list", "weixin", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if listed.returncode != 0:
            return {"ok": False, "error": _command_error(listed),
                    "elapsed_ms": _elapsed_ms(started)}
        payload = json.loads(listed.stdout)
        ids = {
            str(item.get("id") or "")
            for item in payload.get("platforms", {}).get("weixin", [])
            if isinstance(item, dict)
        }
        target_id = target.removeprefix("weixin:")
        if target_id not in ids:
            return {"ok": False, "registered": False,
                    "error": "配置的微信目标不在 Hermes 可用目标中",
                    "elapsed_ms": _elapsed_ms(started)}

        sent = subprocess.run(
            [hermes, "send", "--to", target, WECHAT_PROBE_MESSAGE],
            capture_output=True, text=True, timeout=60,
        )
        if sent.returncode != 0:
            return {"ok": False, "registered": True,
                    "error": _command_error(sent),
                    "elapsed_ms": _elapsed_ms(started)}
        return {"ok": True, "registered": True,
                "elapsed_ms": _elapsed_ms(started)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Hermes 微信通道校验超时",
                "elapsed_ms": _elapsed_ms(started)}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Hermes 返回异常：{exc}",
                "elapsed_ms": _elapsed_ms(started)}


def notify_new(conn, cfg: dict, dry: bool = False) -> dict:
    rows = conn.execute(
        """SELECT t.*, s.operation AS suggested_operation,
                  s.related_transaction_ids AS suggested_related_ids,
                  s.reason AS suggested_reason
           FROM transactions t
           LEFT JOIN operation_suggestions s ON s.transaction_id=t.id
           WHERE t.notify_status IS NULL AND t.voided=0 """
        "ORDER BY card_type, txn_time"
    ).fetchall()
    if not rows:
        return {"notified": 0, "text": ""}
    text = format_message(rows)
    if dry:
        return {"notified": 0, "text": text, "dry": True}
    if not send_wechat(cfg, text):
        return {"notified": 0, "text": text, "error": "send failed"}
    now = store.now_ms()
    conn.executemany(
        "UPDATE transactions SET notify_status='sent', notify_channel='weixin', "
        "notified_at=? WHERE id=?",
        [(now, r["id"]) for r in rows],
    )
    conn.commit()
    return {"notified": len(rows), "text": text}
