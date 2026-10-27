"""Render report sections as HTML with numbered citations, and redlines between versions."""

from __future__ import annotations

import difflib
import html
import re

from radar.safety import licence
from radar.schemas import Report, Section, Sentence
from radar.store import Store


class Citations:
    """Numbers sources in order of first appearance across the report (one number per source)."""

    def __init__(self, report: Report, store: Store, order: list[str]):
        self.store, self.num, self.fact_src = store, {}, {}
        for sid in order:
            sec = report.sections.get(sid)
            if not sec:
                continue
            for s in sec.sentences():
                for c in s.cites:
                    f = store.fact(c)
                    src = f.source_id if f else c
                    self.fact_src[c] = src
                    self.num.setdefault(src, len(self.num) + 1)

    def marks(self, s: Sentence) -> list[int]:
        return sorted({self.num[self.fact_src[c]] for c in s.cites if c in self.fact_src})

    def sources(self) -> list[dict]:
        out = []
        for src, n in sorted(self.num.items(), key=lambda kv: kv[1]):
            row = self.store.source(src) or {"id": src, "title": src, "licence": "public", "connector": "", "url": "",
                                             "published": ""}
            out.append({"n": n, **row, "kind": licence.kind_of(row["licence"]), "licence_label": licence.label(row["licence"])})
        return out


def _sup(nums: list[int]) -> str:
    if not nums:
        return ""
    return "<sup>" + ",".join(f'<a href="#src-{n}">{n}</a>' for n in nums) + "</sup>"


def sentence_html(s: Sentence, cites: Citations) -> str:
    cls = ' class="judgement"' if s.kind == "judgement" else ""
    return f"<span{cls}>{html.escape(s.text)}</span>{_sup(cites.marks(s))}"


def section_html(sec: Section, cites: Citations) -> str:
    out = []
    for b in sec.blocks:
        if b.type == "h3":
            out.append(f"<h3>{html.escape(b.text)}</h3>")
        else:
            out.append("<p>" + " ".join(sentence_html(s, cites) for s in b.sentences) + "</p>")
    return "\n".join(out)


# ------------------------------------------------------------------ redline
def _units(sec: Section | None) -> list[tuple[str, Sentence | None]]:
    if sec is None:
        return []
    out = []
    for b in sec.blocks:
        if b.type == "h3":
            out.append(("h3:" + b.text, None))
        else:
            out += [(s.text, s) for s in b.sentences]
            out.append(("¶", None))
    return out


def _word_diff(a: str, b: str) -> str:
    wa, wb = re.findall(r"\S+|\s+", a), re.findall(r"\S+|\s+", b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(wa[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(wa[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(wb[j1:j2]))}</ins>")
    return "".join(out)


def redline_html(old: Section | None, new: Section, cites: Citations) -> tuple[str, int]:
    """Sentence-level redline; a reworded sentence shows word-level changes. Returns (html, number of edits)."""
    a, b = _units(old), _units(new)
    sm = difflib.SequenceMatcher(None, [x[0] for x in a], [x[0] for x in b], autojunk=False)
    parts, edits, para = [], 0, []

    def flush():
        if para:
            parts.append("<p>" + " ".join(para) + "</p>")
            para.clear()

    def emit(text: str, s: Sentence | None, how: str):
        if text == "¶":
            flush()
            return
        if text.startswith("h3:"):
            flush()
            t = html.escape(text[3:])
            parts.append(f"<h3>{t}</h3>" if how == "eq" else f"<h3><{how}>{t}</{how}></h3>")
            return
        body = html.escape(text) if how == "eq" else f"<{how}>{html.escape(text)}</{how}>"
        cls = ' class="judgement"' if s is not None and s.kind == "judgement" and how != "del" else ""
        marks = _sup(cites.marks(s)) if s is not None and how != "del" else ""
        para.append(f"<span{cls}>{body}</span>{marks}")

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for t, s in b[j1:j2]:
                emit(t, s, "eq")
            continue
        olds = [x for x in a[i1:i2] if x[0] != "¶"]
        news = [x for x in b[j1:j2] if x[0] != "¶"]
        edits += max(len(olds), len(news))
        if op == "replace" and len(olds) == len(news) and all(not o[0].startswith("h3:") for o in olds):
            for (ot, _), (nt, ns) in zip(olds, news):
                ratio = difflib.SequenceMatcher(None, ot, nt).ratio()
                if ratio > 0.6:
                    cls = ' class="judgement"' if ns and ns.kind == "judgement" else ""
                    para.append(f"<span{cls}>{_word_diff(ot, nt)}</span>{_sup(cites.marks(ns)) if ns else ''}")
                else:
                    emit(ot, None, "del")
                    emit(nt, ns, "ins")
            for t, _s in b[j1:j2]:
                if t == "¶":
                    flush()
            continue
        for t, _ in a[i1:i2]:
            if t != "¶":
                emit(t, None, "del")
        for t, s in b[j1:j2]:
            emit(t, s, "ins")
    flush()
    return "\n".join(parts), edits
