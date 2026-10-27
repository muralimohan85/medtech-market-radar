"""Connectors turn each source's own format into RawItems, and fail soft when a source is unreachable."""

import json

from radar.config import sources_config
from radar.connectors import licensed, public
from radar.connectors.base import SourceUnavailable
from radar.connectors.registry import build, load_scenario
from radar.schemas import WatchBrief

LIVE = WatchBrief.load("hs_troponin_poc_live")


def test_openfda_snapshot_fallback_and_window():
    c = public.OpenFDA510k("openfda_510k", sources_config()["openfda_510k"])
    res = c.pull(LIVE, "2024-01-01", "2025-12-31", offline=True)
    assert res.status == "snapshot"
    ks = {i.fields["k_number"] for i in res.items}
    assert "K240984" in ks and "K253051" not in ks          # 2025-01 in, 2026-06 out of the window
    item = next(i for i in res.items if i.fields["k_number"] == "K240984")
    assert item.published == "2025-01-03" and "i-STAT" in item.title and item.licence == "public"


def test_openfda_recall_item_shape():
    c = public.OpenFDARecall("openfda_recall", sources_config()["openfda_recall"])
    res = c.pull(LIVE, "2023-01-01", "2023-12-31", offline=True)
    quidel = next(i for i in res.items if "Z-1974-2023" in i.id)
    assert quidel.fields["recalling_firm"].startswith("QUIDEL") and "Triage" in quidel.text


def test_openfda_live_parses_api_payload(monkeypatch):
    payload = {"results": [{"k_number": "K999999", "decision_date": "20261001", "applicant": "Acme Dx", "device_name": "POC hs-cTnI",
                            "product_code": "MMI", "decision_description": "Substantially Equivalent"}]}
    monkeypatch.setattr(public, "http_json", lambda url, params=None, headers=None, timeout=30: payload)
    c = public.OpenFDA510k("openfda_510k", {"label": "x"})
    res = c.pull(LIVE, "2026-09-01", "2026-10-27")
    assert res.status == "ok" and res.items[0].published == "2026-10-01" and res.items[0].fields["applicant"] == "Acme Dx"


def test_clinicaltrials_v2_record():
    study = {"protocolSection": {
        "identificationModule": {"nctId": "NCT01234567", "briefTitle": "POC hs-cTnI in the ED"},
        "statusModule": {"overallStatus": "RECRUITING", "startDateStruct": {"date": "2026-01"},
                         "lastUpdatePostDateStruct": {"date": "2026-09-30"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Dx"}},
        "designModule": {"studyType": "OBSERVATIONAL", "phases": [], "enrollmentInfo": {"count": 900}},
        "conditionsModule": {"conditions": ["Myocardial Infarction"]},
        "descriptionModule": {"briefSummary": "Point-of-care troponin study."},
        "contactsLocationsModule": {"locations": [{"country": "Germany"}, {"country": "Spain"}]}}}
    item = public.ClinicalTrialsGov("clinicaltrials_gov", {}).to_item(study)
    assert item.fields["nct_id"] == "NCT01234567" and item.fields["enrollment"] == 900
    assert item.fields["countries"] == ["Germany", "Spain"] and item.published == "2026-09-30"


def test_pubmed_efetch_xml():
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
      <Journal><Title>Emergency Journal</Title></Journal><ArticleTitle>POC <i>hs</i>-cTnI evaluation</ArticleTitle>
      <Abstract><AbstractText>In 500 patients the NPV was 99.5%.</AbstractText></Abstract>
      <ArticleDate><Year>2026</Year><Month>03</Month><Day>04</Day></ArticleDate></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    items = public.PubMed("pubmed", {}).parse(xml)
    assert items[0].id == "pubmed-123" and items[0].published == "2026-03-04" and "hs-cTnI" in items[0].title


def test_rss_and_atom():
    rss = b"""<rss><channel><item><title>Acme launches POC troponin test</title><link>https://x.example/a</link>
      <description>&lt;p&gt;Point-of-care troponin.&lt;/p&gt;</description>
      <pubDate>Tue, 20 Oct 2026 08:00:00 GMT</pubDate></item></channel></rss>"""
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Troponin news</title><link href="https://x.example/b"/>
      <updated>2026-10-21T09:00:00Z</updated><summary>hs-cTnI</summary></entry></feed>"""
    c = public.RSS("news_rss", {})
    a, b = c.parse(rss, "https://x.example/feed")[0], c.parse(atom)[0]
    assert a.published == "2026-10-20" and a.text == "Point-of-care troponin." and a.url.endswith("/a")
    assert b.published == "2026-10-21" and b.url.endswith("/b")
    assert a.id == c.parse(rss, "https://x.example/feed")[0].id          # stable ids across runs


def test_unreachable_sources_fail_soft(monkeypatch):
    def boom(*a, **k):
        raise SourceUnavailable("blocked")
    monkeypatch.setattr(public, "http_json", boom)
    monkeypatch.setattr(public, "http_get", boom)
    for cls in (public.ClinicalTrialsGov, public.PubMed):
        assert cls("x", {}).pull(LIVE, None, "2026-10-27").status == "unreachable"
    assert public.RSS("x", {"feeds": ["https://a.example/rss"]}).pull(LIVE, None, "2026-10-27").status == "unreachable"


def test_licensed_file_connector_maps_vendor_fields():
    c = licensed.FileJSON("licensed_db_a", sources_config()["licensed_db_a"])
    items = c.pull(WatchBrief.load("hs_troponin_poc"), None, "2026-12-31").items
    assert [i.fields["cagr"] for i in items] == [10.8, 11.0]
    assert items[0].fields["value_low"] == 1.15 and items[0].licence == "licensed_no_quote"


def test_licensed_rest_needs_key_then_maps(monkeypatch):
    cfg = {"type": "rest_json", "label": "Vendor", "licence": "licensed_no_quote", "base_url": "https://api.vendor.example",
           "endpoint": "/est", "auth_env": "VENDOR_KEY", "params": {"q": "{diagnostic_solution}"}, "records_path": "data",
           "map": {"title": "t", "published": "d", "value_low": "v", "value_high": "v", "year": "y", "cagr": "g"}}
    c = licensed.RestJSON("vendor", cfg)
    brief = WatchBrief.load("hs_troponin_poc")
    assert c.pull(brief, None, "2026-12-31").status == "unreachable"
    seen = {}

    def fake(url, params=None, headers=None, timeout=30):
        seen.update(url=url, params=params, auth=headers["Authorization"])
        return {"data": [{"t": "Outlook", "d": "2026-05-01", "v": 1.0, "y": 2026, "g": 9.0}]}
    monkeypatch.setenv("VENDOR_KEY", "secret")
    monkeypatch.setattr(licensed, "http_json", fake)
    items = c.pull(brief, None, "2026-12-31").items
    assert seen["auth"] == "Bearer secret" and seen["params"]["q"] == brief.diagnostic_solution
    assert items[0].fields["cagr"] == 9.0 and json.dumps(items[0].fields)


def test_scenario_replay_releases_items_on_their_dates():
    meta, items = load_scenario("data/scenario/hs_troponin_poc.yaml")
    conns = build(WatchBrief.load(meta["watch"]), items)
    news = conns["news_rss"].pull(None, "2026-10-21", "2026-10-22").items
    assert {i.id for i in news} == {"E26", "E27"}
    assert conns["licensed_db_b"].items[0].licence == "licensed_no_quote"
