"""Dependency-free web console built on the stdlib http.server.

Serves a single-page app plus a small JSON API over the SQLite ledger. One
SQLite connection is opened per request (simplest thing that is thread-safe).
"""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import classifier, config, gmail_client, notify, store
from ..parsers import registry

STATIC = Path(__file__).resolve().parent / "static"
_CFG = config.load()
_DB = _CFG["db"]["path"]
# Billing-period start day drives the monthly/annual bucketing in store. The
# config.toml value is the initial default; a value saved via the UI (persisted
# in the DB) wins and takes effect without a restart.
store.set_billing_start_day(_CFG.get("stats", {}).get("billing_start_day", 1))
with store.get_conn(_CFG["db"]["path"]) as _c:
    store.load_billing_start_day(_c)


def _conn():
    return store.get_conn(_DB)


# Background AI-suggestion run (it calls hermes per batch and can take minutes,
# so it must not block the HTTP request).
_classify = {"running": False, "result": None, "log": []}


def _run_classify():
    conn = _conn()
    _classify["log"] = []
    try:
        _classify["result"] = classifier.suggest_pending(
            conn, log=lambda line: _classify["log"].append(line))
    except Exception as exc:  # surface to the UI, keep the server alive
        _classify["result"] = {"error": str(exc)}
        _classify["log"].append("✗ " + str(exc))
    finally:
        conn.close()
        _classify["running"] = False


def _mail_provider_options() -> list[dict]:
    return [
        {"key": key, "label": spec["label"]}
        for key, spec in gmail_client.PROVIDERS.items()
    ]


def _mail_self_check(card_type: str = "credit", bank: str = "cmb") -> dict:
    """Live read-only probe for the config page: login, select the provider's
    mailbox, and count recent emails from the parser registered for this card
    and bank. Unsupported pairs stop before connecting so another card's parser
    cannot produce a false positive."""
    import time
    t0 = time.time()
    elapsed = lambda: int((time.time() - t0) * 1000)
    parser = registry.get_email(card_type, bank)
    if parser is None:
        return {
            "ok": False,
            "unsupported": True,
            "error": f"{card_type}/{bank} 邮箱暂无解析器",
            "elapsed_ms": elapsed(),
        }
    try:
        # Read config fresh so a just-added app password is picked up without a
        # web restart (the on-page guide says "授权后点重新自检").
        cfg = config.load()
        mail = gmail_client._mail_cfg(cfg)
        m = gmail_client.connect(cfg)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "elapsed_ms": elapsed()}
    try:
        senders = parser.senders
        q = " OR ".join(f"from:{s}" for s in senders)
        recent = len(gmail_client.search_uids(m, f"({q}) newer_than:7d"))
        return {
            "ok": True,
            "provider": mail["provider"],
            "provider_label": gmail_client.provider_label(mail["provider"]),
            "mailbox": (
                mail.get("mailbox")
                or gmail_client.PROVIDERS[mail["provider"]]["mailbox"]
                or gmail_client.ALL_MAIL
            ),
            "recent_email_7d": recent,
            "elapsed_ms": elapsed(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "elapsed_ms": elapsed()}
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _gmail_self_check(card_type: str = "credit", bank: str = "cmb") -> dict:
    """Backward-compatible name used by older tests/callers."""
    return _mail_self_check(card_type, bank)


class Handler(BaseHTTPRequestHandler):
    server_version = "Plutus/0.1"

    # -- helpers ---------------------------------------------------------
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        if not path.is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(path.suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, *args):  # quieter logs
        pass

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}

        if path == "/" or path == "/index.html":
            return self._send_file(STATIC / "index.html")
        if path.startswith("/static/"):
            return self._send_file(STATIC / path[len("/static/"):])

        if path == "/api/bootstrap":
            with _conn() as c:
                month = qs.get("month") or (store.months(c) or [None])[0]
                return self._send_json({
                    "categories": store.list_categories(c),
                    "months": store.months(c),
                    "month": month,
                    "summary": store.summary(c, month),
                    "total_pending": store.summary(c, None)["pending"],
                    "category_stats": store.category_stats(c),
                    "billing_start_day": store.get_billing_start_day(),
                })
        if path == "/api/transactions":
            with _conn() as c:
                return self._send_json({"rows": store.list_transactions(
                    c,
                    month=qs.get("month"), year=qs.get("year"), card=qs.get("card"),
                    status=qs.get("status"), category=qs.get("category"),
                    q=qs.get("q"), merchant_key=qs.get("merchant_key"),
                    limit=int(qs.get("limit", 300)), offset=int(qs.get("offset", 0)),
                )})
        if path == "/api/deposits":
            # Income is parsed from email by the daemon into the deposits table;
            # this request only reads the stored records.
            amount = qs.get("amount")
            amount = float(amount) if amount not in (None, "") else None
            sd = qs.get("since_days")
            with _conn() as c:
                return self._send_json({"rows": store.list_deposits(
                    c, amount=amount, since_days=int(sd) if sd else None)})
        if path == "/api/monthly":
            with _conn() as c:
                return self._send_json(store.monthly_matrix(c))
        if path == "/api/annual":
            with _conn() as c:
                return self._send_json(store.annual_matrix(c))
        if path == "/api/pending_merchants":
            with _conn() as c:
                return self._send_json({"rows": store.pending_merchants(c)})
        if path == "/api/merge_candidates":
            with _conn() as c:
                return self._send_json({"rows": store.merge_candidates(c)})
        if path == "/api/knowledge":
            with _conn() as c:
                return self._send_json({"rows": store.list_knowledge(c)})
        if path == "/api/context":
            with _conn() as c:
                return self._send_json({"text": classifier.build_context(c)})
        if path == "/api/classify/status":
            return self._send_json(_classify)
        if path == "/api/config":
            cfg = config.load()  # fresh, so the page reflects current auth state
            mail = gmail_client._mail_cfg(cfg)
            n = cfg.get("notify", {})
            with _conn() as c:
                last_credit_email = c.execute(
                    "SELECT max(fetched_at) FROM emails WHERE email_type='credit_daily'"
                ).fetchone()[0]
                last_credit_txn = c.execute(
                    "SELECT max(txn_time) FROM transactions "
                    "WHERE card_type='credit' AND voided=0"
                ).fetchone()[0]
                last_debit_txn = c.execute(
                    "SELECT max(txn_time) FROM transactions "
                    "WHERE card_type='debit' AND voided=0"
                ).fetchone()[0]
                notify_sent, notify_last = c.execute(
                    "SELECT count(*), max(notified_at) FROM transactions "
                    "WHERE notify_status='sent'"
                ).fetchone()
                deposits_n, deposits_last = c.execute(
                    "SELECT count(*), max(txn_time) FROM deposits"
                ).fetchone()
                categories_n = len(store.list_categories(c))
                knowledge_n = len(store.list_knowledge(c))
                last_uid = store.get_watermark(c, "last_uid")
                sources = store.get_sources(c)
            # Report parser availability for each email source.
            channel_support = {
                ct: {"email": registry.has_parser(
                    ct, "email", sources[ct]["bank"]
                )}
                for ct in ("credit", "debit")
            }
            return self._send_json({
                # Provider-aware mail collection. gmail_* fields are retained
                # for compatibility with older UI/tests.
                "mail_provider": mail["provider"],
                "mail_provider_label": gmail_client.provider_label(mail["provider"]),
                "mail_providers": _mail_provider_options(),
                "mail_email": mail.get("email", ""),
                "mail_configured": bool(mail.get("email") and mail.get("app_password")),
                "gmail_email": mail.get("email", ""),
                "gmail_configured": bool(mail.get("email") and mail.get("app_password")),
                "imap_host": mail.get("imap_host", ""),
                "mailbox": mail.get("mailbox", ""),
                "proxy": bool(cfg.get("proxy", {}).get("host")),
                "mail_proxy_supported": mail["provider"] == "gmail",
                "mail_proxy_enabled": gmail_client._proxy_cfg(cfg, mail)["enabled"],
                "mail_proxy_host": gmail_client._proxy_cfg(cfg, mail)["host"] or "127.0.0.1",
                "mail_proxy_port": gmail_client._proxy_cfg(cfg, mail)["port"],
                "last_credit_email": last_credit_email,
                "last_credit_txn": last_credit_txn,
                "last_uid": last_uid,
                # debit card and income via email
                "last_debit_txn": last_debit_txn,
                # spending config
                "categories_n": categories_n,
                "knowledge_n": knowledge_n,
                "billing_start_day": store.get_billing_start_day(),
                # income
                "deposits_n": deposits_n,
                "deposits_last": deposits_last,
                # notification (shared by spending + income)
                "notify_channel": "微信",
                "notify_configured": bool(n.get("weixin_target") and n.get("hermes_bin")),
                "notify_sent": notify_sent or 0,
                "notify_last": notify_last,
                # collection sources
                "sources": sources,
                "channel_support": channel_support,
                # runtime
                "poll_interval": cfg.get("poll", {}).get("interval_seconds", 90),
                "db_path": _DB,
            })
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body_json()

        if path == "/api/classify":
            if _classify["running"]:
                return self._send_json({"running": True})
            _classify["running"] = True
            _classify["result"] = None
            threading.Thread(target=_run_classify, daemon=True).start()
            return self._send_json({"started": True})

        if path == "/api/config/check":
            card_type = body.get("card_type", "credit")
            with _conn() as c:
                sources = store.get_sources(c)
            if card_type not in sources:
                return self._send_json({
                    "ok": False,
                    "unsupported": True,
                    "error": "未知卡种",
                })
            return self._send_json(_gmail_self_check(
                card_type, sources[card_type]["bank"]))

        if path == "/api/config/check_notify":
            return self._send_json(notify.check_wechat(config.load()))

        if path == "/api/gmail_auth":
            # Legacy route name retained for compatibility. Save the provider,
            # email, and authorization code from the in-page wizard to a
            # gitignored secrets file (config.load picks it up live), then
            # verify by connecting.
            provider = (body.get("provider") or "gmail").strip()
            if provider not in gmail_client.PROVIDERS:
                return self._send_json({"ok": False, "error": "不支持的邮箱类型"})
            email = (body.get("email") or "").strip()
            pw = (body.get("app_password") or "").replace(" ", "").strip()
            if not email or not pw:
                return self._send_json({"ok": False, "error": "邮箱和授权码都要填"})
            Path("secrets").mkdir(exist_ok=True)
            f = Path("secrets/mail_auth.json")
            saved = {
                "provider": provider,
                "email": email,
                "app_password": pw,
            }
            if provider == "gmail":
                proxy_enabled = bool(body.get("proxy_enabled"))
                proxy_host = (body.get("proxy_host") or "127.0.0.1").strip()
                try:
                    proxy_port = int(body.get("proxy_port") or 8118)
                except (TypeError, ValueError):
                    return self._send_json({"ok": False, "error": "代理端口必须是数字"})
                saved.update({
                    "proxy_enabled": proxy_enabled,
                    "proxy_host": proxy_host,
                    "proxy_port": proxy_port,
                })
            f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
            return self._send_json({"ok": True, "check": _mail_self_check()})

        if path == "/api/settings/billing_start_day":
            with _conn() as c:
                day = store.save_billing_start_day(c, body.get("day"))
                return self._send_json({"ok": True, "billing_start_day": day})

        m = re.match(r"^/api/transactions/(\d+)/category$", path)
        if m:
            with _conn() as c:
                return self._send_json(store.set_category(
                    c, int(m.group(1)), body["category"]))
        if path == "/api/transactions/merge":
            with _conn() as c:
                return self._send_json(store.merge_transactions(
                    c, body.get("ids", []), category=body.get("category") or None,
                    note=body.get("note") or None, month=body.get("month") or None))

        m = re.match(r"^/api/transactions/(\d+)/split$", path)
        if m:
            with _conn() as c:
                return self._send_json(store.split_transaction(
                    c, int(m.group(1)), body.get("amounts", [])))
        m = re.match(r"^/api/transactions/(\d+)/note$", path)
        if m:
            with _conn() as c:
                store.set_note(c, int(m.group(1)), body.get("note", ""))
                return self._send_json({"ok": True})
        m = re.match(r"^/api/transactions/(\d+)/confirm$", path)
        if m:
            with _conn() as c:
                store.confirm_txn(c, int(m.group(1)))
                return self._send_json({"ok": True})
        m = re.match(r"^/api/transactions/(\d+)/void$", path)
        if m:
            with _conn() as c:
                store.set_voided(c, int(m.group(1)), bool(body.get("voided", True)))
                return self._send_json({"ok": True})
        if path == "/api/merchant/category":
            with _conn() as c:
                return self._send_json(store.categorize_merchant(
                    c, body["merchant_key"], body["category"],
                ))
        if path == "/api/knowledge":
            with _conn() as c:
                store.add_knowledge(
                    c, body.get("scope", "global"), body.get("text", ""),
                    merchant_key=body.get("merchant_key"),
                    category=body.get("category"),
                )
                return self._send_json({"ok": True})
        if path == "/api/categories/update":
            with _conn() as c:
                return self._send_json(store.update_category(
                    c, body["key"], name=body.get("name"), descr=body.get("descr")))
        if path == "/api/categories/add":
            with _conn() as c:
                return self._send_json(store.add_category(
                    c, body.get("name", ""), body.get("descr", "")))
        if path == "/api/categories/delete":
            with _conn() as c:
                return self._send_json(store.delete_category(c, body["key"]))
        if path == "/api/knowledge/delete":
            with _conn() as c:
                store.delete_knowledge(c, int(body["id"]))
                return self._send_json({"ok": True})
        if path == "/api/knowledge/update":
            with _conn() as c:
                return self._send_json(store.update_knowledge(
                    c, int(body["id"]), body.get("text", ""),
                    category=body.get("category") or None))
        self.send_error(404)


def main():
    host = _CFG.get("web", {}).get("host", "127.0.0.1")
    port = int(_CFG.get("web", {}).get("port", 8770))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Plutus console on http://{host}:{port}  (db: {_DB})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
