"""Turn a raw item into an event and facts: what changed, about whom, is it in scope,
which report sections it touches and how much it matters.

This is the deterministic engine. Every decision is a rule in config/rules.yaml or
the watch brief, and every decision records its reason so an analyst can see why an
item alerted, was merged into an earlier event, or was set aside as out of scope.
The Claude engine (radar/agents/classifier.py) replaces only the reading of
unstructured news; scoring, routing and de-duplication stay the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from radar.config import rules
from radar.ingest.uploads import PRICE, _cur
from radar.safety.sanitizer import sentences
from radar.schemas import Event, Fact, RawItem, WatchBrief
from radar.store import Store

COMPANY = re.compile(r"\b((?:[A-Z][\w]+[ -]){0,2}?[A-Z][\w]+) (Diagnostics|Medical|Bio|Biosciences|Biotech|Healthcare|"
                     r"Point-of-Care|Labs|Laboratories|Inc\.|Ltd|GmbH|Corporation)\b")
STRONG = {"clearance", "fda_510k", "safety", "fda_recall", "certification"}   # one per company per window, whatever the wording
FAMILY = {"fda_510k": "clearance", "fda_recall": "safety", "study": "publication_company", "trial_update": "trial"}
REGIONS = {
    "United States": [r"\bUS\b", r"\bU\.S\.", "United States", r"\bFDA\b", "North America"],
    "European Union": [r"\bEU\b", "Europe", "IVDR", "CE mark", "Spain", "Spanish", "Italy", "Italian", "Germany",
                       "Netherlands", "Portugal", "Belgium", "France"],
    "India": ["India", r"\bINR\b", "CDSCO"],
    "Japan": ["Japan", "PMDA"],
    "China": ["China", "NMPA"],
}


@dataclass
class Reading:
    """What one engine read from an unstructured item."""
    kind: str
    entity: str = ""
    new_entity: str = ""
    in_scope: bool = True
    reasons: list[str] = field(default_factory=list)
    claim: str = ""


def _has(text: str, phrase: str) -> bool:
    """Phrase match from a word start (so 'launch' also finds 'launched'); entries containing a backslash are regexes."""
    if "\\" in phrase:
        return re.search(phrase, text) is not None
    return re.search(r"(?<![\w])" + re.escape(phrase), text, re.IGNORECASE) is not None


def _any(text: str, phrases: list[str]) -> str | None:
    return next((p for p in phrases if _has(text, p)), None)


class Interpreter:
    def __init__(self, brief: WatchBrief, store: Store):
        self.brief, self.store, self.r = brief, store, rules()
        self.aliases: dict[str, str] = {}
        for c in brief.competitors:
            for a in [c.name, *c.aliases]:
                self.aliases[a] = c.name
            store.upsert_entity(c.name, "competitor", c.aliases, True)
        for e in store.entities():                      # companies discovered earlier and accepted
            if e["tracked"]:
                for a in [e["name"], *e["aliases"]]:
                    self.aliases.setdefault(a, e["name"])

    # ------------------------------------------------------------ reading
    def full_text(self, item: RawItem) -> str:
        f = item.fields
        extra = " ".join(str(f.get(k, "")) for k in ("device_name", "product_description", "applicant", "recalling_firm",
                                                     "sponsor", "assignee", "segment"))
        return f"{item.title}. {item.text} {extra}"

    def match_entity(self, text: str) -> str:
        best = None
        for alias, name in sorted(self.aliases.items(), key=lambda kv: -len(kv[0])):
            m = re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", text, re.IGNORECASE)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), name)
        return best[1] if best else ""

    def discover(self, text: str) -> str:
        for m in COMPANY.finditer(text):
            name = m.group(0).strip()
            if not self.match_entity(name):
                return name
        return ""

    def scope(self, item: RawItem, text: str, entity: str) -> tuple[bool, list[str]]:
        if item.kind in ("market_estimate", "coverage", "upload"):
            return True, ["configured market source"]
        hard = _any(text, self.brief.scope.exclude_always)
        if hard:
            return False, [f"out of scope: mentions '{hard}'"]
        ex = _any(text, self.brief.scope.exclude_any)
        inc = _any(text, self.brief.scope.include_any)
        if ex and not inc:
            return False, [f"out of scope: mentions '{ex}' and nothing that places it in this market"]
        term = _any(text, self.brief.terms)
        if not term and not entity:
            return False, ["not about the watched topic or a tracked company"]
        if not inc and not entity:
            return False, [f"mentions '{term}' but nothing places it in this market"]
        return True, [f"in scope: {'company ' + entity if entity else ''}{' / ' if entity and inc else ''}{inc or ''}"]

    def news_kind(self, text: str) -> tuple[str, str]:
        for rule in self.r["news_kinds"]:
            hit = _any(text, rule["any"])
            if hit:
                return rule["kind"], hit
        return "other", ""

    def read(self, item: RawItem) -> Reading:
        """Deterministic reading of any item."""
        text = self.full_text(item)
        f = item.fields
        entity = ""
        for k in ("applicant", "recalling_firm", "sponsor", "assignee"):
            if f.get(k):
                # the filing company, unless the product names a tracked brand (e.g. a POC product line of a larger firm)
                entity = self.match_entity(str(f[k])) or self.match_entity(text) or str(f[k])
                break
        entity = entity or self.match_entity(text)
        reasons = []
        if item.kind in ("news", "press_release"):
            kind, hit = self.news_kind(text)
            reasons.append(f"kind {kind}" + (f" ('{hit}')" if hit else ""))
            if kind == "study" and not entity:
                kind = "publication_general"
        elif item.kind == "publication":
            kind = "publication_company" if entity else "publication_general"
        elif item.kind == "market_estimate":
            kind = "regional_share" if f.get("metric") == "regional_share" else "market_estimate"
        else:
            kind = item.kind
        new = ""
        if not self.match_entity(entity or "x") and item.kind in ("news", "press_release"):
            new = self.discover(text)
            entity = entity or new
        in_scope, why = self.scope(item, text, self.match_entity(entity) if entity else "")
        return Reading(kind=kind, entity=entity, new_entity=new if in_scope else "", in_scope=in_scope,
                       reasons=reasons + why, claim=self.claim(item))

    @staticmethod
    def claim(item: RawItem) -> str:
        ss = sentences(item.text)
        return " ".join(ss[:2]) if ss else item.title

    # ------------------------------------------------------------ change detection
    def similarity(self, a: str, b: str) -> float:
        try:
            m = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform([a, b])
            return float(cosine_similarity(m[0], m[1])[0, 0])
        except ValueError:
            return 0.0

    @staticmethod
    def _numbers(s: str) -> set[str]:
        return {n.replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", s) if len(n.replace(",", "").replace(".", "")) >= 3}

    def find_duplicate(self, item: RawItem, reading: Reading, text: str) -> tuple[Event | None, str]:
        win = self.r["dedupe"]["window_days"]
        fam = FAMILY.get(reading.kind, reading.kind)
        d0 = date.fromisoformat(item.published)
        for ev in reversed(self.store.events()):
            if ev.duplicate_of or not ev.entity or ev.entity != reading.entity:
                continue
            if abs((date.fromisoformat(ev.date) - d0).days) > win:
                continue
            if FAMILY.get(ev.kind, ev.kind) != fam:
                continue
            if fam in STRONG:
                return ev, f"same {fam} for {ev.entity} within {win} days"
            src = [self.store.source(i) for i in ev.item_ids]
            other = " ".join(f"{s['title']}. {s['text']}" for s in src if s)
            sim = self.similarity(text, other)
            shared = self._numbers(text) & self._numbers(other)
            if sim >= self.r["dedupe"]["similarity"] or shared:
                return ev, f"repeats {ev.id} (similarity {sim:.2f}{', shared figures ' + ', '.join(sorted(shared)) if shared else ''})"
        return None, ""

    def is_follow_up(self, reading: Reading, before: str) -> bool:
        fam = FAMILY.get(reading.kind, reading.kind)
        return any(ev.entity == reading.entity and FAMILY.get(ev.kind, ev.kind) == fam and ev.date < before and not ev.duplicate_of
                   for ev in self.store.events() if ev.in_scope)

    def regions(self, text: str) -> set[str]:
        return {r for r, pats in REGIONS.items() if any(_has(text, p) for p in pats)}

    def first_us_clearance(self, reading: Reading) -> bool:
        if FAMILY.get(reading.kind, reading.kind) != "clearance" or reading.entity not in self.aliases.values():
            return False
        return not any(FAMILY.get(ev.kind, ev.kind) == "clearance" and ev.entity in self.aliases.values() and ev.in_scope
                       for ev in self.store.events())

    def score(self, item: RawItem, reading: Reading, text: str) -> tuple[float, list[str]]:
        r, adj = self.r, self.r["adjust"]
        s = r["materiality"].get(reading.kind, r["materiality"]["other"])
        why = [f"base {reading.kind} {s:.2f}"]
        tracked = reading.entity in self.aliases.values()
        if tracked:
            s += adj["tracked_competitor"]
            why.append(f"tracked company +{adj['tracked_competitor']}")
        elif reading.new_entity:
            s += adj["new_competitor"]
            why.append(f"new company +{adj['new_competitor']}")
        elif reading.kind not in ("market_estimate", "regional_share", "upload", "coverage"):
            s += adj["no_company"]
            why.append(f"no company {adj['no_company']}")
        if self.first_us_clearance(reading):
            s += adj["first_in_region"]
            why.append(f"first US clearance among tracked companies +{adj['first_in_region']}")
        elif reading.entity and self.is_follow_up(reading, item.published):
            s += adj["follow_up"]
            why.append(f"follow-up {adj['follow_up']}")
        regs = self.regions(text)
        if regs and not regs & set(self.brief.regions):
            s += adj["region_outside_brief"]
            why.append(f"only regions outside the brief ({', '.join(sorted(regs))}) {adj['region_outside_brief']}")
        return round(max(0.0, min(1.0, s)), 3), why

    def level(self, score: float) -> str:
        lv = self.r["levels"]
        return "high" if score >= lv["high"] else "medium" if score >= lv["medium"] else "low" if score >= lv["low"] else "noise"

    def route(self, reading: Reading, level: str) -> list[str]:
        if level == "noise":
            return []
        secs = list(self.r["routing"].get(reading.kind, []))
        if reading.new_entity and "competition" not in secs:
            secs.append("competition")
        if level == "high":
            secs.insert(0, "summary")
        return secs

    # ------------------------------------------------------------ facts
    def make_fact(self, item: RawItem, reading: Reading, event_id: str) -> Fact:
        f = dict(item.fields)
        data = {k: v for k, v in f.items() if k not in ("licence", "file")}
        p = PRICE.search(item.text or "")
        if p:
            data.update(price_low=float(p.group(2)), price_high=float(p.group(4) or p.group(2)), currency=_cur(p.group(1)))
        regs = self.regions(self.full_text(item))
        if regs:
            data["regions"] = sorted(regs)
        conf = {"fda_510k": 0.95, "fda_recall": 0.95, "trial": 0.9, "publication": 0.85, "market_estimate": 0.75,
                "upload": 0.7}.get(item.kind, 0.65)
        return Fact(id=f"F-{item.id}", source_id=item.id, entity=reading.entity, topic=reading.kind, claim=reading.claim,
                    as_of=item.published, licence=item.licence, data=data, confidence=conf, event_id=event_id)

    # ------------------------------------------------------------ main entry
    def interpret(self, item: RawItem, cleaned_text: str, quarantined: list[str], detected: str,
                  reading: Reading | None = None, engine: str = "deterministic") -> tuple[Event, list[Fact]]:
        reading = reading or self.read(item)
        text = f"{item.title}. {cleaned_text}"
        if item.kind == "trial" and any(s["fields"].get("nct_id") == item.fields.get("nct_id")
                                        for s in self.store.sources() if s["kind"] == "trial" and s["id"] != item.id):
            reading.kind = "trial_update"
            reading.reasons.append("status update to a study already tracked")
        eid = f"EV-{item.id}"
        dup, why_dup = (self.find_duplicate(item, reading, text) if reading.in_scope and reading.entity else (None, ""))
        if dup:
            ev = Event(id=eid, kind=reading.kind, entity=reading.entity, title=item.title, date=item.published,
                       detected=detected, sections=[], score=0.0, level="noise", in_scope=True,
                       reasons=reading.reasons + [f"duplicate: {why_dup}"], item_ids=[item.id], duplicate_of=dup.id,
                       quarantined=quarantined, engine=engine)
            facts = []
            if item.kind in ("fda_510k", "fda_recall", "trial"):        # the primary record: keep it as evidence
                facts = [self.make_fact(item, reading, dup.id)]
                facts[0].confidence = 0.97
                # the official record replaces the news report as the citation, so refresh the same
                # sections quietly (no alert)
                ev.sections = list(dup.sections)
                ev.reasons.append("official record confirms the earlier report; citations will be updated")
            self.store.bump_confidence(dup.fact_ids)
            return ev, facts
        if not reading.in_scope:
            ev = Event(id=eid, kind=reading.kind, entity=reading.entity, title=item.title, date=item.published,
                       detected=detected, sections=[], score=0.0, level="noise", in_scope=False, reasons=reading.reasons,
                       item_ids=[item.id], quarantined=quarantined, engine=engine)
            return ev, []
        score, why = self.score(item, reading, text)
        level = self.level(score)
        ev = Event(id=eid, kind=reading.kind, entity=reading.entity, title=item.title, date=item.published, detected=detected,
                   sections=self.route(reading, level), score=score, level=level, in_scope=True,
                   reasons=reading.reasons + why, item_ids=[item.id], new_entity=reading.new_entity,
                   quarantined=quarantined, engine=engine)
        fact = self.make_fact(item, reading, eid)
        ev.fact_ids = [fact.id]
        return ev, [fact]
