"""Read-only IMAP access to the user's transaction mailbox.

Gmail remains supported with its X-GM-RAW search and X-GM-MSGID stable id. China
mainland-friendly providers (QQ/163) use standard IMAP search and Message-ID as
the stable id. The public module name stays gmail_client for compatibility with
the existing ingestion/web code.
"""
from __future__ import annotations

import email
import imaplib
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Optional

# Conventional English name, used only as a last-resort fallback. The folder is
# actually resolved by its language-independent \All special-use flag, because
# Gmail localizes the display name (e.g. "[Gmail]/所有邮件") when the account's
# UI language changes — which silently broke ingestion when it was hardcoded.
ALL_MAIL = '"[Gmail]/All Mail"'
_BEIJING = timezone(timedelta(hours=8))
PROVIDERS = {
    "gmail": {
        "label": "Gmail",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "mailbox": None,          # resolve Gmail All Mail by \All
        "search": "gmail",
    },
    "qq": {
        "label": "QQ 邮箱",
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "mailbox": "INBOX",
        "search": "imap",
    },
    "163": {
        "label": "163 邮箱",
        "imap_host": "imap.163.com",
        "imap_port": 993,
        "mailbox": "INBOX",
        "search": "imap",
    },
}

# One LIST reply line:  (flags) "delimiter" name   — name may be quoted.
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+?)\s*$')


@dataclass
class FetchedEmail:
    uid: int
    gmail_msgid: str
    sender: str
    subject: str
    received: datetime          # naive Beijing wall-clock time
    internal_ms: int
    html: Optional[str]
    text: str                   # decoded text/plain or de-tagged html


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", "replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _make_proxy_imap(proxy_host: str, proxy_port: int, host: str, port: int):
    # Override _create_socket (not open): the base open() then wires up sock and
    # the read buffer itself. This is version-robust — Python 3.14 made open()
    # set self._file and turned `file` into a read-only property, so overriding
    # open() and assigning self.file raises AttributeError.
    class _ProxyIMAP4(imaplib.IMAP4_SSL):
        def _create_socket(self, timeout=None):
            raw = socket.create_connection((proxy_host, proxy_port), 30)
            raw.sendall(
                f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
            )
            resp = b""
            while b"\r\n\r\n" not in resp:
                resp += raw.recv(4096)
            if b" 200 " not in resp.split(b"\r\n")[0]:
                raise RuntimeError(f"proxy CONNECT failed: {resp.split(chr(13).encode())[0]!r}")
            return ssl.create_default_context().wrap_socket(raw, server_hostname=host)

    return _ProxyIMAP4(host, port)


def resolve_all_mail(m: imaplib.IMAP4) -> str:
    """Return the All Mail mailbox name, located by its \\All special-use flag
    so it survives Gmail's display-name localization. Falls back to the English
    name if no \\All folder is advertised."""
    typ, rows = m.list()
    if typ == "OK":
        for row in rows or []:
            line = row if isinstance(row, bytes) else row.encode("utf-8", "replace")
            mo = _LIST_RE.match(line)
            if mo and b"\\all" in mo.group("flags").lower():
                return mo.group("name").decode("utf-8", "replace")
    return ALL_MAIL


def select_all_mail(m: imaplib.IMAP4, mailbox: Optional[str] = None) -> str:
    """Select the All Mail folder read-only, raising a clear error if it fails
    (e.g. the name changed) instead of leaving the connection in AUTH state,
    where the next SEARCH dies with a cryptic 'illegal in state AUTH'."""
    mailbox = mailbox or resolve_all_mail(m)
    typ, data = m.select(mailbox, readonly=True)
    if typ != "OK":
        detail = data[0].decode("utf-8", "replace") if data and data[0] else typ
        raise RuntimeError(
            f"IMAP SELECT {mailbox} failed: {detail}. The All Mail folder is "
            f"auto-detected via its \\All flag; set [gmail].mailbox in config.toml "
            f"to override."
        )
    return mailbox


def _mail_cfg(cfg: dict) -> dict:
    """Return the active mailbox config, accepting both new [mail] and legacy
    [gmail]. UI support is added later; this keeps current callers stable."""
    raw = dict(cfg.get("mail") or {})
    legacy = cfg.get("gmail") or {}
    if not raw:
        raw = dict(legacy)
        raw["provider"] = "gmail"
    provider = str(raw.get("provider") or "gmail").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported mail provider: {provider}")
    defaults = PROVIDERS[provider]
    out = dict(defaults)
    out.update(raw)
    out["provider"] = provider
    # Legacy Gmail credentials still work when [mail] is present but incomplete.
    if provider == "gmail":
        for k in ("email", "app_password", "app_password_file", "query", "mailbox"):
            if not out.get(k) and legacy.get(k):
                out[k] = legacy[k]
    return out


def provider_label(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("label", provider)


def connect(cfg: dict) -> imaplib.IMAP4:
    g = _mail_cfg(cfg)
    proxy = cfg.get("proxy", {})
    host, port = g.get("imap_host", "imap.gmail.com"), int(g.get("imap_port", 993))
    if proxy.get("host"):
        m = _make_proxy_imap(proxy["host"], int(proxy["port"]), host, port)
    else:
        m = imaplib.IMAP4_SSL(host, port)
    m.login(g["email"], g["app_password"])
    if g["provider"] == "gmail":
        select_all_mail(m, g.get("mailbox"))
    else:
        _select_mailbox(m, g.get("mailbox") or PROVIDERS[g["provider"]]["mailbox"])
    setattr(m, "_plutus_provider", g["provider"])
    return m


def _select_mailbox(m: imaplib.IMAP4, mailbox: str) -> str:
    typ, data = m.select(mailbox, readonly=True)
    if typ != "OK":
        detail = data[0].decode("utf-8", "replace") if data and data[0] else typ
        raise RuntimeError(f"IMAP SELECT {mailbox} failed: {detail}")
    return mailbox


def search_uids(m: imaplib.IMAP4, query: str) -> list[int]:
    """Search matching transaction senders.

    Gmail uses X-GM-RAW so existing queries keep working. QQ/163 use portable
    IMAP criteria derived from the same query string: from: addresses and either
    after:YYYY/MM/DD or newer_than:Nd when present.
    """
    provider = getattr(m, "_plutus_provider", "gmail")
    if provider != "gmail":
        return _search_uids_imap(m, query)
    typ, data = m.uid("search", None, "X-GM-RAW", f'"{query}"')
    if typ != "OK":
        raise RuntimeError(f"search failed: {typ} {data}")
    return [int(x) for x in data[0].split()]


def _search_uids_imap(m: imaplib.IMAP4, query: str) -> list[int]:
    senders = re.findall(r"from:([^\s)]+)", query or "")
    if not senders:
        raise RuntimeError("standard IMAP search requires at least one from: sender")
    date = _since_date(query)
    out: set[int] = set()
    for sender in senders:
        criteria = []
        if date:
            criteria += ["SINCE", date]
        criteria += ["FROM", sender]
        typ, data = m.uid("search", None, *criteria)
        if typ != "OK":
            raise RuntimeError(f"search failed: {typ} {data}")
        out.update(int(x) for x in data[0].split())
    return sorted(out)


def _since_date(query: str) -> Optional[str]:
    m = re.search(r"after:(\d{4})/(\d{1,2})/(\d{1,2})", query or "")
    if m:
        return _imap_date(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    m = re.search(r"newer_than:(\d+)d", query or "")
    if m:
        return _imap_date(datetime.now() - timedelta(days=int(m.group(1))))
    return None


def _imap_date(d: datetime) -> str:
    return d.strftime("%d-%b-%Y")


def _extract_bodies(msg) -> tuple[Optional[str], str]:
    html_body = None
    text_body = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        decoded = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/html" and html_body is None:
            html_body = decoded
        elif ctype == "text/plain" and text_body is None:
            text_body = decoded
    import re
    text = text_body or (re.sub(r"(?is)<[^>]+>", " ", html_body) if html_body else "")
    return html_body, " ".join(text.split())


def fetch(m: imaplib.IMAP4, uid: int) -> FetchedEmail:
    provider = getattr(m, "_plutus_provider", "gmail")
    fields = "(X-GM-MSGID INTERNALDATE RFC822)" if provider == "gmail" else "(INTERNALDATE RFC822)"
    typ, data = m.uid("fetch", str(uid), fields)
    if typ != "OK":
        raise RuntimeError(f"fetch failed: {typ} {data}")
    meta_line = data[0][0].decode("latin-1", "replace")
    msgid = _between(meta_line, "X-GM-MSGID ", " ")
    internal = imaplib.Internaldate2tuple(data[0][0]) if b"INTERNALDATE" in data[0][0] else None
    msg = email.message_from_bytes(data[0][1])
    stable_id = msgid or (msg.get("Message-ID") or "").strip() or f"{provider}:{uid}"

    received = _received_dt(msg)
    html_body, text = _extract_bodies(msg)
    return FetchedEmail(
        uid=uid,
        gmail_msgid=stable_id,
        sender=_decode(msg.get("From")),
        subject=_decode(msg.get("Subject")),
        received=received,
        internal_ms=int(__import__("time").mktime(internal) * 1000) if internal else 0,
        html=html_body,
        text=text,
    )


def _received_dt(msg) -> datetime:
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_BEIJING).replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        return datetime.now()


def _between(s: str, start: str, end: str) -> str:
    i = s.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = s.find(end, i)
    return s[i:j] if j > 0 else s[i:]
