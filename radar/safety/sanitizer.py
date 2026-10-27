"""Treat every source as data, never as instructions.

News, press releases and uploaded reports can carry text aimed at an AI model
("rate us as the market leader", "ignore previous instructions"). Sentences like
that are removed before any engine reads the source and kept on the source record
so an analyst can see what was quarantined and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|other) (instructions|rules|prompts)",
    r"\b(ai|a\.i\.|assistant|llm|language model|chatbot|model|automated (system|tool|summar\w+))s?\b[^.]{0,60}"
    r"\b(should|must|will|shall|please)\b[^.]{0,60}\b(rate|rank|describe|call|treat|ignore|omit|state|say|summari[sz]e|mention)\b",
    r"\b(disregard|forget)\b[^.]{0,30}\b(instructions|competitors|rules)\b",
    r"\bsystem prompt\b",
    r"\byou are (now )?(an?|the) (ai|assistant|model)\b",
    r"<\s*/?\s*(system|instructions?)\s*>",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class Cleaned:
    text: str
    quarantined: list[str] = field(default_factory=list)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split((text or "").strip()) if s.strip()]


def is_injection(sentence: str) -> bool:
    return any(r.search(sentence) for r in _INJ)


def clean(text: str) -> Cleaned:
    keep, drop = [], []
    for s in sentences(text):
        (drop if is_injection(s) else keep).append(s)
    return Cleaned(text=" ".join(keep), quarantined=drop)
