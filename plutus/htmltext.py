"""Minimal, dependency-free HTML -> text-lines flattener.

CMB notification emails are simple table layouts; we only need the visible text
split into non-empty lines in document order. Using the stdlib keeps the parser
runnable without lxml/bs4 installed.
"""
from __future__ import annotations

import html
import re

_DROP_SCRIPT_STYLE = re.compile(r"(?is)<(script|style).*?</\1>")
_BR = re.compile(r"(?is)<br\s*/?>")
_BLOCK_CLOSE = re.compile(r"(?is)</(td|tr|div|p|table|li|h[1-6])>")
_ANY_TAG = re.compile(r"(?is)<[^>]+>")
_WS = re.compile(r"[ \t\xa0]+")


def html_to_lines(raw: str) -> list[str]:
    """Return visible text as a list of trimmed, non-empty lines."""
    t = _DROP_SCRIPT_STYLE.sub(" ", raw)
    t = _BR.sub("\n", t)
    t = _BLOCK_CLOSE.sub("\n", t)
    t = _ANY_TAG.sub(" ", t)
    t = html.unescape(t)
    return [_WS.sub(" ", ln).strip() for ln in t.split("\n") if ln.strip()]
