"""Read models for the UI: the numbers and tables each screen shows, computed from the store.

Kept out of the Streamlit code so they can be tested and reused (CLI, exports).
"""

from __future__ import annotations

from datetime import date, timedelta

from radar.config import SECTION_ORDER, section_titles, sources_config
from radar.report.render import Citations, redline_html
from radar.report.writer import Ctx, fmt_date, threat
from radar.safety import licence


def ctx(radar, as_of: str | None = None) -> Ctx:
    latest = radar.store.latest()
    return Ctx(radar.store, radar.brief, as_of or (latest["as_of"] if latest else date.today().isoformat()))


def status(radar) -> dict:
    pub, last = radar.store.latest("published"), radar.store.latest()
    draft = last if last and last["status"] == "draft" else None
    as_of = last["as_of"] if last else None
    checked = max([p["last"] for p in radar.store.pulls()] + [radar.store.get("clock") or ""] + [as_of or ""]) or None
    nxt = None
    if checked:
        d = date.fromisoformat(checked) + timedelta(days=1)
        while d.weekday() != radar.brief.refresh.get("report_weekday", 0):
            d += timedelta(days=1)
        nxt = d.isoformat()
    return {"published": pub, "draft": draft, "as_of": as_of, "checked": checked, "next_refresh": nxt}


def estimates(radar, as_of: str | None = None) -> dict:
    c = ctx(radar, as_of)
    pairs, rng = c.estimates()
    rows = []
    for f, _prev in pairs:
        src = radar.store.source(f.source_id) or {}
        rows.append({"source": c.source_label(f)[0].upper() + c.source_label(f)[1:], "kind": licence.kind_of(f.licence),
                     "low": f.data["value_low"], "high": f.data["value_high"], "year": f.data.get("year"),
                     "cagr": f.data.get("cagr"), "period": f.data.get("cagr_period"), "date": f.as_of, "fact": f.id,
                     "title": src.get("title", "")})
    out = {"rows": rows, "working_range": None, "spread_pct": None, "cagr_range": None}
    if rows:
        lo = min(r["low"] for r in rows)
        hi = max(r["high"] for r in rows)
        out.update(low=lo, high=hi, spread_pct=round((hi - lo) / lo * 100) if lo else None)
        cagrs = [r["cagr"] for r in rows if r["cagr"] is not None]
        if cagrs:
            out["cagr_range"] = (min(cagrs), max(cagrs))
    if rng:
        out["working_range"] = {"low": rng.data["low"], "high": rng.data["high"], "by": rng.data.get("analyst"), "date": rng.as_of}
    return out


def changes_since_published(radar) -> list:
    st = status(radar)
    if st["draft"]:
        return [e for e in (radar.store.event(i) for i in st["draft"]["event_ids"]) if e]
    pub = st["published"]
    return [e for e in (radar.store.event(i) for i in (pub["event_ids"] if pub else [])) if e]


def recent_events(radar, limit: int = 12, include_noise: bool = False) -> list:
    evs = [e for e in radar.store.events() if include_noise or e.level != "noise"]
    return list(reversed(evs))[:limit]


def _headline(c: Ctx, f) -> str:
    """Short description of a fact for tables: the source headline when it may be quoted."""
    src = c.store.source(f.source_id) or {}
    if licence.can_quote(f.licence) and src.get("title") and src.get("connector") not in ("upload",):
        return src["title"]
    return c.say(f).split(". ")[0][:120]


def competitor_rows(radar, as_of: str | None = None) -> list[dict]:
    c = ctx(radar, as_of)
    latest = c.latest_by_entity()
    cl = {f.entity: f for f in c.clearances()}
    rows = []
    for name in c.tracked() + [e["name"] for e in c.discovered()]:
        fs = [f for f in latest.get(name, []) if f.topic not in ("patent", "hiring", "other")]
        if not fs:
            continue
        ce = next((f for f in fs if f.topic == "certification"), None)
        sub = next((f for f in fs if "510(k)" in f.claim and f.topic != "clearance" and "review" in f.claim), None)
        th = threat(c, name, fs)
        level = th.text.split(":")[1].split(".")[0].strip() if th else "Watch"
        rows.append({"name": name, "tracked": name in c.tracked(),
                     "us": (f"510(k) cleared, {fmt_date(cl[name].as_of)}", cl[name].id) if name in cl else
                           (("Under FDA review", sub.id) if sub else ("No clearance found", None)),
                     "eu": ("CE marked (IVDR)", ce.id) if ce else ("—", None),
                     "latest": (_headline(c, fs[0]), fs[0].id), "latest_date": fs[0].as_of,
                     "threat": level if name in c.tracked() else "Watch"})
    order = {"High": 0, "Medium": 1, "Low": 2, "Watch": 3}
    rows.sort(key=lambda x: (order.get(x["threat"], 4), x["name"]))
    return rows


def competitor_profile(radar, name: str, as_of: str | None = None) -> dict:
    c = ctx(radar, as_of)
    facts = sorted([f for f in c.facts(entity=name)], key=lambda f: f.as_of, reverse=True)
    timeline = []
    for f in facts:
        src = radar.store.source(f.source_id) or {}
        conn = src.get("connector", "")
        timeline.append({"date": f.as_of, "title": src.get("title") or f.claim[:100],
                         "claim": f.claim if licence.can_quote(f.licence) else c.say(f),
                         "source": sources_config().get(conn, {}).get("label", conn), "kind": licence.kind_of(f.licence),
                         "topic": f.topic, "fact": f.id, "url": src.get("url", "")})
    rel = [f for f in facts if f.topic not in ("patent", "hiring", "other")]
    th = threat(c, name, rel)
    cl = next((f for f in c.clearances() if f.entity == name), None)
    ce = next((f for f in rel if f.topic == "certification"), None)
    price = next((f for f in rel if "price_low" in f.data), None)
    ys = radar.brief.your_solution
    compare = [
        ("US clearance", fmt_date(cl.as_of) if cl else "Not cleared", f"Planned {ys.us_filing_year}" if ys.us_filing_year else ys.status),
        ("EU (IVDR)", "CE marked" if ce else "—", "CE marked" if "CE" in ys.status else "—"),
        ("Latest price seen", f"{price.data.get('currency', '')} {price.data['price_low']:g} per test" if price else "—", "[YOUR PRICE]"),
    ]
    watch = []
    topics = {f.topic for f in rel}
    if cl and "launch" not in topics:
        watch.append("US launch date and first distribution partner")
    if cl or "launch" in topics:
        watch.append("Adverse event reports and recalls after launch (FDA MAUDE and recalls are already watched)")
    if any(f.topic in ("trial", "trial_update") and f.data.get("status") in ("RECRUITING", "ACTIVE_NOT_RECRUITING") for f in rel):
        watch.append("Results from its open studies")
    if any(f.topic == "trial_update" and f.data.get("status") == "COMPLETED" for f in rel) and not cl:
        watch.append("FDA decision: the pivotal study has completed")
    if "safety" in topics or "fda_recall" in topics:
        watch.append("Whether the field safety issue recurs in new lots")
    if "pricing" in topics:
        watch.append("Further tender price cuts in the regions you sell in")
    if not watch:
        watch.append("Regulatory filings and first commercial launch")
    summary = [s for s in [th] if s]
    return {"name": name, "threat": th.text.split(":")[1].split(".")[0].strip() if th else "Watch",
            "threat_reason": th.text.split(". ", 1)[1] if th and ". " in th.text else "", "summary": summary,
            "timeline": timeline, "compare": compare, "watch": watch, "tracked": bool((radar.store.entity(name) or {}).get("tracked"))}


def redline(radar, n: int) -> dict:
    v = radar.store.version(n)
    base = radar.store.version(v["parent"]) if v and v.get("parent") else None
    cites = Citations(v["report"], radar.store, SECTION_ORDER)
    out = {}
    for sid in SECTION_ORDER:
        new = v["report"].sections[sid]
        old = base["report"].sections.get(sid) if base else None
        if sid in v["changed"]:
            h, edits = redline_html(old, new, cites)
        else:
            from radar.report.render import section_html

            h, edits = section_html(new, cites), 0
        out[sid] = {"title": section_titles()[sid], "html": h, "edits": edits, "changed": sid in v["changed"]}
    return {"version": v, "sections": out, "sources": cites.sources(), "cites": cites}


def section_sources(radar, n: int, sid: str) -> list[dict]:
    v = radar.store.version(n)
    cites = Citations(v["report"], radar.store, SECTION_ORDER)
    nums = sorted({m for s in v["report"].sections[sid].sentences() for m in cites.marks(s)})
    srcs = {s["n"]: s for s in cites.sources()}
    out = []
    for m in nums:
        s = srcs[m]
        fact_ids = [fid for fid, src in cites.fact_src.items() if src == s["id"]]
        conf = max([radar.store.fact(f).confidence for f in fact_ids if radar.store.fact(f)] or [0])
        eid = s.get("event_id")
        corroborated = max(0, sum(1 for x in radar.store.sources() if eid and x["event_id"] == eid) - 1)
        out.append({**s, "confidence": conf, "corroborated": corroborated,
                    "label": sources_config().get(s.get("connector", ""), {}).get("label", s.get("connector", ""))})
    return out
