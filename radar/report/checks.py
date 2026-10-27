"""The fact-checker: runs on every draft before an analyst sees it.

* No source, no sentence: every sentence cites facts that exist and are dated on or
  before the report date. Judgement calls (sentences that use your own plan) are
  allowed but flagged for the analyst.
* Every number in a sentence appears in the facts it cites (counts of the cited
  companies or items are allowed).
* Licensed wording is never copied: no run of eight words from a no-quote source.
* No instruction-like text from a source reaches the report.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from radar.safety import licence
from radar.safety.sanitizer import is_injection
from radar.schemas import Fact, Report, Section, Sentence
from radar.store import Store

NUM = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")


@dataclass
class Issue:
    section: str
    sentence: str
    problem: str
    severity: str = "block"      # block | flag


@dataclass
class CheckResult:
    sentences: int = 0
    cited: int = 0
    judgement: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(i.severity == "block" for i in self.issues)

    def summary(self) -> dict:
        kinds = {p: sum(1 for i in self.issues if i.problem.startswith(p)) for p in ("uncited", "unsupported number",
                                                                                    "licensed wording", "instruction")}
        return {"sentences": self.sentences, "cited": self.cited, "judgement_calls": self.judgement,
                "uncited": kinds["uncited"], "unsupported_numbers": kinds["unsupported number"],
                "licence_breaches": kinds["licensed wording"], "injection_leaks": kinds["instruction"],
                "blocked": self.blocked, "issues": [asdict(i) for i in self.issues]}


def _numbers(s: str) -> set[float]:
    out = set()
    for n in NUM.findall(s or ""):
        try:
            out.add(round(float(n.replace(",", "")), 4))
        except ValueError:
            pass
    return out


def _flatten(x) -> str:
    if isinstance(x, dict):
        return " ".join(f"{k} {_flatten(v)}" for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return " ".join(_flatten(v) for v in x)
    return str(x)


def allowed_numbers(facts: list[Fact]) -> set[float]:
    nums: set[float] = set()
    for f in facts:
        nums |= _numbers(f.claim) | _numbers(_flatten(f.data)) | _numbers(f.as_of)
    nums |= {float(len(facts)), float(len({f.entity for f in facts if f.entity}))}
    return nums


def check_sentence(sec_id: str, s: Sentence, store: Store, as_of: str, res: CheckResult) -> None:
    res.sentences += 1
    facts = [store.fact(c) for c in s.cites]
    missing = [c for c, f in zip(s.cites, facts) if f is None]
    facts = [f for f in facts if f is not None]
    future = [f.id for f in facts if f.as_of > as_of]
    if not s.cites or missing or future:
        if not s.cites:
            why = "no citation"
        else:
            why = f"cites unknown facts {missing}" if missing else f"cites facts dated after the report {future}"
        res.issues.append(Issue(sec_id, s.text, f"uncited: {why}"))
    else:
        res.cited += 1
    if s.kind == "judgement":
        res.judgement += 1
        res.issues.append(Issue(sec_id, s.text, "judgement call: uses your own plan from the watch brief", "flag"))
    bad = sorted(n for n in _numbers(s.text) - allowed_numbers(facts) if facts)
    if facts and bad:
        res.issues.append(Issue(sec_id, s.text, f"unsupported number: {', '.join(f'{b:g}' for b in bad)} not in the cited facts"))
    for f in facts:
        if not licence.can_quote(f.licence) and licence.copies_wording(s.text, f.claim):
            res.issues.append(Issue(sec_id, s.text, f"licensed wording copied from {f.source_id}"))
    if is_injection(s.text):
        res.issues.append(Issue(sec_id, s.text, "instruction-like text in the report"))


def check_section(sec: Section, store: Store, as_of: str, res: CheckResult | None = None) -> CheckResult:
    res = res or CheckResult()
    for s in sec.sentences():
        check_sentence(sec.id, s, store, as_of, res)
    quarantined = [q for src in store.sources() for q in src["quarantined"]]
    text = sec.plain()
    for q in quarantined:
        if q and q[:60] in text:
            res.issues.append(Issue(sec.id, q, "instruction from a source reached the report"))
    return res


def check_report(rep: Report, store: Store, only: list[str] | None = None) -> CheckResult:
    res = CheckResult()
    for sid, sec in rep.sections.items():
        if only is None or sid in only:
            check_section(sec, store, rep.as_of, res)
    return res
