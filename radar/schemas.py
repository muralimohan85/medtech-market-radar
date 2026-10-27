"""Core records: the watch brief, raw items from connectors, facts, events and the report."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from radar.config import CONFIG, load_yaml


# ---------------------------------------------------------------- watch brief
class Competitor(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)


class YourSolution(BaseModel):
    name: str = "[YOUR PRODUCT]"
    status: str = ""
    us_filing_year: int | None = None


class Scope(BaseModel):
    include_any: list[str] = Field(default_factory=list)
    exclude_any: list[str] = Field(default_factory=list)      # out of scope unless an include term also appears
    exclude_always: list[str] = Field(default_factory=list)   # out of scope whatever else the item says


class WatchBrief(BaseModel):
    id: str
    name: str
    device_type: str
    diagnostic_solution: str
    short_name: str = ""            # how the report names the test, e.g. hs-cTnI
    clinical_use: str = ""
    regions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    regulatory_ids: dict[str, list[str]] = Field(default_factory=dict)
    scope: Scope = Field(default_factory=Scope)
    competitors: list[Competitor] = Field(default_factory=list)
    your_solution: YourSolution = Field(default_factory=YourSolution)
    refresh: dict = Field(default_factory=lambda: {"report": "weekly", "report_weekday": 0})
    cadence: dict[str, str] = Field(default_factory=dict)
    digest: dict = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, watch_id_or_path: str | Path) -> "WatchBrief":
        p = Path(watch_id_or_path)
        if not p.exists():
            p = CONFIG / "watches" / f"{watch_id_or_path}.yaml"
        return cls(**load_yaml(p))

    @property
    def test_name(self) -> str:
        return self.short_name or self.diagnostic_solution

    def brief_text(self) -> str:
        comps = ", ".join(c.name for c in self.competitors) or "none listed"
        return (f"Device type: {self.device_type}. Diagnostic solution: {self.diagnostic_solution}. "
                f"Clinical use: {self.clinical_use}. Regions: {', '.join(self.regions)}. Competitors: {comps}. "
                f"Your solution: {self.your_solution.name}, {self.your_solution.status}.")


# ---------------------------------------------------------------- connector output
@dataclass
class RawItem:
    """One record as a connector returns it, before any interpretation."""
    id: str
    source: str                     # connector name, e.g. news_rss
    kind: str                       # news, press_release, fda_510k, fda_recall, trial, publication, market_estimate, ...
    published: str                  # ISO date
    title: str
    text: str = ""
    url: str = ""
    fields: dict = field(default_factory=dict)
    licence: str = "public"

    @property
    def content_hash(self) -> str:
        # the title is left out so an identical re-delivery under a new title is still recognised
        body = json.dumps([self.source, self.text, self.fields], sort_keys=True, default=str)
        return hashlib.sha256(body.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- evidence
@dataclass
class Fact:
    id: str
    source_id: str
    entity: str                     # company name, or "" for market-wide facts
    topic: str                      # same vocabulary as event kinds
    claim: str                      # one self-contained sentence, as stored (already sanitised)
    as_of: str                      # date the fact refers to / was published
    licence: str
    data: dict = field(default_factory=dict)   # structured values: value_low, cagr, price, status, ...
    confidence: float = 0.8
    event_id: str = ""


@dataclass
class Event:
    id: str
    kind: str
    entity: str
    title: str
    date: str                       # published date of the first item
    detected: str                   # date the radar saw it
    sections: list[str]
    score: float
    level: str                      # high | medium | low | noise
    in_scope: bool
    reasons: list[str] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    duplicate_of: str = ""
    new_entity: str = ""
    quarantined: list[str] = field(default_factory=list)
    engine: str = "deterministic"


# ---------------------------------------------------------------- report
@dataclass
class Sentence:
    text: str
    cites: list[str] = field(default_factory=list)     # fact ids
    kind: str = "fact"              # fact | assessment (rule-based judgement over cited facts) | judgement (uses the brief)


@dataclass
class Block:
    type: str                       # p | h3
    text: str = ""                  # for h3
    sentences: list[Sentence] = field(default_factory=list)


@dataclass
class Section:
    id: str
    title: str
    blocks: list[Block] = field(default_factory=list)
    as_of: str = ""
    engine: str = "deterministic"

    def sentences(self) -> list[Sentence]:
        return [s for b in self.blocks for s in b.sentences]

    def plain(self) -> str:
        out = []
        for b in self.blocks:
            out.append(b.text if b.type == "h3" else " ".join(s.text for s in b.sentences))
        return "\n".join(out)


@dataclass
class Report:
    watch_id: str
    as_of: str
    sections: dict[str, Section] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        secs = {}
        for k, s in d["sections"].items():
            blocks = [Block(type=b["type"], text=b.get("text", ""),
                            sentences=[Sentence(**x) for x in b.get("sentences", [])]) for b in s["blocks"]]
            secs[k] = Section(id=s["id"], title=s["title"], blocks=blocks, as_of=s.get("as_of", ""),
                              engine=s.get("engine", "deterministic"))
        return cls(watch_id=d["watch_id"], as_of=d["as_of"], sections=secs)

    @property
    def content_hash(self) -> str:
        """Hash of what a reader sees: section text and citations. Approval signs this."""
        body = json.dumps({k: [[b.type, b.text, [[s.text, s.cites, s.kind] for s in b.sentences]] for b in v.blocks]
                           for k, v in sorted(self.sections.items())}, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()


def iso(d: date | str) -> str:
    return d if isinstance(d, str) else d.isoformat()
