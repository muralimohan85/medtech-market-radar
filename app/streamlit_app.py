"""MedTech Market Radar: Streamlit demo.

    streamlit run app/streamlit_app.py

Two watches: a fictional six-month scenario (default) that you can replay day by day,
and the same brief on real FDA data for troponin assays. No API key is needed; paste
one in the sidebar to let Claude read news and write sections.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui import esc, inject, kind_pill, kpi, level_pill, md, pill, threat_pill  # noqa: E402

from radar import views, watches  # noqa: E402
from radar.config import SECTION_ORDER, section_titles, sources_config  # noqa: E402
from radar.engine import Radar  # noqa: E402
from radar.report import export  # noqa: E402
from radar.report.approval import PublishRefused  # noqa: E402
from radar.safety import licence  # noqa: E402
from radar.schemas import WatchBrief  # noqa: E402

st.set_page_config(page_title="MedTech Market Radar", page_icon="📡", layout="wide")
inject()

DEMO_STOP = "2026-10-21"      # replay runs to here with editions auto-approved ...
DEMO_TODAY = "2026-10-22"     # ... then today's pull leaves a draft for you to review


# ====================================================================== state
def _claude():
    key = st.session_state.get("api_key", "").strip()
    if not key:
        return {}
    from radar.llm.claude import make_client

    return {"engine": "claude", "client": make_client(key), "model": st.session_state.get("model")}


def build_demo() -> Radar:
    r = Radar.for_scenario(**_claude())
    r.run(DEMO_STOP)
    r.tick(DEMO_TODAY)
    r.draft(DEMO_TODAY)
    return r


def build_real(live_apis: bool) -> Radar:
    r = Radar.live("hs_troponin_poc_live", db=":memory:", offline=not live_apis, **_claude())
    today = date.today().isoformat()
    r.run(today, start="2023-01-01")
    return r


def build_user(watch_id: str, live_apis: bool) -> Radar:
    """One of your saved watches. Its evidence database lives in the workspace, so it persists between sessions."""
    r = Radar.live(watch_id, offline=not live_apis, **_claude())
    if not r.store.latest():
        r.bootstrap(date.today().isoformat(), "You")
    return r


def radar() -> Radar:
    key = st.session_state.get("watch", "demo")
    cache = st.session_state.setdefault("radars", {})
    if key not in cache:
        live = key.startswith("user:") or st.session_state.get("live_apis", False)
        with st.spinner("Pulling sources and building the report…"):
            if key == "demo":
                cache[key] = build_demo()
            elif key == "real":
                cache[key] = build_real(live)
            else:
                wid = key.split(":", 1)[1]
                if watches.find(wid) is None:
                    # the saved file is gone, e.g. Streamlit Cloud reset its storage on a restart or redeploy
                    st.warning(f"The saved watch '{wid}' is no longer in this app's storage (hosted apps lose saved files when they "
                               "restart). Showing the demo instead. Recreate it, or import its YAML on the Watches page.")
                    st.session_state["watch"] = "demo"
                    st.session_state["next_watch"] = "demo"
                    cache.pop(key, None)
                    return radar()
                cache[key] = build_user(wid, live)
    return cache[key]


def open_watch(key: str) -> None:
    """Switch to a watch from a page (the sidebar picks it up on the next run)."""
    st.session_state["next_watch"] = key
    st.session_state.get("radars", {}).pop(key, None)
    st.switch_page(PAGES["overview"])


BUILTIN_KEYS = {"hs_troponin_poc": "demo", "hs_troponin_poc_live": "real"}


def flash(msg: str, kind: str = "success") -> None:
    st.session_state["flash"] = (msg, kind)


def show_flash() -> None:
    f = st.session_state.pop("flash", None)
    if f:
        getattr(st, f[1])(f[0])


# ====================================================================== sidebar
with st.sidebar:
    st.markdown("#### Watch")
    yours = [w for w in watches.list_watches() if w["kind"] == "yours"]
    options = ["demo", "real"] + [f"user:{w['id']}" for w in yours]
    labels = {"demo": "Demo scenario (fictional)", "real": "Real FDA data: troponin",
              **{f"user:{w['id']}": f"{w['name']}  ·  yours" for w in yours}}
    if st.session_state.get("next_watch") in options:
        st.session_state["watch_pick"] = st.session_state.pop("next_watch")
    if st.session_state.get("watch_pick") not in options:
        st.session_state["watch_pick"] = "demo"
    st.radio("Watch", options, format_func=lambda k: labels.get(k, k), label_visibility="collapsed", key="watch_pick")
    st.session_state["watch"] = st.session_state["watch_pick"]
    if st.session_state["watch"] == "real":
        st.checkbox("Try the live APIs (otherwise the committed openFDA snapshot)", key="live_apis")
    elif st.session_state["watch"].startswith("user:"):
        st.caption("Your watches pull live public sources. Create more on the Watches page.")
    st.markdown("#### Engine")
    st.text_input("Anthropic API key (optional)", type="password", key="api_key",
                  help="Without a key the deterministic engine runs. With a key, Claude reads news and writes sections; "
                       "every draft still goes through the same fact-checker.")
    st.selectbox("Model", ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"], key="model")
    if st.button("Rebuild watch", width="stretch"):
        st.session_state.get("radars", {}).pop(st.session_state["watch"], None)
        st.rerun()
    st.caption("Companies, products and figures in the demo scenario are fictional. The real-data watch uses public "
               "openFDA records.")


# ====================================================================== shared bits
def header(r: Radar, crumb: str | None = None) -> None:
    b = r.brief
    md(f'<div class="mr-eyebrow">{esc(b.device_type)} · {esc(b.diagnostic_solution)} · {esc(", ".join(b.regions))}</div>')
    if crumb:
        md(f'<div class="mr-eyebrow" style="margin-top:2px">{esc(b.name)} / {esc(crumb)}</div>')


def replay_controls(r: Radar) -> None:
    if not hasattr(r, "scenario"):
        return
    with st.expander(f"Replay clock: {today(r)}", expanded=False):
        st.caption("Move time forward. Each day the radar pulls whatever is due (news daily, FDA and trials weekly, "
                   "licensed databases monthly). High-impact changes and the Monday refresh open a draft for review.")
        c1, c2, c3 = st.columns(3)
        if c1.button("Next day", width="stretch"):
            advance(r, 1)
        if c2.button("Next Monday", width="stretch"):
            d = date.fromisoformat(today(r)) + timedelta(days=1)
            while d.weekday() != 0:
                d += timedelta(days=1)
            advance(r, (d - date.fromisoformat(today(r))).days)
        if c3.button("Restart at 1 May", width="stretch"):
            st.session_state["radars"].pop("demo", None)
            r2 = Radar.for_scenario(**_claude())
            r2.bootstrap("2026-05-01")
            st.session_state["radars"]["demo"] = r2
            st.rerun()


def advance(r: Radar, days: int) -> None:
    start = date.fromisoformat(today(r))
    found, d = [], today(r)
    for i in range(1, days + 1):
        d = (start + timedelta(days=i)).isoformat()
        new = r.tick(d)
        found += [e for e in new if e.level != "noise"]
        weekly = (start + timedelta(days=i)).weekday() == 0
        if any(e.level == "high" for e in new) or (weekly and r.pending_events()):
            r.draft(d)
        r.store.put("clock", d)
    flash(f"Moved to {d}. {len(found)} new change(s)." + (" A draft is ready for review." if views.status(r)["draft"] else ""))
    st.rerun()


def today(r: Radar) -> str:
    """The replay clock: the later of the last edition and the last day the clock was moved to."""
    s = views.status(r)
    c = r.store.get("clock")
    vals = [x for x in (s["as_of"], c) if x]
    return max(vals) if vals else DEMO_TODAY


# ====================================================================== pages
def page_overview() -> None:
    r = radar()
    show_flash()
    s = views.status(r)
    header(r)
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"# {r.brief.name}")
        pub, dr = s["published"], s["draft"]
        line = f"Report v{pub['n']} published {pub['as_of']}" if pub else "No published edition yet"
        if dr:
            line += f" · draft v{dr['n']} ready for review"
        if s["checked"] and s["checked"] != (pub or {}).get("as_of"):
            line += f" · sources checked {s['checked']}"
        if s["next_refresh"]:
            line += f" · next full refresh Mon {s['next_refresh']}"
        md(f'<div class="mr-sub">{esc(line)}</div>')
    with right:
        b1, b2, b3 = st.columns(3)
        if dr and b1.button(f"Review v{dr['n']}", type="primary", width="stretch"):
            st.switch_page(PAGES["review"])
        with b2.popover("Export", width="stretch"):
            export_menu(r)
        if b3.button("Refresh now", width="stretch"):
            new = r.tick(today(r), force=True)
            v = r.draft(today(r))
            flash(f"Checked every source. {len([e for e in new if e.level != 'noise'])} new change(s)."
                  + (f" Draft v{v['n']} is ready." if v else " No section needed rewriting."))
            st.rerun()
    replay_controls(r)

    est = views.estimates(r)
    changes = views.changes_since_published(r)
    counts = {lv: sum(1 for e in changes if e.level == lv) for lv in ("high", "medium", "low")}
    rows = views.competitor_rows(r)
    tracked = [x for x in rows if x["tracked"]]
    new_found = [x for x in rows if not x["tracked"]]
    cleared = sum(1 for x in tracked if x["us"][0].startswith("510(k)"))
    k = st.columns(4)
    if est["rows"]:
        k[0].markdown(kpi(f"Market size {est['rows'][0]['year']}", f"${est['low']:g}–{est['high']:g}B",
                          f"{len(est['rows'])} sources disagree by up to {est['spread_pct']}%"), unsafe_allow_html=True)
        cg = est["cagr_range"]
        k[1].markdown(kpi("Growth forecast, a year", f"{cg[0]:g}–{cg[1]:g}%" if cg else "—",
                          "from " + ", ".join(sorted({x['kind'] for x in est['rows']})) + " sources"), unsafe_allow_html=True)
    else:
        k[0].markdown(kpi("Market size", "—", "no estimate yet: add a licensed source or upload"), unsafe_allow_html=True)
        k[1].markdown(kpi("Growth forecast", "—", ""), unsafe_allow_html=True)
    k[2].markdown(kpi("Competitors tracked", str(len(tracked)),
                      f"{cleared} cleared in the US" + (f" · {len(new_found)} new found" if new_found else "")), unsafe_allow_html=True)
    lab = "Changes in this draft" if s["draft"] else "Changes in the last edition"
    k[3].markdown(kpi(lab, str(sum(counts.values())), " · ".join(f"{v} {k_}" for k_, v in counts.items() if v) or "none"),
                  unsafe_allow_html=True)
    st.write("")

    c1, c2 = st.columns([3, 2])
    with c1:
        feed = []
        for e in sorted(changes, key=lambda e: ({"high": 0, "medium": 1, "low": 2}.get(e.level, 3), e.date)):
            if e.level == "noise" and not e.sections:
                continue
            secs = ", ".join(section_titles().get(x, x)[2:] for x in e.sections) or "no section"
            srcs = len([x for x in r.store.sources() if x["event_id"] == (e.duplicate_of or e.id)])
            feed.append(f'<div class="mr-feed">{level_pill(e.level if not e.duplicate_of else "low")}<div><span class="t">'
                        f'{esc(e.title)}</span><span class="m">Rewrites: {esc(secs)} · {srcs} source{"s" if srcs != 1 else ""}'
                        f' · {esc(e.date)}</span></div></div>')
        if est["rows"] and est["spread_pct"] and est["spread_pct"] > 15 and not est["working_range"]:
            feed.append(f'<div class="mr-feed">{level_pill("conflict")}<div><span class="t">Market size estimates disagree '
                        f'by {est["spread_pct"]}%.</span><span class="m">All kept side by side · needs your working range</span></div></div>')
        title = f"What changed since v{s['published']['n']}" if s["draft"] and s["published"] else "What changed in this edition"
        md(f'<div class="mr-card"><h4>{esc(title)}</h4><div class="mr-sub" style="margin:0 0 6px 0">Scanned {esc(today(r))}'
           f'</div>{"".join(feed) or "<p>No changes.</p>"}</div>')
        with st.expander("Everything the radar saw recently, including what it set aside"):
            evs = views.recent_events(r, 25, include_noise=True)
            st.dataframe(pd.DataFrame([{"detected": e.detected, "level": e.level, "kind": e.kind, "company": e.entity,
                                        "title": e.title, "why": "; ".join(e.reasons[-3:])} for e in evs]),
                         hide_index=True, width="stretch")
    with c2:
        market_card(r, est)
    competitor_table(r, rows)


def market_card(r: Radar, est: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**Market size estimates, {est['rows'][0]['year']}**" if est["rows"] else "**Market size estimates**")
        if est["rows"]:
            df = pd.DataFrame(est["rows"])
            df["label"] = [("Your upload" if k == "upload" else s_.replace(" (fictional)", "").replace("Sample Market ", ""))
                           + f" ({k})" for s_, k in zip(df["source"], df["kind"])]
            lo_, hi_ = df["low"].min(), df["high"].max()
            pad = (hi_ - lo_) * 0.25 or 0.1
            colours = alt.Scale(domain=["licensed", "upload", "public"], range=["#4450AE", "#9C5D0C", "#2A7449"])
            base = alt.Chart(df).encode(y=alt.Y("label:N", sort=None, title=None, axis=alt.Axis(labelLimit=220)))
            bars = base.mark_bar(height=14, cornerRadius=3).encode(
                x=alt.X("low:Q", title="USD billion", scale=alt.Scale(domain=[lo_ - pad, hi_ + pad])), x2="high:Q",
                color=alt.Color("kind:N", scale=colours, legend=None),
                tooltip=["source", "low", "high", "cagr", "period", "date"])
            ticks = base.mark_point(filled=True, size=140).encode(x="low:Q", color=alt.Color("kind:N", scale=colours, legend=None),
                                                                  tooltip=["source", "low", "high", "cagr", "period", "date"])
            ticks2 = base.mark_point(filled=True, size=140).encode(x="high:Q", color=alt.Color("kind:N", scale=colours, legend=None))
            layers = [bars, ticks, ticks2]
            wr = est["working_range"]
            if wr:
                band = alt.Chart(pd.DataFrame([wr])).mark_rect(opacity=0.12, color="#0B6176").encode(x="low:Q", x2="high:Q")
                layers.insert(0, band)
            st.altair_chart(alt.layer(*layers).properties(height=40 + 34 * len(df)), width="stretch")
            md('<p class="mr-foot">Every estimate is shown; none are averaged. You set the working range, and the report '
               'cites it as your decision.</p>')
            if wr:
                md(f'<p class="mr-foot">Working range: USD {wr["low"]:g}–{wr["high"]:g} billion, set by {esc(wr["by"])} on {esc(wr["date"])}.</p>')
            with st.popover("Set working range"):
                with st.form("range"):
                    lo = st.number_input("Low (USD bn)", value=float(min(x["low"] for x in est["rows"])), step=0.01)
                    hi = st.number_input("High (USD bn)", value=float(max(x["high"] for x in est["rows"])), step=0.01)
                    who = st.text_input("Your name", value="Murali Mohan")
                    if st.form_submit_button("Save and draft", type="primary"):
                        r.set_working_range(lo, hi, who, today(r))
                        v = r.draft(today(r))
                        flash(f"Working range saved. Draft v{v['n']} updates the summary and market sections." if v else "Saved.")
                        st.rerun()
        else:
            st.caption("No market estimates yet. Connect a licensed database or upload a report on the Sources page.")


def competitor_table(r: Radar, rows: list[dict]) -> None:
    v = views.status(r)["draft"] or views.status(r)["published"]
    rl = views.redline(r, v["n"]) if v else None
    cites = rl["cites"] if rl else None

    def sup(fid):
        if not (cites and fid and fid in cites.fact_src):
            return ""
        n = cites.num[cites.fact_src[fid]]
        return f"<sup>{n}</sup>"

    body = []
    for x in rows:
        name = esc(x["name"]) + ("" if x["tracked"] else ' <span class="pill" style="background:#ECEEFA;color:#4450AE">new</span>')
        body.append(f"<tr><td><b>{name}</b></td><td>{esc(x['us'][0])}{sup(x['us'][1])}</td><td>{esc(x['eu'][0])}{sup(x['eu'][1])}</td>"
                    f"<td>{esc(x['latest'][0])}{sup(x['latest'][1])}<br><span class='mr-foot'>{esc(x['latest_date'])}</span></td>"
                    f"<td>{threat_pill(x['threat'])}</td></tr>")
    ys = r.brief.your_solution
    body.append(f"<tr class='you'><td><b>{esc(ys.name)}</b></td><td>{'Planned ' + str(ys.us_filing_year) if ys.us_filing_year else '—'}"
                f"</td><td>{'CE marked' if 'CE' in ys.status else '—'}</td><td>{esc(ys.status)}</td><td>—</td></tr>")
    md('<div class="mr-card"><h4>Competitive landscape</h4><div style="overflow-x:auto"><table class="mr-table"><thead><tr>'
       '<th>Company</th><th>US (FDA)</th><th>EU (IVDR)</th><th>Latest move</th><th>Threat</th></tr></thead><tbody>'
       + "".join(body) + '</tbody></table></div><p class="mr-foot" style="margin-top:8px">Numbers are citations into the '
       'report\'s source list. Threat levels follow fixed rules (US clearance → High; CE marked → Medium; in development → Low).</p></div>')
    cands = [x["name"] for x in rows if not x["tracked"]]
    if cands:
        c1, c2 = st.columns([3, 1])
        c1.info(f"The radar found {', '.join(cands)}, which is not in your brief. Track it?")
        if c2.button(f"Track {cands[0]}", width="stretch"):
            r.accept_entity(cands[0], today(r))
            v = r.draft(today(r))
            flash(f"Now tracking {cands[0]}." + (f" Draft v{v['n']} adds it to the landscape." if v else ""))
            st.rerun()


def export_menu(r: Radar) -> None:
    v = views.status(r)["published"]
    if not v:
        st.caption("Publish an edition first.")
        return
    aud = st.radio("Audience", ["internal", "external"], horizontal=True,
                   help="External exports leave out sentences that rest on licensed or uploaded material.")
    evs = views.changes_since_published(r) if not views.status(r)["draft"] else [r.store.event(i) for i in v["event_ids"]]
    evs = [e for e in evs if e]
    name = f"{r.brief.id}_v{v['n']}_{aud}"
    st.download_button("Word report (.docx)", export.to_docx(v["report"], r.store, f"Market report: {r.brief.name}", aud, v["n"]),
                       f"{name}.docx", width="stretch")
    st.download_button("Slides (.pptx)", export.to_pptx(v["report"], r.store, f"Market update: {r.brief.name}", evs, aud),
                       f"{name}.pptx", width="stretch")
    st.download_button("Data (.xlsx)", export.to_xlsx(v["report"], r.store, r.store.events(), aud), f"{name}.xlsx",
                       width="stretch")


def page_review() -> None:
    r = radar()
    show_flash()
    s = views.status(r)
    v = s["draft"]
    header(r, f"Review v{s['published']['n'] if s['published'] else 0} → v{v['n']}" if v else "Report")
    if not v:
        st.markdown("# Report")
        st.info("No draft is waiting. This is the latest published edition. Move the replay clock forward or press "
                "Refresh now on the overview to look for changes.")
        v = s["published"]
        if not v:
            return
    else:
        st.markdown(f"# Review draft v{v['n']}")
    rl = views.redline(r, v["n"])
    secs = rl["sections"]
    nav, main, side = st.columns([1.1, 2.8, 1.6])
    with nav:
        md('<div class="mr-eyebrow">REPORT SECTIONS</div>')
        labels = []
        for sid in SECTION_ORDER:
            x = secs[sid]
            labels.append(f"{x['title']}" + (f"  ·  {x['edits']} edit{'s' if x['edits'] != 1 else ''}" if x["changed"] and v["status"] == "draft" else ""))
        default = next((i for i, sid in enumerate(SECTION_ORDER) if secs[sid]["changed"]), 0)
        pick = st.radio("Section", labels, index=default, label_visibility="collapsed")
        sid = SECTION_ORDER[labels.index(pick)]
        unchanged = [secs[x]["title"] for x in SECTION_ORDER if not secs[x]["changed"]]
        if v["status"] == "draft":
            md(f'<p class="mr-foot">{len(unchanged)} of {len(SECTION_ORDER)} sections unchanged and kept exactly as approved.</p>')
    with main:
        legend = ('<div class="mr-foot" style="margin-bottom:8px"><del>removed</del> <ins>added</ins> · '
                  '<span class="judgement">judgement call</span> · numbers are sources</div>') if v["status"] == "draft" and secs[sid]["changed"] else ""
        md(f'<div class="mr-report">{legend}<h2 style="margin-top:0">{esc(secs[sid]["title"])}</h2>{secs[sid]["html"]}</div>')
        flags = [i for i in v["checks"].get("issues", []) if i["severity"] == "flag" and i["section"] == sid]
        for fl in flags:
            md(f'<div class="mr-flag"><b>Judgement call flagged for you.</b> "{esc(fl["sentence"])}" uses your own plan from '
               'the watch brief, not an external source. Keep it or remove it when you publish.</div>')
        with st.expander("Sources for this section"):
            for src in views.section_sources(r, v["n"], sid):
                st.markdown(f"**[{src['n']}]** {src['title']}  \n{kind_pill(src['kind'], src['licence_label'])} "
                            f"<span class='mr-foot'>{esc(src['label'])} · published {esc(src.get('published'))} · fetched "
                            f"{esc(src.get('fetched'))} · confidence {src['confidence']:.2f}"
                            + (f" · confirmed by {src['corroborated']} more" if src["corroborated"] else "") + "</span>",
                            unsafe_allow_html=True)
                if src["kind"] == "public" and src.get("url"):
                    st.caption(src["url"])
    with side:
        srcs = views.section_sources(r, v["n"], sid)
        if srcs:
            nums = [x["n"] for x in srcs]
            pick_n = st.selectbox("Source", nums, format_func=lambda n: f"Source {n}")
            x = next(s_ for s_ in srcs if s_["n"] == pick_n)
            body = esc(r.store.source(x["id"])["text"][:420]) if licence.can_quote(x["licence"]) else \
                "Licensed source: shown in the report as numbers only, in the radar's own words."
            md(f'<div class="mr-card"><h4>Source {x["n"]}</h4>{kind_pill(x["kind"], x["licence_label"] + " · " + x["label"])}'
               f'<div style="font-size:13.5px;line-height:1.5;margin:8px 0">{body}</div><div class="mr-foot">'
               f'{esc(x["title"])} · fetched {esc(x.get("fetched"))}'
               + (f" · confirmed by {x['corroborated']} more source{'s' if x['corroborated'] > 1 else ''}" if x["corroborated"] else "")
               + f' · confidence {x["confidence"]:.2f}</div></div>')
        c = v["checks"]
        n_new = c.get("sentences", 0)
        items = [("✓" if c.get("uncited", 0) == 0 else "✗", f"Every rewritten sentence traces to a source ({c.get('cited', 0)} of {n_new})"),
                 ("✓" if c.get("unsupported_numbers", 0) == 0 else "✗", "Every number appears in the facts it cites"),
                 ("✓" if c.get("licence_breaches", 0) == 0 else "✗", "Licensed content restated, never quoted"),
                 ("✓" if c.get("injection_leaks", 0) == 0 else "✗", "No instructions from source text in the report")]
        if c.get("judgement_calls"):
            items.append(("!", f"{c['judgement_calls']} judgement call(s) use your own plan"))
        q = sum(len(x["quarantined"]) for x in r.store.sources())
        if q:
            items.append(("✓", f"{q} instruction-like sentence(s) quarantined from sources"))
        colour = {"✓": "#2A7449", "✗": "#B3321E", "!": "#845E00"}
        md('<div class="mr-card"><h4>Checks on this draft</h4>' + "".join(
            f'<div class="mr-check"><b style="color:{colour[a]}">{a}</b><span>{esc(t)}</span></div>' for a, t in items) + "</div>")
        if v["status"] == "draft":
            with st.form("publish"):
                st.markdown(f"**Publish v{v['n']}**")
                evs = [r.store.event(i) for i in v["event_ids"]]
                default_note = "; ".join(e.title for e in evs if e and e.level == "high")[:200]
                note = st.text_area("Note for the change log", value=default_note)
                who = st.text_input("Your name", value="Murali Mohan")
                drop = st.checkbox("Remove judgement calls before publishing", value=False) if c.get("judgement_calls") else False
                a, b = st.columns(2)
                ok = a.form_submit_button("Approve and publish", type="primary", width="stretch")
                back = b.form_submit_button("Send back", width="stretch")
                st.caption(f"Publishing sends the digest to {len(r.brief.digest.get('recipients', []))} recipient(s).")
            if ok:
                try:
                    pv = r.approve(v["n"], who, note, remove_judgement=drop)
                    flash(f"Published v{pv['n']}. Digest written to the outbox.")
                except PublishRefused as e:
                    flash(f"Not published: {e}", "error")
                st.rerun()
            if back:
                r.send_back(v["n"], who, note)
                flash(f"Draft v{v['n']} sent back. Its changes return to the queue.", "warning")
                st.rerun()
        else:
            a = v.get("approval") or {}
            st.caption(f"Approved by {a.get('name', '—')} · {a.get('note', '')}")
    md('<p class="mr-foot">Approval is an HMAC over the exact draft text: a draft edited after approval cannot be published.</p>')


def page_competitors() -> None:
    r = radar()
    show_flash()
    rows = views.competitor_rows(r)
    if not rows:
        header(r, "Competitors")
        st.info("No competitors found yet.")
        return
    names = [x["name"] for x in rows]
    pick = st.session_state.get("competitor") if st.session_state.get("competitor") in names else names[0]
    header(r, "Competitors")
    name = st.selectbox("Company", names, index=names.index(pick), label_visibility="collapsed", key="competitor")
    p = views.competitor_profile(r, name)
    md(f'<div style="margin-top:6px">{threat_pill("Threat: " + p["threat"])} <span class="mr-foot">{esc(p["threat_reason"])}</span></div>')
    st.markdown(f"# {name}")
    left, right = st.columns([3, 2])
    with left:
        tl = []
        for t in p["timeline"]:
            tl.append(f'<span class="d">{esc(t["date"])}</span><span class="dot"></span><div class="b"><b>{esc(t["title"][:120])}</b>'
                      f'<small>{esc(t["claim"][:240])}</small><small>{esc(t["source"])} {kind_pill(t["kind"])}</small></div>')
        md(f'<div class="mr-card"><h4>Timeline of moves</h4><div class="mr-tl">{"".join(tl)}</div></div>')
    with right:
        rows_html = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in p["compare"])
        md(f'<div class="mr-card"><h4>Against your solution</h4><table class="mr-table"><thead><tr><th></th><th>{esc(name)}</th>'
           f'<th>{esc(r.brief.your_solution.name)}</th></tr></thead><tbody>{rows_html}</tbody></table>'
           '<p class="mr-foot" style="margin-top:8px">Fill in your own price in the watch brief; the radar never guesses it.</p></div>')
        md('<div class="mr-card"><h4>What to watch next</h4><ul>' + "".join(f"<li>{esc(w)}</li>" for w in p["watch"])
           + '</ul><p class="mr-foot">Suggested from the evidence above by fixed rules.</p></div>')
        if not p["tracked"] and st.button(f"Track {name}"):
            r.accept_entity(name, today(r))
            r.draft(today(r))
            flash(f"Now tracking {name}.")
            st.rerun()


def selected_sources(r: Radar) -> None:
    """The sources this watch uses, as chosen in its brief, with an editor for your own watches."""
    cat = watches.catalogue()
    chosen = [x for x in cat if x["name"] in r.brief.sources]
    off = [x for x in cat if x["name"] not in r.brief.sources]
    chips = "".join(source_chip(x, r.brief.cadence.get(x["name"], x["cadence"])) for x in chosen)
    off_chips = "".join(f'<span class="mr-chip" style="color:#8A979F;text-decoration:line-through">{esc(x["label"])}</span>' for x in off)
    md(f'<div class="mr-card" style="border-top:3px solid #0B6176"><h4>Sources selected for this watch ({len(chosen)} of {len(cat)})</h4>'
       f'{chips}<div class="mr-foot" style="margin-top:6px">Not used: </div>{off_chips or "<span class=mr-foot>none</span>"}'
       '<div class="mr-foot" style="margin-top:8px"><span class="mr-chip" style="background:#ECEEFA">licensed</span>'
       '<span class="mr-chip" style="background:#E8F3EC">public</span><span class="mr-chip" style="background:#F8F0E4">upload</span>'
       ' · cadence after each name</div></div>')
    key = st.session_state.get("watch", "demo")
    if not key.startswith("user:"):
        st.caption("This is a built-in watch, so its sources are fixed. To choose your own, create a watch on the Watches page "
                   "(you can start from a copy of this one).")
        return
    with st.expander("Change sources"):
        with st.form("sources_form"):
            picks, cads = [], {}
            for x in cat:
                c1, c2 = st.columns([3, 1.3])
                if c1.checkbox(x["label"] + (" · demo replay only" if x["demo_only"] else ""), value=x["name"] in r.brief.sources,
                               key=f"edit_src_{x['name']}"):
                    picks.append(x["name"])
                cur = r.brief.cadence.get(x["name"], x["cadence"])
                cads[x["name"]] = c2.selectbox("Cadence", watches.CADENCES, index=watches.CADENCES.index(cur),
                                               key=f"edit_cad_{x['name']}", label_visibility="collapsed")
            if st.form_submit_button("Save sources", type="primary"):
                if not picks:
                    st.error("Pick at least one source.")
                else:
                    watches.update_sources(r.brief.id, picks, cads)
                    st.session_state.get("radars", {}).pop(key, None)
                    flash(f"Saved: this watch now uses {len(picks)} source(s). New sources are pulled on their next check.")
                    st.rerun()


def page_sources() -> None:
    r = radar()
    show_flash()
    header(r, "Sources and uploads")
    st.markdown("# Sources and uploads")
    md('<div class="mr-sub">Where this watch gets its facts. Licensed content stays in your workspace and is quoted only as '
       'your licence allows.</div>')
    st.write("")
    selected_sources(r)
    cfg = sources_config()
    pulls = {p["connector"]: p for p in r.store.pulls()}
    facts_by_conn: dict[str, int] = {}
    for f in r.store.facts():
        src = r.store.source(f.source_id) or {}
        facts_by_conn[src.get("connector", "")] = facts_by_conn.get(src.get("connector", ""), 0) + 1
    lic_rows = []
    for name in r.brief.sources:
        c = cfg.get(name, {})
        if licence.kind_of(c.get("licence", "public")) != "licensed":
            continue
        p = pulls.get(name)
        state = pill("Connected", "#E8F3EC", "#2A7449") if p and p["status"].startswith("ok") else pill("Not reachable", "#FBEAE6", "#B3321E")
        lic_rows.append(f"<tr><td><b>{esc(c.get('label', name))}</b><br><span class='mr-foot'>{esc(c.get('type'))} · licence: "
                        f"{esc(licence.label(c.get('licence')))}</span></td><td>{state}</td><td class='mr-foot'>"
                        f"{esc(p['last'] if p else '—')} · {facts_by_conn.get(name, 0)} facts</td><td class='mr-foot'>"
                        f"{esc(r.brief.cadence.get(name, ''))}</td></tr>")
    md('<div class="mr-card" style="border-top:3px solid #4450AE"><h4>Licensed market research databases</h4>'
       + ('<table class="mr-table"><thead><tr><th>Connector</th><th>Status</th><th>Last pull</th><th>Cadence</th></tr></thead><tbody>'
          + "".join(lic_rows) + "</tbody></table>" if lic_rows else "<p class='mr-foot'>None configured for this watch.</p>")
       + '<p class="mr-foot" style="margin-top:8px">Add a subscription as a block in <code>config/sources.yaml</code>: '
       'endpoint, the environment variable holding your key, and which of the vendor\'s fields map to market size, year and growth.</p></div>')

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.markdown("**Your uploaded reports**")
            if "upload" not in r.brief.sources:
                st.caption("Uploads are not selected for this watch; you can still add a file here.")
            up = st.file_uploader("PDF, PPTX, XLSX, DOCX, CSV · stays private to you", type=["pdf", "pptx", "xlsx", "docx", "csv", "txt"],
                                  accept_multiple_files=True)
            tag = st.selectbox("Licence for these files", ["upload_internal", "licensed_no_quote"],
                               format_func=lambda t: f"{licence.label(t)} ({'may be quoted internally' if licence.can_quote(t) else 'numbers only'})")
            if up and st.button("Read files and draft", type="primary"):
                n_facts = 0
                for f in up:
                    tmp = Path(tempfile.mkdtemp()) / f.name
                    tmp.write_bytes(f.getvalue())
                    ev = r.add_upload(tmp, today(r), tag)
                    n_facts += len(ev.fact_ids) if ev else 0
                v = r.draft(today(r))
                flash(f"Read {len(up)} file(s), {n_facts} facts extracted." + (f" Draft v{v['n']} is ready." if v else ""))
                st.rerun()
            sample = Path(__file__).resolve().parents[1] / "data" / "samples" / "poc_cardiac_markers_market_2026_synthetic.pdf"
            if sample.exists():
                st.download_button("Download the synthetic sample report to try", sample.read_bytes(), sample.name)
            for s_ in [x for x in r.store.sources() if x["connector"] == "upload"]:
                fs = [f for f in r.store.facts() if f.source_id == s_["id"]]
                md(f'<div class="mr-feed" style="grid-template-columns:minmax(0,1fr) auto"><div><b>{esc(s_["title"])}</b>'
                   f'<span class="m">{s_["fields"].get("passages", "?")} passages · {len(fs)} facts · licence: '
                   f'{esc(licence.label(s_["licence"]))} · {esc(s_["published"])}</span></div>{pill("Read", "#E8F3EC", "#2A7449")}</div>')
    with c2:
        rows = []
        for name in r.brief.sources:
            c = cfg.get(name, {})
            if licence.kind_of(c.get("licence", "public")) == "licensed" or name == "upload":
                continue
            p = pulls.get(name)
            st_ = (p["status"].split(":")[0] if p else "not yet pulled")
            colour = {"ok": ("#E8F3EC", "#2A7449"), "snapshot": ("#F7F0DC", "#845E00")}.get(st_, ("#EDF1F3", "#56656F"))
            rows.append(f"<tr><td>{esc(c.get('label', name))}<br><span class='mr-foot'>last checked {esc(p['last'] if p else '—')}"
                        f" · {facts_by_conn.get(name, 0)} facts</span></td><td>{pill(st_, *colour)}</td><td>"
                        f"{pill(r.brief.cadence.get(name, 'daily'), '#EDF1F3', '#56656F')}</td></tr>")
        md('<div class="mr-card" style="border-top:3px solid #2A7449"><h4>Public and live sources</h4><table class="mr-table">'
           '<thead><tr><th>Source</th><th>Status</th><th>Cadence</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
           '<p class="mr-foot" style="margin-top:8px">snapshot = live API unreachable, so the committed openFDA snapshot was used. '
           'In the demo scenario every source replays fictional items on their dates.</p></div>')
    q = [(x["title"], x["quarantined"]) for x in r.store.sources() if x["quarantined"]]
    if q:
        with st.expander(f"Quarantined text ({sum(len(x[1]) for x in q)} sentence(s) removed before any model or writer saw them)"):
            for title, qs in q:
                st.markdown(f"**{title}**")
                for s_ in qs:
                    st.code(s_, language=None)


GROUP_BG = {"licensed": "#ECEEFA", "public": "#E8F3EC", "upload": "#F8F0E4"}


def source_chip(x: dict, cadence: str) -> str:
    return f'<span class="mr-chip" style="background:{GROUP_BG[x["group"]]}">{esc(x["label"])} · {esc(cadence)}</span>'


def _csv(text: str) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def watch_list() -> None:
    rows = watches.list_watches()
    yours = [w for w in rows if w["kind"] == "yours"]
    builtin = [w for w in rows if w["kind"] == "built-in"]
    cat = {c["name"]: c for c in watches.catalogue()}
    if not yours:
        st.caption("None yet. Create one below; it is saved to your workspace and appears in the sidebar.")
    for w in yours:
        with st.container(border=True):
            a, b = st.columns([4, 2])
            srcs = ", ".join(cat.get(x, {}).get("label", x) for x in w["sources"])
            a.markdown(f"**{w['name']}**  \n<span class='mr-foot'>{esc(w['device_type'])} · {esc(w['diagnostic_solution'])} · "
                       f"{esc(', '.join(w['regions']))}<br>Competitors: {esc(', '.join(w['competitors']) or '—')}<br>"
                       f"Sources ({len(w['sources'])}): {esc(srcs)}<br>Saved {esc(w['updated'][:16].replace('T', ' '))} · "
                       f"<code>{esc(w['id'])}</code></span>", unsafe_allow_html=True)
            c1, c2, c3 = b.columns(3)
            if c1.button("Open", key=f"open_{w['id']}", type="primary", width="stretch"):
                open_watch(f"user:{w['id']}")
            if c2.button("Edit", key=f"edit_{w['id']}", width="stretch"):
                st.session_state["edit_watch"] = w["id"]
                st.rerun()
            with c3.popover("Delete", width="stretch"):
                st.write(f"Delete **{w['name']}** and its evidence database?")
                if st.button("Yes, delete", key=f"del_{w['id']}", type="primary"):
                    watches.delete(w["id"])
                    st.session_state.get("radars", {}).pop(f"user:{w['id']}", None)
                    flash(f"Deleted {w['name']}.", "warning")
                    st.rerun()
    with st.expander(f"Built-in watches ({len(builtin)})"):
        for w in builtin:
            a, b = st.columns([5, 1])
            a.markdown(f"**{w['name']}** · <span class='mr-foot'>{esc(w['diagnostic_solution'])} · {len(w['sources'])} sources · "
                       "read-only (use it as a template below)</span>", unsafe_allow_html=True)
            if w["id"] in BUILTIN_KEYS and b.button("Open", key=f"open_b_{w['id']}", width="stretch"):
                open_watch(BUILTIN_KEYS[w["id"]])


def import_watch() -> None:
    with st.expander("Import a watch (YAML)", key="import_expander"):
        st.caption("Restore a brief you downloaded earlier. Hosted apps such as Streamlit Community Cloud lose saved files when "
                   "they restart, so keep a copy of each watch with the Download button below the form.")
        f = st.file_uploader("Watch brief", type=["yaml", "yml"], key="import_yaml", label_visibility="collapsed")
        replace = st.checkbox("Replace a saved watch with the same id", key="import_replace")
        if f and st.button("Import", type="primary"):
            try:
                data = yaml.safe_load(f.getvalue().decode("utf-8")) or {}
                b, _ = watches.save(data, overwrite=replace)
            except FileExistsError:
                st.error("A watch with that id already exists. Tick 'Replace' to overwrite it.")
            except Exception as e:  # noqa: BLE001 - show the problem with the file to the user
                st.error(f"Could not import this file: {e}")
            else:
                flash(f"Imported '{b.name}'.")
                st.rerun()


def page_new_watch() -> None:
    show_flash()
    st.markdown("# Watches")
    md('<div class="mr-sub">Create, edit and open watches. Each one is saved as a YAML brief in your workspace and keeps its own '
       'evidence database.</div>')
    st.write("")
    rows = watches.list_watches()
    by_id = {w["id"]: w for w in rows}
    start_opts = ["template"] + [w["id"] for w in rows]
    editing = st.session_state.pop("edit_watch", None)          # set by Edit / Save; applied before the selectbox exists
    if editing in by_id or editing == "template":
        st.session_state["start_from"] = editing
    if st.session_state.get("start_from") not in start_opts:
        st.session_state["start_from"] = "template"
    current = by_id.get(st.session_state["start_from"])
    editing_yours = bool(current and current["kind"] == "yours")

    import_watch()                         # first, so its position (and open/closed state) never shifts
    if editing_yours:
        c1, c2 = st.columns([5, 1])
        c1.info(f"Editing **{current['name']}**. Change the form below, then press *Save changes*.")
        if c2.button("Stop editing", width="stretch"):
            st.session_state["edit_watch"] = "template"
            st.rerun()
    n_yours = sum(1 for w in rows if w["kind"] == "yours")
    st.markdown(f"**Your watches ({n_yours})**")
    watch_list()
    st.write("")
    st.markdown(f"### Editing: {current['name']}" if editing_yours else "### Create a watch")
    st.selectbox("Start from", start_opts, key="start_from",
                 format_func=lambda k: "Blank template (troponin example)" if k == "template"
                 else f"{by_id[k]['name']} ({'edit yours' if by_id[k]['kind'] == 'yours' else 'copy of built-in'})")
    base_id = st.session_state["start_from"]
    base = WatchBrief.load(by_id[base_id]["path"]) if base_id != "template" else None
    is_edit = bool(base and by_id[base_id]["kind"] == "yours")
    k = base_id                                     # widget keys per starting point, so switching reloads the form

    form, preview = st.columns([3, 2])
    with form:
        with st.container(border=True):
            st.markdown("**What to watch**")
            name = st.text_input("Watch name", base.name + ("" if is_edit else " (copy)") if base else "POC troponin, my market",
                                 key=f"name_{k}", disabled=is_edit, help="The id is made from the name and cannot change later.")
            a, b = st.columns(2)
            dtype = a.text_input("Medical device type", base.device_type if base else "Point-of-care immunoassay analyzer", key=f"dt_{k}")
            diag = b.text_input("Diagnostic solution", base.diagnostic_solution if base else "High-sensitivity cardiac troponin I",
                                key=f"dg_{k}")
            use = st.text_input("Clinical use", base.clinical_use if base else "Ruling heart attack in or out in emergency departments",
                                key=f"use_{k}", help="Helps the agent tell your market apart from neighbouring ones, such as central-lab testing.")
            regions = st.multiselect("Regions", ["United States", "European Union", "India", "Japan", "China"],
                                     base.regions if base else ["United States", "European Union", "India"], key=f"rg_{k}")
        terms0, codes0, out0 = suggest(diag, dtype, use)
        with st.container(border=True):
            st.markdown("**Search terms and scope**")
            st.caption("Suggested from the device and solution; edit freely (comma separated).")
            terms = st.text_area("Terms and synonyms", ", ".join(base.terms if base else terms0), key=f"tm_{k}_{diag}", height=68)
            a, b = st.columns(2)
            codes = a.text_input("FDA product codes", ", ".join(base.regulatory_ids.get("fda_product_codes", []) if base else
                                                                [c.split()[-1] for c in codes0 if c.startswith("FDA")]),
                                 key=f"pc_{k}_{diag}", help="Used to pull 510(k) clearances and recalls from openFDA.")
            inc = b.text_input("In scope if it mentions", ", ".join(base.scope.include_any if base else
                                                                  ["point-of-care", "point of care", "POC", "handheld", "whole blood", "cartridge"]),
                               key=f"in_{k}")
            out = st.text_input("Out of scope", ", ".join(base.scope.exclude_any if base else out0), key=f"out_{k}_{diag}")
        with st.container(border=True):
            st.markdown("**Competitors and your solution**")
            comps = st.text_input("Competitors to watch (comma separated)",
                                  ", ".join(c.name for c in base.competitors) if base else "Kestrel Diagnostics, Norvia Medical", key=f"cp_{k}")
            st.caption("Optional. The agent also finds competitors you did not list and asks before tracking them.")
            a, b, c = st.columns([2, 2, 1])
            yours = a.text_input("Your solution", base.your_solution.name if base else "[YOUR PRODUCT NAME]", key=f"ys_{k}")
            status_ = b.text_input("Regulatory status", base.your_solution.status if base else "CE marked; US filing planned 2027",
                                   key=f"st_{k}")
            year = c.number_input("US filing year", 2020, 2040, (base.your_solution.us_filing_year or 2027) if base else 2027, key=f"yr_{k}")
        with st.container(border=True):
            st.markdown("**Sources**")
            st.caption("Pick where this watch gets its facts and how often each one is checked.")
            cat = watches.catalogue()
            chosen, cadence = [], {}
            groups = [("licensed", "Licensed market research databases"), ("public", "Public and live sources"), ("upload", "Uploads")]
            for g, title in groups:
                items = [x for x in cat if x["group"] == g]
                if not items:
                    continue
                st.markdown(f"<span class='mr-foot'><b>{title}</b></span>", unsafe_allow_html=True)
                for x in items:
                    # a new watch starts with the real public sources and uploads; the fictional samples are opt-in
                    on_default = x["name"] in base.sources if base else (x["group"] != "licensed" and not x["demo_only"])
                    c1, c2 = st.columns([3, 1.3])
                    label = x["label"] + (" · demo replay only" if x["demo_only"] else "")
                    if c1.checkbox(label, value=on_default, key=f"src_{k}_{x['name']}"):
                        chosen.append(x["name"])
                    cad_default = (base.cadence.get(x["name"]) if base else None) or x["cadence"]
                    cadence[x["name"]] = c2.selectbox("Cadence", watches.CADENCES, index=watches.CADENCES.index(cad_default),
                                                      key=f"cad_{k}_{x['name']}", label_visibility="collapsed",
                                                      disabled=x["group"] == "upload")
        with st.container(border=True):
            st.markdown("**Refresh and alerts**")
            cads = ["daily", "weekly", "monthly"]
            cad = st.radio("Full report refresh", cads, index=cads.index(base.refresh.get("report", "weekly")) if base else 1,
                           horizontal=True, key=f"rf_{k}", format_func=str.capitalize)
            st.caption("High-impact changes open a draft straight away, whatever the refresh cadence.")
            a, b = st.columns(2)
            who = a.text_input("Send the digest to", ", ".join(base.digest.get("recipients", [])) if base else "you@example.com", key=f"to_{k}")
            chans = ["Email and in-app", "In-app only", "Microsoft Teams (webhook)", "Slack (webhook)"]
            ch0 = base.digest.get("channel") if base else None
            ch = b.selectbox("Alerts by", chans, index=chans.index(ch0) if ch0 in chans else 0, key=f"ch_{k}")

    brief = {"id": base.id if is_edit else watches.slugify(name), "name": name.strip(), "device_type": dtype, "diagnostic_solution": diag,
             "clinical_use": use, "regions": regions, "terms": _csv(terms),
             "regulatory_ids": {"fda_product_codes": [c.upper() for c in _csv(codes)]},
             "scope": {"include_any": _csv(inc), "exclude_any": _csv(out),
                       "exclude_always": list(base.scope.exclude_always) if base else []},
             "competitors": [{"name": c, "aliases": [c.split()[0]] if len(c.split()) > 1 else []} for c in _csv(comps)],
             "your_solution": {"name": yours, "status": status_, "us_filing_year": int(year)},
             "refresh": {"report": cad, "report_weekday": 0},
             "cadence": {s_: cadence[s_] if s_ != "upload" else "immediate" for s_ in chosen},
             "digest": {"recipients": _csv(who), "channel": ch},
             "sources": chosen}
    if base and base.short_name:
        brief["short_name"] = base.short_name

    with preview:
        cat_by = {x["name"]: x for x in watches.catalogue()}
        src_html = "".join(source_chip(cat_by[s_], brief["cadence"][s_]) for s_ in chosen) or "<span class='mr-foot'>none selected</span>"
        md('<div class="mr-card"><h4>The agent will search for</h4><div class="mr-foot">Terms and synonyms</div>'
           + "".join(f'<span class="mr-chip">{esc(t)}</span>' for t in brief["terms"])
           + '<div class="mr-foot" style="margin-top:8px">Regulatory identifiers</div>'
           + "".join(f'<span class="mr-chip" style="background:#E8F3EC;color:#2A7449">FDA product code {esc(c)}</span>'
                     for c in brief["regulatory_ids"]["fda_product_codes"])
           + '<div class="mr-foot" style="margin-top:8px">Out of scope</div>'
           + "".join(f'<span class="mr-chip" style="background:#FBEAE6;color:#8E2716">{esc(o)}</span>' for o in brief["scope"]["exclude_any"])
           + f'<div class="mr-foot" style="margin-top:8px">Sources ({len(chosen)})</div>{src_html}</div>')
        error = None
        try:
            WatchBrief(**brief)
            if not chosen:
                error = "Pick at least one source."
            if not name.strip():
                error = "Give the watch a name."
        except Exception as e:  # noqa: BLE001 - show validation problems to the user
            error = f"The brief is not valid yet: {e}"
        if error:
            st.error(error)
        exists = (watches.user_dir() / f"{brief['id']}.yaml").exists()
        if exists and not is_edit:
            st.warning(f"A watch with the id `{brief['id']}` already exists. Change the name or edit that watch.")
        verb = "Save changes" if is_edit else "Save watch"
        c1, c2 = st.columns(2)
        save = c1.button(verb, width="stretch", disabled=bool(error) or (exists and not is_edit))
        save_open = c2.button(f"{verb} and open", type="primary", width="stretch", disabled=bool(error) or (exists and not is_edit))
        if save or save_open:
            try:
                b, path = watches.save(brief, overwrite=is_edit)
            except (ValueError, FileExistsError) as e:
                st.error(str(e))
            else:
                st.session_state.get("radars", {}).pop(f"user:{b.id}", None)
                flash(f"Saved '{b.name}' to {path.name}. It is listed above and in the sidebar.")
                if save_open:
                    open_watch(f"user:{b.id}")
                st.session_state["edit_watch"] = b.id          # applied before the selectbox on the next run
                st.rerun()
        text = yaml.safe_dump(brief, sort_keys=False, allow_unicode=True)
        st.download_button("Download brief (YAML)", text, f"{brief['id']}.yaml", width="stretch")
        st.caption(f"Saved watches can also run from the command line: `python -m radar run {brief['id']}`.")


KNOWN_CODES = {"troponin": ("FDA product code MMI", ["troponin", "hs-cTnI", "hs-TnI", "cTnI", "high-sensitivity troponin", "POC troponin",
                                                     "0/1-hour algorithm"],
                            ["central-lab analyzers", "troponin T research assays"]),
               "natriuretic": ("FDA product code NBC", ["BNP", "NT-proBNP", "heart failure testing"], ["central-lab analyzers"]),
               "d-dimer": ("FDA product code DAP", ["D-dimer", "venous thromboembolism rule-out"], ["central-lab coagulation analyzers"]),
               "glucose": ("FDA product code NBW", ["blood glucose", "glucometer"], ["continuous glucose monitors"])}


def suggest(diag: str, dtype: str, use: str) -> tuple[list[str], list[str], list[str]]:
    text = f"{diag} {dtype} {use}".lower()
    for k, (code, terms, out) in KNOWN_CODES.items():
        if k in text or (k == "natriuretic" and "bnp" in text):
            return terms + ["near-patient testing"], [code, "EMDN category W (IVD)"], out
    return [diag, "point-of-care"], ["look up the FDA product code (openFDA classification)"], ["central-lab"]


PAGES = {
    "overview": st.Page(page_overview, title="Overview", icon="📡", default=True),
    "review": st.Page(page_review, title="Review redline", icon="📝", url_path="review"),
    "competitors": st.Page(page_competitors, title="Competitors", icon="🏁", url_path="competitors"),
    "sources": st.Page(page_sources, title="Sources and uploads", icon="🗂️", url_path="sources"),
    "new": st.Page(page_new_watch, title="Watches", icon="➕", url_path="watches"),
}
st.navigation(list(PAGES.values()), position="top").run()
