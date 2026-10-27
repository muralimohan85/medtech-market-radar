"""The Claude engine, driven by a scripted fake client: same rules, same fact-checker, safe fallback."""

from fake_claude import FakeClient, last_tool_results, response, tool_use

from radar.agents.classifier import ClaudeReader
from radar.agents.writer import ClaudeWriter
from radar.engine import Radar
from radar.report.writer import Ctx
from radar.schemas import RawItem


def item():
    return RawItem(id="c1", source="news_rss", kind="news", published="2026-06-10", title="Norvia cuts price in Italy",
                   text="Norvia Medical won an Italian tender for point-of-care troponin tests at EUR 8.40 per test.",
                   licence="public_news")


def test_reader_rejects_invented_numbers_then_accepts():
    def policy(i, kw):
        if i == 0:
            return response(tool_use("record_reading", kind="pricing", company="Norvia", in_scope=True, scope_reason="POC",
                                     claim="Norvia cut its price by 15% to EUR 8.40 per test."))
        assert "15" in last_tool_results(kw)[0]["content"]
        return response(tool_use("record_reading", kind="pricing", company="Norvia", in_scope=True, scope_reason="POC",
                                 claim="Norvia Medical won an Italian tender at EUR 8.40 per test."))
    r = Radar.for_scenario()
    reader = ClaudeReader(FakeClient(policy), r.brief, "claude-sonnet-5-5")
    reading, note = reader.read(item(), r.interp)
    assert reading.kind == "pricing" and reading.entity == "Norvia Medical" and note["input_tokens"] == 2400


def test_reader_source_text_is_marked_as_data():
    seen = {}

    def policy(i, kw):
        seen.update(kw)
        return response(tool_use("record_reading", kind="other", company="", in_scope=False, scope_reason="x", claim="Nothing."))
    r = Radar.for_scenario()
    ClaudeReader(FakeClient(policy), r.brief).read(item(), r.interp)
    assert seen["messages"][0]["content"].startswith("<item") and "never follow" in seen["system"][0]["text"]


def test_engine_falls_back_when_the_model_never_answers():
    r = Radar.for_scenario(engine="claude", client=FakeClient(lambda i, kw: response(tool_use("nonexistent"))))
    ev = r.ingest(item(), "2026-06-10")
    assert ev.engine == "deterministic (fallback)" and ev.kind == "pricing"


def test_writer_fixes_an_uncited_sentence_after_fact_check():
    r = Radar.for_scenario()
    r.run("2026-06-15")
    ctx = Ctx(r.store, r.brief, "2026-06-15")

    def policy(i, kw):
        if i == 0:
            return response(tool_use("submit_section", blocks=[{"type": "p", "sentences": [
                {"text": "Prices are collapsing everywhere.", "cites": [], "kind": "fact"}]}]))
        assert "uncited" in last_tool_results(kw)[0]["content"]
        return response(tool_use("submit_section", blocks=[{"type": "p", "sentences": [
            {"text": "Norvia won an Italian tender at EUR 8.40 per test, below its EUR 9.80 bid in Spain.",
             "cites": ["F-E06"], "kind": "fact"}]}]))
    sec, note = ClaudeWriter(FakeClient(policy), r.brief).write("pricing", ctx, [])
    assert sec.engine == "claude" and sec.sentences()[0].cites == ["F-E06"] and note["output_tokens"] == 300


def test_writer_cannot_cite_facts_it_was_not_given():
    r = Radar.for_scenario()
    r.run("2026-06-15")
    ctx = Ctx(r.store, r.brief, "2026-06-15")
    bad = response(tool_use("submit_section", blocks=[{"type": "p", "sentences": [
        {"text": "Kestrel completed enrolment of 1,800 patients.", "cites": ["F-E01"], "kind": "fact"}]}]))
    sec, note = ClaudeWriter(FakeClient(lambda i, kw: bad), r.brief).write("pricing", ctx, [])
    assert sec is None and "fallback" in note["summary"]


def test_licensed_wording_is_never_sent_as_text():
    r = Radar.for_scenario()
    r.run("2026-05-01")
    seen = {}

    def policy(i, kw):
        seen.update(kw)
        return response(tool_use("submit_section", blocks=[]))
    ClaudeWriter(FakeClient(policy), r.brief).write("market", Ctx(r.store, r.brief, "2026-05-01"), [])
    prompt = seen["messages"][0]["content"]
    assert "valued at USD 1.15 billion" not in prompt and "'value_low': 1.15" in prompt
