"""Plutus as an MCP server (stdio, JSON-RPC 2.0), so the hermes agent can read
and act on the ledger when the user chats on WeChat.

This is a THIN CLIENT: it owns no data and never opens a database. Every tool
calls the launchd-managed Plutus web service over local HTTP. So the agent only
ever touches the curated API surface, never the raw plutus.db.

Stdlib only. Messages are newline-delimited JSON per the MCP stdio transport.
Launched by hermes; paths resolve against the project root.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import config as cfgmod

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = "2025-06-18"


def _base() -> str:
    try:
        cfg = cfgmod.load(str(ROOT / "config.toml"))
    except FileNotFoundError:
        cfg = {}
    port = int(cfg.get("web", {}).get("port", 8973))
    return f"http://127.0.0.1:{port}"


# -- HTTP client to the Plutus web service --------------------------------

def _req(method: str, path: str, body=None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(_base() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # endpoint replied with an error body
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            raise RuntimeError(f"Plutus 服务返回 {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 Plutus 服务（web 在运行吗？）：{e.reason}")


def _get(path: str) -> dict:
    return _req("GET", path)


def _post(path: str, body: dict) -> dict:
    return _req("POST", path, body)


def _categories() -> list[str]:
    try:
        return [c["name"] for c in _get("/api/bootstrap?month=").get("categories", [])]
    except Exception:
        return []


def _fmt_txn(t: dict) -> str:
    cat = t.get("category") or "未分类"
    return (f"#{t['id']} {t['txn_time']} {t['card_type']}{t['card_last4']} "
            f"¥{t['amount']} {t['merchant_raw']} [{cat}/{t['status']}]")


# -- tool schemas ---------------------------------------------------------

def tool_definitions() -> list[dict]:
    cats = "、".join(_categories())
    return [
        {
            "name": "list_pending",
            "description": "列出待分类（pending）的交易，供你逐笔向用户确认或纠正。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "month": {"type": "string", "description": "YYYY-MM，可选，限定月份"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 20"},
                },
            },
        },
        {
            "name": "set_category",
            "description": (
                "为某一笔交易设定/纠正分类（用户在微信里确认或纠正某笔时调用）。"
                f"category 必须是以下之一：{cats}。"
                "只改这一笔、不建任何规则。"
                "若用户想让“以后某商户都算某类”，那是知识，应改用 teach 工具记进记忆。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "integer"},
                    "category": {"type": "string"},
                },
                "required": ["transaction_id", "category"],
            },
        },
        {
            "name": "confirm",
            "description": "确认某笔交易当前分类正确、无需修改。",
            "inputSchema": {
                "type": "object",
                "properties": {"transaction_id": {"type": "integer"}},
                "required": ["transaction_id"],
            },
        },
        {
            "name": "add_note",
            "description": "给某笔交易加一条用户备注。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["transaction_id", "note"],
            },
        },
        {
            "name": "teach",
            "description": (
                "记录用户教给系统的知识，作为以后分类的上下文。"
                "scope='merchant' 时配 merchant_key 把知识绑定到某商户；"
                "scope='global' 是通用规则。可选 category 表示该知识暗示的分类。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "scope": {"type": "string", "enum": ["merchant", "global"]},
                    "merchant_key": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "monthly_summary",
            "description": "返回某月各分类的支出合计与笔数。",
            "inputSchema": {
                "type": "object",
                "properties": {"month": {"type": "string", "description": "YYYY-MM"}},
            },
        },
        {
            "name": "list_categories",
            "description": "返回可用的分类清单（设定分类时必须从中选取）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "find_deposits",
            "description": (
                "查询从招行一卡通账户变动邮件识别的入账（如报销款/工资），"
                "用于核对垫付/报销是否到账。传 amount 只返回等额入账；不传则列出最近入账。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "只返回等于此金额的入账（核对某笔垫付是否报销到账）"},
                    "since_days": {"type": "integer", "description": "往回查多少天，默认 30"},
                },
            },
        },
    ]


# -- tool implementations (all via the web service) -----------------------

def call_tool(name: str, args: dict) -> str:
    if name == "list_pending":
        q = f"/api/transactions?status=pending&limit={int(args.get('limit', 20))}"
        if args.get("month"):
            q += f"&month={args['month']}"
        rows = _get(q).get("rows", [])
        if not rows:
            return "没有待分类的交易。"
        return f"待分类 {len(rows)} 笔：\n" + "\n".join(_fmt_txn(t) for t in rows)

    if name == "set_category":
        cat = args["category"]
        valid = set(_categories())
        if valid and cat not in valid:
            return f"无效分类「{cat}」。可用分类：{ '、'.join(sorted(valid)) }"
        res = _post(f"/api/transactions/{int(args['transaction_id'])}/category", {"category": cat})
        if not res.get("ok"):
            return f"未找到交易 #{args['transaction_id']}。"
        return f"已将 #{args['transaction_id']} 设为「{cat}」。"

    if name == "confirm":
        _post(f"/api/transactions/{int(args['transaction_id'])}/confirm", {})
        return f"已确认 #{args['transaction_id']}。"

    if name == "add_note":
        _post(f"/api/transactions/{int(args['transaction_id'])}/note", {"note": args.get("note", "")})
        return f"已为 #{args['transaction_id']} 添加备注。"

    if name == "teach":
        _post("/api/knowledge", {
            "scope": args.get("scope", "global"), "text": args["text"],
            "merchant_key": args.get("merchant_key"), "category": args.get("category"),
        })
        return "已记录这条知识，以后分类会参考它。"

    if name == "monthly_summary":
        month = args.get("month")
        cells = _get("/api/monthly").get("cells", [])
        agg: dict = {}
        for x in cells:
            if month and x["m"] != month:
                continue
            cat = "未分类" if x["c"] == "__uncat__" else x["c"]
            a = agg.setdefault(cat, {"spend": 0.0, "n": 0})
            a["spend"] += x.get("spend") or 0.0
            a["n"] += x.get("n") or 0
        rows = sorted(((c, v) for c, v in agg.items() if abs(v["spend"]) > 0.004),
                      key=lambda kv: kv[1]["spend"], reverse=True)
        head = f"{month or '全部'} 分类汇总：\n"
        return head + "\n".join(f"  {c}: ¥{round(v['spend'], 2)}（{v['n']} 笔）" for c, v in rows)

    if name == "list_categories":
        return "可用分类：" + "、".join(_categories())

    if name == "find_deposits":
        amount = args.get("amount")
        since_days = int(args.get("since_days", 30))
        q = f"/api/deposits?since_days={since_days}"
        if amount is not None:
            q += f"&amount={amount}"
        res = _get(q)
        if "error" in res:
            return f"查询进项出错：{res['error']}"
        deps = res.get("rows", [])
        if not deps:
            amt = f" ¥{float(amount):.2f} 的" if amount is not None else ""
            return f"最近 {since_days} 天没有{amt}进项记录。"
        head = (f"找到 {len(deps)} 笔"
                + (f"匹配 ¥{float(amount):.2f} 的" if amount is not None else "")
                + "进项：\n")

        def _label(d):
            bits = [d.get("kind") or "进项"]
            if d.get("payer"):
                bits.append(d["payer"])
            return "·".join(bits)
        return head + "\n".join(
            f"- {d['txn_time']} 卡{d.get('card_last4') or '?'} {_label(d)} ¥{d['amount']:.2f}"
            for d in deps)

    return f"未知工具：{name}"


# -- JSON-RPC stdio loop --------------------------------------------------

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id, result) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code, message) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle(msg: dict) -> None:
    method = msg.get("method")
    req_id = msg.get("id")

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "plutus", "version": "0.1.0"},
        })
    elif method in ("notifications/initialized", "initialized"):
        pass  # notification, no reply
    elif method == "ping":
        _result(req_id, {})
    elif method == "tools/list":
        _result(req_id, {"tools": tool_definitions()})
    elif method == "tools/call":
        params = msg.get("params", {})
        try:
            text = call_tool(params.get("name", ""), params.get("arguments", {}) or {})
            _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:  # surface errors to the agent, do not crash
            _result(req_id, {"content": [{"type": "text", "text": f"错误：{exc}"}],
                             "isError": True})
    elif req_id is not None:
        _error(req_id, -32601, f"method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()
