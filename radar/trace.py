"""Audit trace: every agent step, model call, token and cost, in order."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class TraceEvent:
    ts: str
    case_id: str
    agent: str
    step: str
    summary: str
    detail: dict = field(default_factory=dict)
    duration_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Tracer:
    def __init__(self):
        self.events: list[TraceEvent] = []

    def log(self, case_id: str, agent: str, step: str, summary: str, **kw) -> TraceEvent:
        ev = TraceEvent(ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        case_id=case_id, agent=agent, step=step, summary=summary, **kw)
        self.events.append(ev)
        return ev

    @contextmanager
    def span(self, case_id: str, agent: str, step: str):
        """Time a step. The caller fills `rec` with summary/detail/tokens/cost."""
        rec: dict = {"summary": "", "detail": {}}
        t0 = time.perf_counter()
        try:
            yield rec
        finally:
            self.log(case_id, agent, step, rec.pop("summary", ""), detail=rec.pop("detail", {}),
                     duration_ms=round((time.perf_counter() - t0) * 1000, 2), **rec)

    def for_case(self, case_id: str) -> list[TraceEvent]:
        return [e for e in self.events if e.case_id == case_id]

    def totals(self, case_id: str) -> dict:
        evs = self.for_case(case_id)
        return {
            "duration_ms": round(sum(e.duration_ms for e in evs), 1),
            "input_tokens": sum(e.input_tokens for e in evs),
            "output_tokens": sum(e.output_tokens for e in evs),
            "cost_usd": round(sum(e.cost_usd for e in evs), 5),
        }

    def to_jsonl(self, case_id: str | None = None) -> str:
        evs = self.for_case(case_id) if case_id else self.events
        return "\n".join(json.dumps(asdict(e), default=str) for e in evs)
