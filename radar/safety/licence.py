"""Licence rules that travel with every fact.

* quote: may a sentence reuse the source's own wording? For licensed databases the
  answer is usually no, so the report states their numbers in its own words.
* export: may the fact leave the workspace in an export, and for which audience?
* send_to_model: may the fact be sent to a hosted model?
"""

from __future__ import annotations

import re

from radar.config import licences

RESTRICTED = "[Restricted: licensed source, available in the workspace]"


def profile(tag: str) -> dict:
    return licences().get(tag, licences()["public"])


def label(tag: str) -> str:
    return profile(tag).get("label", tag)


def kind_of(tag: str) -> str:
    """public | licensed | upload | brief, used for the coloured source pills."""
    if tag.startswith("licensed"):
        return "licensed"
    if tag.startswith("upload"):
        return "upload"
    if tag in ("brief", "analyst"):
        return "brief"
    return "public"


def can_quote(tag: str) -> bool:
    return bool(profile(tag).get("quote", False))


def can_export(tag: str, audience: str = "internal") -> bool:
    return bool(profile(tag).get(f"export_{audience}", False))


def can_send_to_model(tag: str) -> bool:
    return bool(profile(tag).get("send_to_model", False))


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", s.lower())


def copies_wording(sentence: str, source_text: str, n: int = 8) -> bool:
    """True if the sentence repeats n or more consecutive words of the source."""
    a, b = _words(sentence), _words(source_text)
    if len(a) < n or len(b) < n:
        return False
    grams = {tuple(b[i:i + n]) for i in range(len(b) - n + 1)}
    return any(tuple(a[i:i + n]) in grams for i in range(len(a) - n + 1))
