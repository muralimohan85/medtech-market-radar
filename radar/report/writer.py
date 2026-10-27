"""Deterministic section writers.

Each writer builds one report section from facts in the evidence store, and every
sentence carries the ids of the facts it rests on. Public and upload facts may be
quoted; licensed facts are restated from their structured numbers so the vendor's
wording never appears. Sentences that compare a competitor with your own plan are
marked as judgement calls so the analyst decides whether to keep them.

The Claude engine (radar/agents/writer.py) writes the same sections in better prose
under the same rules; this module is its fallback and its baseline in the evaluation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from radar.config import section_titles, sources_config
from radar.safety import licence
from radar.schemas import Block, Fact, Section, Sentence, WatchBrief
from radar.store import Store

STATUS_TEXT = {"RECRUITING": "is recruiting for", "ACTIVE_NOT_RECRUITING": "has finished recruiting for",
               "COMPLETED": "has completed", "NOT_YET_RECRUITING": "has registered", "SUSPENDED": "has suspended",
               "TERMINATED": "has terminated", "WITHDRAWN": "has withdrawn", "ENROLLING_BY_INVITATION": "is enrolling for"}
NOT_REPORT_TOPICS = {"patent", "hiring", "other", "coverage"}


def fmt_date(d: str) -> str:
    try:
        x = date.fromisoformat(d[:10])
        return f"{x.day} {x.strftime('%B %Y')}"
    except ValueError:
        return d


def fmt_bn(lo: float, hi: float, unit: str = "USD bn") -> str:
    cur = (unit or "USD bn").split()[0]
    return f"{cur} {lo:g} billion" if lo == hi else f"{cur} {lo:g}-{hi:g} billion"


def S(text: str, *facts: Fact | None, kind: str = "fact") -> Sentence:
    return Sentence(text=text, cites=[f.id for f in facts if f is not None], kind=kind)


def P(*sentences: Sentence | None) -> Block:
    return Block(type="p", sentences=[s for s in sentences if s is not None])


@dataclass
class Ctx:
    store: Store
    brief: WatchBrief
    as_of: str

    # ---------------------------------------------------------------- lookups
    def facts(self, topic=None, entity=None) -> list[Fact]:
        return self.store.facts(as_of=self.as_of, topic=topic, entity=entity)

    def in_report(self, f: Fact) -> bool:
        if not f.event_id:
            return True
        ev = self.store.event(f.event_id)
        return ev is None or (ev.in_scope and (ev.level != "noise" or ev.kind in ("market_estimate", "coverage")))

    def tracked(self) -> list[str]:
        return [e["name"] for e in self.store.entities() if e["tracked"]]

    def discovered(self) -> list[dict]:
        return [e for e in self.store.entities() if not e["tracked"] and e["discovered"] and e["discovered"] <= self.as_of]

    def brief_fact(self) -> Fact | None:
        return self.store.fact(f"F-brief-{self.brief.id}")

    def source_label(self, f: Fact) -> str:
        src = self.store.source(f.source_id) or {}
        conn = src.get("connector", "")
        if conn == "upload":
            return f"your upload '{src.get('title', '')}'"
        return sources_config().get(conn, {}).get("label", conn or "source")

    def say(self, f: Fact) -> str:
        """The fact as a sentence: its own words when the licence allows, otherwise restated from its numbers."""
        if licence.can_quote(f.licence):
            return f.claim
        d = f.data
        if d.get("metric") == "market_value":
            return self.estimate_sentence(f)
        if d.get("metric") == "regional_share":
            return self.share_sentence(f)
        return f"{self.source_label(f)} published an update on {fmt_date(f.as_of)}."

    def estimate_sentence(self, f: Fact, prev: Fact | None = None) -> str:
        d = f.data
        who = self.source_label(f)
        who = who[0].upper() + who[1:]
        size = fmt_bn(d["value_low"], d["value_high"], d.get("unit"))
        s = f"{who} puts the {d.get('year')} {d.get('region', 'global').lower()} market at {size}"
        if d.get("cagr") is not None:
            s += f", growing {d['cagr']:g}% a year" + (f" over {d['cagr_period']}" if d.get("cagr_period") else "")
        if prev and prev.data.get("cagr") not in (None, d.get("cagr")):
            s += f" (revised from {prev.data['cagr']:g}% on {fmt_date(f.as_of)})"
        return s + "."

    def share_sentence(self, f: Fact) -> str:
        parts = [f"{k} {v:g}%" for k, v in (f.data.get("shares") or {}).items()]
        body = ", ".join(parts[:-1]) + (f" and {parts[-1]}" if len(parts) > 1 else parts[0] if parts else "")
        return f"By region, {self.source_label(f)} splits {f.data.get('year')} revenue as {body}."

    def latest_by_entity(self, topics: set[str] | None = None) -> dict[str, list[Fact]]:
        out: dict[str, list[Fact]] = defaultdict(list)
        for f in self.facts():
            if f.entity and self.in_report(f) and (topics is None or f.topic in topics):
                out[f.entity].append(f)
        for v in out.values():
            v.sort(key=lambda x: (x.as_of, x.id), reverse=True)
        return out

    def estimates(self) -> tuple[list[tuple[Fact, Fact | None]], Fact | None]:
        """Latest market-value estimate per source (with its previous one), and the analyst's working range."""
        by_src: dict[str, list[Fact]] = defaultdict(list)
        for f in self.facts(topic=["market_estimate", "upload"]):
            if f.data.get("metric") == "market_value":
                src = self.store.source(f.source_id) or {}
                key = src.get("connector", "") if src.get("connector") != "upload" else f.source_id
                by_src[key].append(f)
        pairs = []
        for fs in by_src.values():
            fs.sort(key=lambda x: x.as_of)
            pairs.append((fs[-1], fs[-2] if len(fs) > 1 else None))
        pairs.sort(key=lambda p: -p[0].data["value_high"])
        rng = [f for f in self.facts(topic="analyst_range")]
        return pairs, (rng[-1] if rng else None)

    def clearances(self) -> list[Fact]:
        """One fact per cleared product: the FDA record when present, else the news that reported it."""
        best: dict[str, Fact] = {}
        for f in self.facts(topic=["clearance", "fda_510k"]):
            if not self.in_report(f) and f.topic != "fda_510k":
                continue
            key = f.event_id or f.id
            if key not in best or f.topic == "fda_510k":
                best[key] = f
        return sorted(best.values(), key=lambda f: f.as_of)


# ==================================================================== sections
def _section(sid: str, blocks: list[Block], ctx: Ctx) -> Section:
    return Section(id=sid, title=section_titles()[sid], blocks=[b for b in blocks if b.type == "h3" or b.sentences],
                   as_of=ctx.as_of)


def write_summary(ctx: Ctx) -> Section:
    pairs, rng = ctx.estimates()
    s1 = None
    if len(pairs) >= 2:
        lo, hi = pairs[-1][0], pairs[0][0]
        s1 = S(f"Published estimates for the {hi.data.get('year')} market range from {fmt_bn(lo.data['value_low'], lo.data['value_high'])} "
               f"to {fmt_bn(hi.data['value_low'], hi.data['value_high'])}; "
               "the market section shows them side by side rather than averaging them.",
               lo, hi)
    elif pairs:
        s1 = S(ctx.estimate_sentence(pairs[0][0]), pairs[0][0])
    s_rng = S(f"Your working range is {fmt_bn(rng.data['low'], rng.data['high'])}.", rng) if rng else None

    latest = ctx.latest_by_entity()
    tracked = [t for t in ctx.tracked() if latest.get(t)]
    s2 = S(f"The radar tracks {len(tracked)} competitors: {', '.join(tracked)}.", *[latest[t][0] for t in tracked]) if tracked else None

    cl = [f for f in ctx.clearances() if f.entity in ctx.tracked()]
    brief = ctx.brief_fact()
    s3 = s4 = None
    if cl:
        f = cl[0]
        s3 = S(f"{f.entity} was the first tracked company to receive FDA 510(k) clearance for a point-of-care "
               f"{ctx.brief.test_name} test, on {fmt_date(f.as_of)}.", f)
        if brief and ctx.brief.your_solution.us_filing_year:
            s4 = S(f"That puts {f.entity} in the US market ahead of your planned {ctx.brief.your_solution.us_filing_year} US filing.",
                   f, brief, kind="judgement")
    else:
        cov = ctx.facts(topic="coverage")
        if cov:
            s3 = S(f"No tracked company had FDA clearance for a point-of-care {ctx.brief.test_name} test "
                   f"as of {fmt_date(cov[-1].as_of)}.", cov[-1])

    highs = []
    for ev in ctx.store.events():
        if ev.level == "high" and ev.detected <= ctx.as_of and ev.fact_ids and FAMILY_CLEAR.isdisjoint({ev.kind}):
            f = ctx.store.fact(ev.fact_ids[0])
            if f:
                highs.append(f)
    s5 = [S(ctx.say(f), f) for f in highs[-2:]]
    return _section("summary", [P(s1, s_rng, s2, s3, s4, *s5)], ctx)


FAMILY_CLEAR = {"clearance", "fda_510k"}


def write_market(ctx: Ctx) -> Section:
    pairs, rng = ctx.estimates()
    est = [S(ctx.estimate_sentence(f, prev), f, prev) for f, prev in pairs]
    rs = None
    if rng:
        rs = S(f"The analyst working range, set on {fmt_date(rng.as_of)}, is {fmt_bn(rng.data['low'], rng.data['high'])}.", rng)
    shares = [f for f in ctx.facts(topic="regional_share")]
    sh = S(ctx.share_sentence(shares[-1]), shares[-1]) if shares else None
    return _section("market", [P(*est), P(rs), P(sh)], ctx)


def write_segments(ctx: Ctx) -> Section:
    pubs = [f for f in ctx.facts(topic=["publication_general"]) if ctx.in_report(f)]
    pubs.sort(key=lambda f: f.as_of, reverse=True)
    return _section("segments", [P(*[S(ctx.say(f), f) for f in pubs[:4]])], ctx)


def threat(ctx: Ctx, entity: str, facts: list[Fact]) -> Sentence | None:
    regions = set(ctx.brief.regions)
    cleared_us = [f for f in ctx.clearances() if f.entity == entity]
    ce = [f for f in facts if f.topic == "certification"]
    safety = [f for f in facts if f.topic in ("safety", "fda_recall")]
    if cleared_us and "United States" in regions:
        return S("Threat level: High. It is FDA-cleared for the US market.", cleared_us[0], kind="assessment")
    if ce:
        extra = " A recent field safety action may slow it down." if safety else ""
        return S(f"Threat level: Medium. It is CE marked and selling in Europe.{extra}", ce[0], *(safety[:1]), kind="assessment")
    if facts:
        return S("Threat level: Low. Its product is still in development.", facts[0], kind="assessment")
    return None


def write_competition(ctx: Ctx) -> Section:
    latest = ctx.latest_by_entity()
    tracked = [t for t in ctx.tracked() if latest.get(t)]
    blocks = []
    intro = S(f"{len(tracked)} tracked companies sell or are developing point-of-care {ctx.brief.test_name} tests "
              f"for the watched regions: {', '.join(tracked)}.", *[latest[t][0] for t in tracked]) if tracked else None
    cl = [f for f in ctx.clearances() if f.entity in tracked]
    us = (S(f"In the US, {cl[0].entity} received FDA 510(k) clearance on {fmt_date(cl[0].as_of)}.", cl[0]) if cl else None)
    ce = [latest_t for t in tracked for latest_t in latest[t] if latest_t.topic == "certification"]
    eu = (S(f"In the EU, {len(ce)} tracked companies hold IVDR CE marking: "
            + ", ".join(f"{f.entity} ({fmt_date(f.as_of)})" for f in sorted(ce, key=lambda x: x.as_of)) + ".", *ce) if ce else None)
    blocks.append(P(intro, us, eu))
    brief = ctx.brief_fact()
    for t in tracked:
        fs = [f for f in latest[t] if f.topic not in NOT_REPORT_TOPICS]
        blocks.append(Block(type="h3", text=t))
        th = threat(ctx, t, fs)
        jud = None
        if th and th.text.startswith("Threat level: High") and brief and ctx.brief.your_solution.us_filing_year:
            jud = S(f"It reaches US emergency departments before your planned {ctx.brief.your_solution.us_filing_year} US filing.",
                    *[ctx.store.fact(c) for c in th.cites], brief, kind="judgement")
        used = set(th.cites if th else [])
        recent = [S(ctx.say(f), f) for f in fs if f.id not in used][:3]
        blocks.append(P(th, jud, *recent))
    for e in ctx.discovered():
        fs = [f for f in ctx.facts(entity=e["name"]) if ctx.in_report(f)]
        if fs:
            blocks.append(Block(type="h3", text=f"{e['name']} (found by the radar, not in your brief)"))
            blocks.append(P(*[S(ctx.say(f), f) for f in fs[-2:]]))
    return _section("competition", blocks, ctx)


def write_pricing(ctx: Ctx) -> Section:
    fs = [f for f in ctx.facts() if "price_low" in f.data and ctx.in_report(f)]
    fs.sort(key=lambda f: f.as_of, reverse=True)
    return _section("pricing", [P(*[S(ctx.say(f), f) for f in fs[:6]])], ctx)


def write_pipeline(ctx: Ctx) -> Section:
    trials: dict[str, Fact] = {}
    for f in ctx.facts(topic=["trial", "trial_update"]):
        if "nct_id" in f.data:
            nct = f.data.get("nct_id", f.id)
            ev = ctx.store.event(f.event_id) if f.event_id else None
            if ev is None or ev.in_scope:
                trials[nct] = f                                 # latest status wins
    sents = []
    for nct, f in sorted(trials.items(), key=lambda kv: kv[1].data.get("start_date") or ""):
        d = f.data
        where = ", ".join(d.get("countries") or [])
        n = d.get("enrollment")
        size = f"{n:,} patients" if isinstance(n, int) else ""
        detail = ", ".join(x for x in (size, where) if x)
        sents.append(S(f"{d.get('sponsor')} {STATUS_TEXT.get(d.get('status'), 'is running')} study {nct}"
                       + (f" ({detail})." if detail else "."), f))
    news = [f for f in ctx.facts(topic=["trial", "publication_company", "study"]) if ctx.in_report(f) and "nct_id" not in f.data]
    news.sort(key=lambda f: f.as_of, reverse=True)
    return _section("pipeline", [P(*sents), P(*[S(ctx.say(f), f) for f in news[:4]])], ctx)


def write_regulatory(ctx: Ctx) -> Section:
    cl = ctx.clearances()
    s_cl = [S(f"FDA 510(k) clearance: {f.entity}, {f.data.get('device_name') or 'point-of-care test'}, {fmt_date(f.as_of)}"
              + (f" ({f.data['k_number']})." if f.data.get("k_number") else "."), f) for f in cl]
    if not cl:
        cov = ctx.facts(topic="coverage")
        if cov:
            s_cl = [S(ctx.say(cov[-1]), cov[-1])]
    ce = [f for f in ctx.facts(topic="certification") if ctx.in_report(f)]
    saf = [f for f in ctx.facts(topic=["safety", "fda_recall"]) if ctx.in_report(f)]
    saf.sort(key=lambda f: f.as_of, reverse=True)
    return _section("regulatory", [P(*s_cl), P(*[S(ctx.say(f), f) for f in ce]), P(*[S(ctx.say(f), f) for f in saf[:4]])], ctx)


WRITERS = {"summary": write_summary, "market": write_market, "segments": write_segments, "competition": write_competition,
           "pricing": write_pricing, "pipeline": write_pipeline, "regulatory": write_regulatory}


def write(section_id: str, ctx: Ctx) -> Section:
    return WRITERS[section_id](ctx)
