"""IMAP access to Gmail, optionally tunneled through an HTTP CONNECT proxy
(Gmail is slow/unreachable directly here). Read-only.

Stable ids: Gmail's X-GM-MSGID is used as the dedup key for emails; the IMAP UID
is used as the incremental high-water mark for resume.
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


def connect(cfg: dict) -> imaplib.IMAP4:
    g = cfg["gmail"]
    proxy = cfg.get("proxy", {})
    host, port = g.get("imap_host", "imap.gmail.com"), int(g.get("imap_port", 993))
    if proxy.get("host"):
        m = _make_proxy_imap(proxy["host"], int(proxy["port"]), host, port)
    else:
        m = imaplib.IMAP4_SSL(host, port)
    m.login(g["email"], g["app_password"])
    select_all_mail(m, g.get("mailbox"))
    return m


def search_uids(m: imaplib.IMAP4, gmail_query: str) -> list[int]:
    """Search using Gmail syntax via X-GM-RAW. ASCII queries only (imaplib limit)."""
    typ, data = m.uid("search", None, "X-GM-RAW", f'"{gmail_query}"')
    if typ != "OK":
        raise RuntimeError(f"search failed: {typ} {data}")
    return [int(x) for x in data[0].split()]


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
    typ, data = m.uid("fetch", str(uid), "(X-GM-MSGID INTERNALDATE RFC822)")
    meta_line = data[0][0].decode("latin-1", "replace")
    msgid = _between(meta_line, "X-GM-MSGID ", " ")
    internal = imaplib.Internaldate2tuple(data[0][0]) if b"INTERNALDATE" in data[0][0] else None
    msg = email.message_from_bytes(data[0][1])

    received = _received_dt(msg)
    html_body, text = _extract_bodies(msg)
    return FetchedEmail(
        uid=uid,
        gmail_msgid=msgid,
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
