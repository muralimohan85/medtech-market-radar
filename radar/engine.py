"""The radar: pulls sources on their cadence, turns items into events, drafts the
changed report sections, and publishes only what an analyst approves.

    radar = Radar.for_scenario()                 # fictional demo
    radar.run(until="2026-10-27", auto_approve=True)

    radar = Radar.live("hs_troponin_poc_live")   # real public sources
    radar.tick("2026-10-27"); radar.draft("2026-10-27")
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from radar.config import SECTION_ORDER, WORKSPACE
from radar.connectors.base import Connector
from radar.connectors.registry import build, load_scenario
from radar.ingest import uploads
from radar.ingest.interpret import Interpreter
from radar.report import approval as appr
from radar.report.checks import check_report, check_section
from radar.report.writer import Ctx, write
from radar.safety.sanitizer import clean
from radar.schemas import Event, Fact, RawItem, Report, Section, WatchBrief
from radar.store import Store
from radar.trace import Tracer

SCENARIO = "data/scenario/hs_troponin_poc.yaml"


def _days(a: str, b: str):
    d, end = date.fromisoformat(a), date.fromisoformat(b)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


class Radar:
    def __init__(self, brief: WatchBrief, store: Store, connectors: dict[str, Connector], engine: str = "deterministic",
                 client=None, model: str | None = None, offline: bool = False, secret: str | None = None):
        self.brief, self.store, self.connectors = brief, store, connectors
        self.engine, self.client, self.model, self.offline = engine, client, model, offline
        self.secret = secret or os.getenv("RADAR_APPROVAL_SECRET", "local-demo-secret")
        self.tracer = Tracer()
        self.interp = Interpreter(brief, store)
        self._claude_reader = self._claude_writer = None
        if engine == "claude":
            from radar.agents.classifier import ClaudeReader
            from radar.agents.writer import ClaudeWriter

            self._claude_reader = ClaudeReader(client, brief, model)
            self._claude_writer = ClaudeWriter(client, brief, model)

    # ------------------------------------------------------------ constructors
    @classmethod
    def for_scenario(cls, db: str | Path = ":memory:", scenario: str = SCENARIO, **kw) -> "Radar":
        meta, items = load_scenario(scenario)
        brief = WatchBrief.load(meta["watch"])
        r = cls(brief, Store(db), build(brief, items), **kw)
        r.scenario = meta
        return r

    @classmethod
    def live(cls, watch: str, db: str | Path | None = None, **kw) -> "Radar":
        brief = WatchBrief.load(watch)
        return cls(brief, Store(db or WORKSPACE / f"{brief.id}.db"), build(brief), **kw)

    # ------------------------------------------------------------ the brief as evidence
    def ensure_brief_fact(self, when: str) -> None:
        fid = f"F-brief-{self.brief.id}"
        if self.store.fact(fid):
            return
        y = self.brief.your_solution
        item = RawItem(id=f"brief-{self.brief.id}", source="brief", kind="brief", published=when, title="Your watch brief",
                       text=self.brief.brief_text(), licence="brief")
        self.store.add_source(item, when, item.text, [])
        self.store.add_fact(Fact(id=fid, source_id=item.id, entity="", topic="brief",
                                 claim=f"Your solution: {y.name}, {y.status}.", as_of=when, licence="brief",
                                 data={"us_filing_year": y.us_filing_year, "status": y.status}, confidence=1.0))

    # ------------------------------------------------------------ pulling
    def due(self, name: str, day: str) -> bool:
        last = self.store.last_pull(name)
        if last is None:
            return True
        if last >= day:
            return False
        cad = self.brief.cadence.get(name, "daily")
        d = date.fromisoformat(day)
        if cad in ("daily", "immediate"):
            return True
        if cad == "weekly":
            return d.weekday() == self.brief.refresh.get("report_weekday", 0) or (d - date.fromisoformat(last)).days >= 7
        if cad == "monthly":
            return (d.weekday() == 0 and d.day <= 7) or (d - date.fromisoformat(last)).days >= 31
        return True

    def tick(self, day: str, force: bool = False) -> list[Event]:
        """Pull every source that is due on `day` and ingest what arrived."""
        self.ensure_brief_fact(day)
        new = []
        for name, conn in self.connectors.items():
            if not (force or self.due(name, day)):
                continue
            since = self.store.last_pull(name)
            res = conn.pull(self.brief, since, day, offline=self.offline)
            self.store.record_pull(name, day, len(res.items), res.status + (f": {res.note}" if res.note else ""))
            for item in sorted(res.items, key=lambda i: (i.published, i.id)):
                ev = self.ingest(item, day)
                if ev:
                    new.append(ev)
        return new

    def ingest(self, item: RawItem, detected: str) -> Event | None:
        if self.store.source(item.id):
            return None
        dup_src = self.store.has_content(item.content_hash)
        if item.kind == "upload":
            return self._ingest_upload(item, detected)
        c = clean(item.text)
        if dup_src:
            orig = self.store.source(dup_src)
            ev = Event(id=f"EV-{item.id}", kind=item.kind, entity="", title=item.title, date=item.published, detected=detected,
                       sections=[], score=0.0, level="noise", in_scope=True,
                       reasons=[f"duplicate: identical content to {dup_src}"], item_ids=[item.id],
                       duplicate_of=(orig or {}).get("event_id", "") or f"EV-{dup_src}", quarantined=c.quarantined)
            self.store.add_source(item, detected, c.text, c.quarantined, ev.id)
            self.store.add_event(ev)
            return ev
        clean_item = RawItem(**{**item.__dict__, "text": c.text})
        reading = None
        engine = "deterministic"
        with self.tracer.span(item.id, "reader", "interpret") as rec:
            if self._claude_reader and item.kind in ("news", "press_release"):
                reading, usage_note = self._claude_reader.read(clean_item, self.interp)
                engine = "claude" if reading else "deterministic (fallback)"
                rec.update(usage_note)
            ev, facts = self.interp.interpret(clean_item, c.text, c.quarantined, detected, reading, engine)
            rec["summary"] = f"{ev.level} {ev.kind} {ev.entity}".strip()
        self.store.add_source(item, detected, c.text, c.quarantined, ev.duplicate_of or ev.id)
        for f in facts:
            self.store.add_fact(f)
        if ev.duplicate_of and facts:
            orig = self.store.event(ev.duplicate_of)
            if orig:
                orig.fact_ids = orig.fact_ids + [f.id for f in facts]
                orig.item_ids = orig.item_ids + [item.id]
                self.store.add_event(orig, self.store.event_version(orig.id))
        self.store.add_event(ev)
        if ev.new_entity and not self.store.entity(ev.new_entity):
            self.store.upsert_entity(ev.new_entity, "candidate", [], False, discovered=detected, first_event=ev.id)
        if ev.level == "high":
            self.store.add_alert(detected, ev.id, "high", f"High-impact change: {ev.title}", self.brief.digest.get("channel", "in_app"))
        return ev

    def _ingest_upload(self, item: RawItem, detected: str) -> Event:
        from radar.config import REPO

        path = Path(item.fields.get("file", ""))
        path = path if path.is_absolute() else REPO / path
        parsed = uploads.parse(path)
        found = uploads.extract_facts(parsed)
        lic = item.fields.get("licence") or item.licence
        eid = f"EV-{item.id}"
        title = item.title or parsed.name
        src = RawItem(id=item.id, source="upload", kind="upload", published=item.published, title=title,
                      text=parsed.text[:20000], fields={"file": path.name, "passages": len(parsed.passages),
                                                        "facts": len(found)}, licence=lic)
        self.store.add_source(src, detected, src.text, parsed.quarantined, eid)
        facts = []
        for i, fd in enumerate(found, 1):
            topic = "market_estimate" if fd["topic"] == "market_estimate" else "pricing"
            f = Fact(id=f"F-{item.id}-{i}", source_id=item.id, entity="", topic=topic, claim=fd["claim"], as_of=item.published,
                     licence=lic, data={**fd["data"], "where": fd["where"]}, confidence=0.7, event_id=eid)
            self.store.add_fact(f)
            facts.append(f)
        secs = sorted({"market" if f.topic == "market_estimate" else "pricing" for f in facts}, key=SECTION_ORDER.index)
        score = 0.5 if facts else 0.1
        ev = Event(id=eid, kind="upload", entity="", title=f"Upload: {title}", date=item.published, detected=detected,
                   sections=secs, score=score, level=self.interp.level(score), in_scope=True,
                   reasons=[f"{len(parsed.passages)} passages read, {len(facts)} facts extracted"], item_ids=[item.id],
                   fact_ids=[f.id for f in facts], quarantined=parsed.quarantined)
        self.store.add_event(ev)
        return ev

    def add_upload(self, path: str | Path, when: str, licence_tag: str = "upload_internal", title: str = "") -> Event:
        p = Path(path)
        item = RawItem(id=f"upload-{p.stem[:40]}-{when}", source="upload", kind="upload", published=when,
                       title=title or p.name, fields={"file": str(p.resolve()), "licence": licence_tag}, licence=licence_tag)
        return self.ingest(item, when)

    # ------------------------------------------------------------ analyst actions that change the report
    def set_working_range(self, low: float, high: float, analyst: str, when: str, note: str = "") -> Event:
        sid = f"analyst-range-{when}-{len(self.store.facts(topic='analyst_range')) + 1}"
        item = RawItem(id=sid, source="analyst", kind="analyst", published=when, title=f"Working range set by {analyst}",
                       text=note or f"Working range USD {low:g}-{high:g} billion", licence="analyst")
        self.store.add_source(item, when, item.text, [], f"EV-{sid}")
        f = Fact(id=f"F-{sid}", source_id=sid, entity="", topic="analyst_range",
                 claim=f"{analyst} set the working range at USD {low:g}-{high:g} billion.", as_of=when, licence="analyst",
                 data={"low": low, "high": high, "analyst": analyst}, confidence=1.0, event_id=f"EV-{sid}")
        self.store.add_fact(f)
        ev = Event(id=f"EV-{sid}", kind="analyst", entity="", title=item.title, date=when, detected=when,
                   sections=["summary", "market"], score=0.0, level="low", in_scope=True, reasons=["analyst decision"],
                   item_ids=[sid], fact_ids=[f.id])
        self.store.add_event(ev)
        return ev

    def accept_entity(self, name: str, when: str) -> Event:
        e = self.store.entity(name)
        self.store.upsert_entity(name, "competitor", e["aliases"] if e else [], True, discovered=when)
        self.interp = Interpreter(self.brief, self.store)
        ev = Event(id=f"EV-accept-{name}-{when}", kind="analyst", entity=name, title=f"Started tracking {name}", date=when,
                   detected=when, sections=["summary", "competition"], score=0.0, level="low", in_scope=True,
                   reasons=["analyst accepted a company found by the radar"])
        self.store.add_event(ev)
        return ev

    # ------------------------------------------------------------ drafting
    def _write(self, sid: str, ctx: Ctx, evidence_events: list[Event]) -> Section:
        if self._claude_writer:
            with self.tracer.span(sid, "writer", "section") as rec:
                sec, note = self._claude_writer.write(sid, ctx, evidence_events)
                rec.update(note)
                if sec is not None:
                    res = check_section(sec, self.store, ctx.as_of)
                    if not res.blocked:
                        return sec
                    rec["detail"] = {**rec.get("detail", {}), "fallback": "fact-check failed", "issues": res.summary()["issues"]}
        return write(sid, ctx)

    def pending_events(self) -> list[Event]:
        drafts = {v["n"] for v in self.store.versions() if v["status"] == "draft"}
        return [e for e in self.store.events() if self.store.event_version(e.id) in (None, *drafts) and e.sections]

    def draft(self, as_of: str, force_all: bool = False, note: str = "") -> dict | None:
        """Rewrite only the sections the pending events touch. Returns the new draft version, or None."""
        self.ensure_brief_fact(as_of)
        events = self.pending_events()
        base_v = self.store.latest("published")
        base = base_v["report"] if base_v else None
        touched = set(SECTION_ORDER) if (force_all or base is None) else {s for e in events for s in e.sections}
        if not touched:
            return None
        for v in self.store.versions():
            if v["status"] == "draft":
                self.store.set_version_status(v["n"], "superseded")
        ctx = Ctx(self.store, self.brief, as_of)
        rep = Report(watch_id=self.brief.id, as_of=as_of)
        changed = []
        for sid in SECTION_ORDER:
            if sid in touched:
                sec = self._write(sid, ctx, [e for e in events if sid in e.sections])
                old = base.sections.get(sid) if base else None
                if old is None or old.plain() != sec.plain() or [s.cites for s in old.sentences()] != [s.cites for s in sec.sentences()]:
                    changed.append(sid)
                    rep.sections[sid] = sec
                else:
                    rep.sections[sid] = old
            else:
                rep.sections[sid] = base.sections[sid]
        if base is not None and not changed:
            for e in events:
                self.store.mark_events([e.id], base_v["n"])
            return None
        checks = check_report(rep, self.store, only=changed).summary()
        n = (self.store.latest() or {"n": 0})["n"] + 1
        self.store.save_version(n, as_of, rep, "draft", base_v["n"] if base_v else None, changed, [e.id for e in events], checks,
                                note)
        self.store.mark_events([e.id for e in events], n)
        return self.store.version(n)

    def approve(self, n: int, analyst: str, note: str = "", remove_judgement: bool = False) -> dict:
        v = self.store.version(n)
        if v is None or v["status"] != "draft":
            raise appr.PublishRefused(f"version {n} is not an open draft")
        rep: Report = v["report"]
        if remove_judgement:
            for sec in rep.sections.values():
                for b in sec.blocks:
                    b.sentences = [s for s in b.sentences if s.kind != "judgement"]
            v["checks"] = check_report(rep, self.store, only=v["changed"]).summary()
            self.store.save_version(n, v["created"], rep, "draft", v["parent"], v["changed"], v["event_ids"], v["checks"], note)
        a = appr.approve(rep.content_hash, analyst, self.secret, note)
        appr.verify(a, rep.content_hash, self.secret, v["checks"])
        self.store.set_version_status(n, "published", a, note)
        from radar.report.digest import build_digest, deliver

        dg = build_digest(self, self.store.version(n))
        deliver(dg, self.brief)
        return self.store.version(n)

    def send_back(self, n: int, analyst: str, comments: str) -> None:
        self.store.set_version_status(n, "returned", {"name": analyst, "decision": "returned", "note": comments}, comments)
        for e in self.store.events():
            if self.store.event_version(e.id) == n:
                self.store.db.execute("UPDATE events SET version=NULL WHERE id=?", (e.id,))
        self.store.db.commit()

    # ------------------------------------------------------------ replay
    def bootstrap(self, start: str, analyst: str = "Demo analyst") -> dict:
        """First edition: everything published up to `start`, all sections written and published."""
        self.tick(start, force=True)
        v = self.draft(start, force_all=True, note="First edition")
        return self.approve(v["n"], analyst, "First edition") if v else self.store.latest()

    def run(self, until: str, start: str | None = None, auto_approve: bool = True, analyst: str = "Demo analyst",
            on_day=None) -> None:
        start = start or str(self.scenario["start"])
        if not self.store.latest():
            self.bootstrap(start, analyst)
        last = self.store.latest()["as_of"]
        for day in _days((date.fromisoformat(last) + timedelta(days=1)).isoformat(), until):
            new = self.tick(day)
            weekly = date.fromisoformat(day).weekday() == self.brief.refresh.get("report_weekday", 0)
            if any(e.level == "high" for e in new) or (weekly and self.pending_events()):
                v = self.draft(day)
                if v and auto_approve and not v["checks"].get("blocked"):
                    self.approve(v["n"], analyst, "auto-approved in replay")
            if on_day:
                on_day(day, new)
