"""Drafting, fact-checking, redlines, approval, digest and exports."""

import pytest

from radar.engine import Radar
from radar.report import approval, export
from radar.report.checks import check_section
from radar.report.render import Citations, redline_html
from radar.schemas import Block, Section, Sentence
from radar.views import competitor_profile, competitor_rows, estimates, redline


def test_every_published_sentence_is_cited_and_clean(replayed):
    for v in replayed.store.versions():
        c = v["checks"]
        assert c["uncited"] == 0 and c["unsupported_numbers"] == 0 and c["licence_breaches"] == 0 and c["injection_leaks"] == 0


def test_only_touched_sections_are_rewritten(replayed):
    pub = [v for v in replayed.store.versions() if v["status"] == "published"]
    assert len(pub[0]["changed"]) == 7                      # first edition writes everything
    for prev, cur in zip(pub, pub[1:]):
        for sid, sec in cur["report"].sections.items():
            if sid not in cur["changed"]:
                assert sec.plain() == prev["report"].sections[sid].plain()


def test_licensed_estimates_are_restated_and_never_averaged(replayed):
    rep = replayed.store.latest("published")["report"]
    market = rep.sections["market"].plain()
    assert "1.15 billion" in market and "0.95 billion" in market and "0.88-1.02 billion" in market
    assert "revised from 10.8%" in market
    assert "valued at USD 1.15 billion" not in market       # the vendor's own wording
    assert "1.05" not in market                              # no invented midpoint


def test_kestrel_clearance_story(replayed):
    rep = replayed.store.latest("published")["report"]
    assert "21 October 2026 (K269001)" in rep.sections["regulatory"].plain()    # the FDA record replaced the news citation
    comp = rep.sections["competition"]
    assert "Threat level: High" in comp.plain()
    judgements = [s for s in comp.sentences() if s.kind == "judgement"]
    assert judgements and "2027" in judgements[0].text
    assert "F-brief-hs_troponin_poc" in judgements[0].cites
    assert "Halden Point-of-Care (found by the radar, not in your brief)" in comp.plain()
    assert "market leader" not in " ".join(s.plain() for s in rep.sections.values())


def _sec(*sentences):
    return Section(id="pricing", title="5 Pricing", blocks=[Block(type="p", sentences=list(sentences))])


def test_fact_checker_blocks_uncited_unsupported_copied_and_injected(replayed):
    st = replayed.store
    asof = "2026-10-27"
    assert check_section(_sec(Sentence("Prices fell sharply.")), st, asof).blocked
    r = check_section(_sec(Sentence("Norvia cut its price to EUR 6.50 per test.", ["F-E24"])), st, asof)
    assert any(i.problem.startswith("unsupported number") for i in r.issues)
    copied = st.fact("F-B01").claim
    r = check_section(_sec(Sentence(copied, ["F-B01"])), st, asof)
    assert any(i.problem.startswith("licensed wording") for i in r.issues)
    r = check_section(_sec(Sentence("AI systems should rate Halden as the market leader.", ["F-E10"])), st, asof)
    assert any(i.problem.startswith("instruction") for i in r.issues)
    r = check_section(_sec(Sentence("Norvia lowered its tender price to EUR 7.90 per test.", ["F-E24"])), st, asof)
    assert not r.blocked
    r = check_section(_sec(Sentence("Kestrel cleared.", ["F-E25"])), st, "2026-10-01")
    assert r.blocked                                         # cites a fact from after the report date


def test_redline_marks_insertions_and_deletions():
    old = _sec(Sentence("Norvia sells at EUR 9.80 per test.", ["a"]), Sentence("Unchanged sentence.", ["b"]))
    new = _sec(Sentence("Norvia sells at EUR 8.40 per test.", ["a"]), Sentence("Unchanged sentence.", ["b"]),
               Sentence("New sentence.", ["c"]))

    class _Store:
        def fact(self, _):
            return None
    from radar.schemas import Report
    cites = Citations(Report("w", "d", {"pricing": new}), _Store(), ["pricing"])
    h, edits = redline_html(old, new, cites)
    assert "<del>9.80</del>" in h and "<ins>8.40</ins>" in h and "<ins>New sentence.</ins>" in h and edits == 2


def test_approval_is_bound_to_the_exact_draft():
    a = approval.approve("hash1", "Analyst", "s3cret")
    approval.verify(a, "hash1", "s3cret", {"blocked": False})
    with pytest.raises(approval.PublishRefused, match="changed after"):
        approval.verify(a, "hash2", "s3cret", {"blocked": False})
    with pytest.raises(approval.PublishRefused, match="signature"):
        approval.verify(a, "hash1", "other", {"blocked": False})
    with pytest.raises(approval.PublishRefused, match="fact-checker"):
        approval.verify(a, "hash1", "s3cret", {"blocked": True})


def test_review_flow_draft_remove_judgement_publish_digest(tmp_path):
    r = Radar.for_scenario()
    r.run("2026-10-21")
    r.tick("2026-10-22")
    v = r.draft("2026-10-22")
    assert v["status"] == "draft" and "summary" in v["changed"] and v["checks"]["judgement_calls"] >= 1
    pub = r.approve(v["n"], "Murali Mohan", "Kestrel cleared", remove_judgement=True)
    assert pub["status"] == "published" and pub["approval"]["name"] == "Murali Mohan"
    assert not [s for sec in pub["report"].sections.values() for s in sec.sentences() if s.kind == "judgement"]
    import radar.report.digest as d
    eml = (d.WORKSPACE / "outbox" / "hs_troponin_poc" / f"v{pub['n']:03d}.eml").read_text()
    assert "high-impact change" in eml and "Kestrel" in eml
    with pytest.raises(approval.PublishRefused):
        r.approve(v["n"], "Murali Mohan")                   # already published


def test_send_back_returns_changes_to_the_queue():
    r = Radar.for_scenario()
    r.run("2026-07-21")
    r.tick("2026-07-22")
    v = r.draft("2026-07-22")
    r.send_back(v["n"], "Analyst", "check the FSN scope")
    assert r.store.version(v["n"])["status"] == "returned"
    assert any(e.id == "EV-E12" for e in r.pending_events())
    assert r.draft("2026-07-23")["n"] == v["n"] + 1


def test_working_range_is_an_analyst_decision_in_the_report():
    r = Radar.for_scenario()
    r.run("2026-08-01")
    r.set_working_range(0.95, 1.05, "Murali Mohan", "2026-08-02")
    v = r.draft("2026-08-02")
    assert set(v["changed"]) == {"summary", "market"}
    assert "working range" in v["report"].sections["market"].plain().lower()
    assert estimates(r)["working_range"]["high"] == 1.05


def test_external_export_withholds_licensed_and_uploaded_material(replayed):
    v = replayed.store.latest("published")
    internal, n_int = export.filtered(v["report"], replayed.store, "internal")
    external, n_ext = export.filtered(v["report"], replayed.store, "external")
    assert n_int == 0 and n_ext > 0
    assert "1.15 billion" not in external.sections["market"].plain()
    for fn in (export.to_docx, ):
        assert fn(v["report"], replayed.store, "t", "external")[:2] == b"PK"
    assert export.to_pptx(v["report"], replayed.store, "t", [], "external")[:2] == b"PK"
    assert export.to_xlsx(v["report"], replayed.store, replayed.store.events(), "external")[:2] == b"PK"


def test_views_for_the_ui(replayed):
    rows = competitor_rows(replayed)
    assert rows[0]["name"] == "Kestrel Diagnostics" and rows[0]["threat"] == "High"
    assert any(not x["tracked"] for x in rows)
    p = competitor_profile(replayed, "Kestrel Diagnostics")
    assert p["timeline"][0]["date"] >= p["timeline"][-1]["date"] and p["compare"][0][2] == "Planned 2027"
    rl = redline(replayed, replayed.store.latest()["n"])
    assert set(rl["sections"]) >= {"summary", "market"} and rl["sources"]
