"""The replay evaluation runs end to end and the headline numbers hold."""

from evaluation.run_eval import run_real, run_scenario


def test_scenario_replay_metrics():
    s, _ = run_scenario()
    assert s["routing"]["exact_match"] >= 0.9
    assert s["alerts"]["high_caught"] == "2/2"
    assert s["citations"]["share_cited"] == 1.0 and s["citations"]["unsupported_numbers"] == 0
    assert s["injection_quarantined"] == "1/1" and s["citations"]["injection_leaks"] == 0
    a, b = s["untouched_sections_preserved"].split("/")
    assert a == b
    assert s["freshness_days"]["median"] <= 1


def test_real_fda_replay_separates_point_of_care_from_central_lab():
    r, _ = run_real()
    assert r["scope_accuracy"] >= 0.9
    assert r["point_of_care_alerted"] == "4/4"
    assert r["central_lab_kept_out"] == "10/10"
