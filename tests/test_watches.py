"""Saving, listing, editing and deleting watches."""

import pytest
import yaml

from radar import watches
from radar.engine import Radar
from radar.schemas import WatchBrief


def brief(**kw):
    b = yaml.safe_load(open(watches.builtin_dir() / "hs_troponin_poc_live.yaml"))
    b.update(id="", name="My D-dimer watch", diagnostic_solution="D-dimer", sources=["openfda_510k", "news_rss", "upload"],
             cadence={"openfda_510k": "weekly", "news_rss": "daily", "upload": "immediate"})
    b.update(kw)
    return b


def test_save_list_load_and_delete():
    b, path = watches.save(brief())
    assert b.id == "my_d_dimer_watch" and path.parent == watches.user_dir()
    rows = watches.list_watches()
    assert rows[0]["id"] == b.id and rows[0]["kind"] == "yours" and rows[0]["created"]
    assert {r["id"] for r in rows if r["kind"] == "built-in"} >= {"hs_troponin_poc", "hs_troponin_poc_live"}
    assert WatchBrief.load(b.id).diagnostic_solution == "D-dimer"        # found by id like a built-in watch
    with pytest.raises(FileExistsError):
        watches.save(brief())
    b2, _ = watches.save(brief(clinical_use="Rule out PE"), overwrite=True)
    assert WatchBrief.load(b2.id).clinical_use == "Rule out PE"
    watches.delete(b.id)
    assert not any(r["id"] == b.id for r in watches.list_watches())


def test_rejects_bad_briefs():
    with pytest.raises(ValueError, match="built-in"):
        watches.save(brief(name="hs troponin poc"))
    with pytest.raises(ValueError, match="unknown sources"):
        watches.save(brief(sources=["nope"]))
    with pytest.raises(ValueError, match="at least one"):
        watches.save(brief(sources=[]))
    with pytest.raises(PermissionError):
        watches.delete("hs_troponin_poc")
    with pytest.raises(PermissionError):
        watches.update_sources("hs_troponin_poc", ["news_rss"], {})


def test_update_sources_changes_what_the_radar_pulls():
    b, _ = watches.save(brief())
    watches.update_sources(b.id, ["openfda_510k", "openfda_recall", "licensed_db_a"], {"licensed_db_a": "monthly"})
    nb = WatchBrief.load(b.id)
    assert nb.sources == ["openfda_510k", "openfda_recall", "licensed_db_a"] and nb.cadence["licensed_db_a"] == "monthly"
    r = Radar.live(b.id, db=":memory:", offline=True)
    assert set(r.connectors) == {"openfda_510k", "openfda_recall", "licensed_db_a"}


def test_catalogue_groups_every_configured_source():
    cat = {c["name"]: c for c in watches.catalogue()}
    assert cat["licensed_db_a"]["group"] == "licensed" and cat["news_rss"]["group"] == "public"
    assert cat["upload"]["group"] == "upload" and cat["patents"]["demo_only"]
