"""Replay evaluation.

    python -m evaluation.run_eval                 # deterministic engine, no API key needed
    python -m evaluation.run_eval --mode claude   # Claude reads news and writes sections (needs ANTHROPIC_API_KEY)

Two replays, both day by day in date order, with every source checked on its own cadence:

1. Scenario: six months of fictional items (data/scenario/) against hand labels
   (evaluation/scenario_labels.yaml). Did the right sections change and nothing else?
   Were duplicates merged, noise kept out of alerts, injected instructions quarantined?
   Does every sentence cite a source, with no unsupported numbers and no licensed wording?
   How long from publication to alert?
2. Real data: FDA 510(k) clearances and recalls for troponin assays, January 2023 to
   October 2026 (committed openFDA snapshot) against hand labels (evaluation/real_labels.yaml).
   Does the radar tell point-of-care events from central-laboratory ones?
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

from radar.config import REPO, SECTION_ORDER, load_yaml
from radar.engine import Radar

OUT = REPO / "evaluation" / "results"
ALERT_LEVELS = {"high", "medium"}


def _as_set(x) -> set:
    return set(x) if isinstance(x, list) else {x}


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


# ====================================================================== scenario
def run_scenario(mode: str = "deterministic", client=None, model: str | None = None) -> dict:
    kw = {"engine": mode, "client": client, "model": model} if mode == "claude" else {}
    radar = Radar.for_scenario(**kw)
    radar.run("2026-10-27")
    labels = load_yaml(REPO / "evaluation" / "scenario_labels.yaml")
    store = radar.store
    ev = {e.id[3:]: e for e in store.events()}

    def root(i: str) -> str:
        while labels.get(i, {}).get("duplicate_of") in labels:
            i = labels[i]["duplicate_of"]
        return i

    clusters: dict[str, list[str]] = {}
    for i in labels:
        clusters.setdefault(root(i), []).append(i)

    rows, tp, fp, fn = [], 0, 0, 0
    level_ok = exact = 0
    for r, members in clusters.items():
        lab = labels[r]
        evs = sorted((ev[m] for m in members if m in ev), key=lambda e: (e.detected, e.date))
        primary = next((e for e in evs if not e.duplicate_of), evs[0] if evs else None)
        pred_secs = set().union(*[set(e.sections) for e in evs]) if evs else set()
        true_secs = set(lab["sections"])
        tp += len(pred_secs & true_secs)
        fp += len(pred_secs - true_secs)
        fn += len(true_secs - pred_secs)
        exact += pred_secs == true_secs
        pl = primary.level if primary else "missing"
        ok = pl in _as_set(lab["level"])
        level_ok += ok
        rows.append({"cluster": r, "items": members, "label_level": lab["level"], "pred_level": pl,
                     "label_sections": sorted(true_secs), "pred_sections": sorted(pred_secs), "level_ok": ok,
                     "sections_ok": pred_secs == true_secs, "detected": primary.detected if primary else None,
                     "published": primary.date if primary else None, "engine": primary.engine if primary else None})

    # in each cluster, every item after the first to arrive should be merged (an item whose
    # duplicate_of points outside the labelled period repeats something already known)
    dup_expected = dup_ok = 0
    for r, members in clusters.items():
        expected = len(members) - 1 + (1 if labels[r].get("duplicate_of") else 0)
        dup_expected += expected
        dup_ok += min(expected, sum(1 for m in members if m in ev and ev[m].duplicate_of))
    # an item whose cluster already has an event should never open a second one
    noise = [i for i, lab in labels.items() if "noise" in _as_set(lab["level"]) and not lab.get("duplicate_of")]
    noise_ok = sum(1 for i in noise if i in ev and ev[i].level not in ALERT_LEVELS)
    alerted = [r for r in rows if r["pred_level"] in ALERT_LEVELS]
    alert_precision = sum(1 for r in alerted if _as_set(r["label_level"]) & ALERT_LEVELS) / max(1, len(alerted))
    should = [r for r in rows if _as_set(r["label_level"]) <= ALERT_LEVELS]
    alert_recall = sum(1 for r in should if r["pred_level"] in ALERT_LEVELS) / max(1, len(should))
    highs = [r for r in rows if r["label_level"] == "high"]
    high_ok = sum(1 for r in highs if r["pred_level"] == "high")
    fresh = [_days(r["published"], r["detected"]) for r in alerted]
    by_source = {}
    for r in alerted:
        src = (store.source(r["cluster"]) or {}).get("connector") or "?"
        first = min((store.source(m) for m in r["items"] if store.source(m)), key=lambda s: s["fetched"])
        by_source.setdefault(first["connector"] if first else src, []).append(_days(r["published"], r["detected"]))

    inj = [i for i, lab in labels.items() if lab.get("injection")]
    inj_ok = sum(1 for i in inj if (store.source(i) or {}).get("quarantined"))
    new_ent = [(i, lab["new_entity"]) for i, lab in labels.items() if lab.get("new_entity")]
    new_ok = sum(1 for i, name in new_ent if i in ev and ev[i].new_entity == name)

    versions = store.versions()
    pub = [v for v in versions if v["status"] == "published"]
    sent = sum(v["checks"].get("sentences", 0) for v in pub)
    cited = sum(v["checks"].get("cited", 0) for v in pub)
    unsupported = sum(v["checks"].get("unsupported_numbers", 0) for v in pub)
    lic = sum(v["checks"].get("licence_breaches", 0) for v in pub)
    leaks = sum(v["checks"].get("injection_leaks", 0) for v in pub)
    preserved = total_untouched = 0
    for prev, cur in zip(pub, pub[1:]):
        for sid in SECTION_ORDER:
            if sid not in cur["changed"]:
                total_untouched += 1
                a, b = prev["report"].sections[sid], cur["report"].sections[sid]
                preserved += a.plain() == b.plain() and [s.cites for s in a.sentences()] == [s.cites for s in b.sentences()]
    rewrites = [len(v["changed"]) for v in pub[1:]]
    totals = radar.tracer.events
    return {
        "engine": mode,
        "items": len(labels), "clusters": len(clusters), "versions_published": len(pub),
        "routing": {"exact_match": round(exact / len(clusters), 3),
                    "precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3)},
        "materiality_accuracy": round(level_ok / len(clusters), 3),
        "alerts": {"precision": round(alert_precision, 3), "recall": round(alert_recall, 3),
                   "high_caught": f"{high_ok}/{len(highs)}", "count": len(alerted), "immediate": len(store.alerts())},
        "noise_kept_out": f"{noise_ok}/{len(noise)}",
        "duplicates_merged": f"{dup_ok}/{dup_expected}",
        "injection_quarantined": f"{inj_ok}/{len(inj)}",
        "new_competitor_found": f"{new_ok}/{len(new_ent)}",
        "citations": {"sentences_checked": sent, "cited": cited, "share_cited": round(cited / max(1, sent), 4),
                      "unsupported_numbers": unsupported, "licence_breaches": lic, "injection_leaks": leaks},
        "untouched_sections_preserved": f"{preserved}/{total_untouched}",
        "sections_rewritten_per_version": round(statistics.mean(rewrites), 2) if rewrites else 0,
        "freshness_days": {"median": statistics.median(fresh) if fresh else None, "max": max(fresh) if fresh else None,
                           "by_first_source": {k: statistics.median(v) for k, v in sorted(by_source.items())}},
        "cost_usd": round(sum(e.cost_usd for e in totals), 4),
        "tokens": {"input": sum(e.input_tokens for e in totals), "output": sum(e.output_tokens for e in totals)},
        "rows": rows,
    }, radar


# ====================================================================== real data
def run_real() -> dict:
    radar = Radar.live("hs_troponin_poc_live", db=":memory:", offline=True)
    radar.run("2026-10-27", start="2023-01-01")
    labels = load_yaml(REPO / "evaluation" / "real_labels.yaml")
    ev_by_item = {}
    for e in radar.store.events():
        for i in e.item_ids:
            ev_by_item.setdefault(i, e)
    rows = []
    for group, prefix in (("clearances", "fda510k-"), ("recalls", "fdarecall-")):
        for key, lab in labels[group].items():
            e = ev_by_item.get(prefix + key)
            primary = radar.store.event(e.duplicate_of) if e and e.duplicate_of else e
            pred_scope = bool(primary and primary.in_scope)
            pred_alert = bool(primary and primary.level in ALERT_LEVELS)
            rows.append({"id": key, "type": group[:-1], "date": e.date if e else None, "label_in_scope": lab["in_scope"],
                         "pred_in_scope": pred_scope, "label_alert": lab["alert"], "pred_alert": pred_alert,
                         "pred_level": primary.level if primary else "missing", "why": lab["why"],
                         "reasons": primary.reasons if primary else []})
    n = len(rows)
    scope_ok = sum(r["label_in_scope"] == r["pred_in_scope"] for r in rows)
    alert_ok = sum(r["label_alert"] == r["pred_alert"] for r in rows)
    pos = [r for r in rows if r["label_alert"]]
    neg = [r for r in rows if not r["label_alert"]]
    pub = [v for v in radar.store.versions() if v["status"] == "published"]
    return {"events_labelled": n, "scope_accuracy": round(scope_ok / n, 3), "alert_accuracy": round(alert_ok / n, 3),
            "point_of_care_alerted": f"{sum(r['pred_alert'] for r in pos)}/{len(pos)}",
            "central_lab_kept_out": f"{sum(not r['pred_alert'] for r in neg)}/{len(neg)}",
            "versions_published": len(pub),
            "share_cited": round(sum(v["checks"].get("cited", 0) for v in pub)
                                 / max(1, sum(v["checks"].get("sentences", 0) for v in pub)), 4),
            "rows": rows}, radar


# ====================================================================== output
def chart(rows: list[dict], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    colours = {"high": "#B3321E", "medium": "#845E00", "low": "#0B6176", "noise": "#9AA7AF"}
    yrow = {"high": 3, "medium": 2, "low": 1, "noise": 0}
    fig, ax = plt.subplots(figsize=(10, 3.4), dpi=150)
    for r in rows:
        if not r["detected"]:
            continue
        d = date.fromisoformat(r["detected"])
        y = yrow.get(r["pred_level"], 0)
        ax.scatter(d, y, s=70, color=colours.get(r["pred_level"], "#999"), edgecolor="white", linewidth=1, zorder=3)
        if not (r["level_ok"] and r["sections_ok"]):
            ax.scatter(d, y, s=160, facecolor="none", edgecolor="#14222B", linewidth=1.2, zorder=4)
    ax.set_yticks([0, 1, 2, 3], ["noise", "low", "medium", "high"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.grid(axis="x", color="#E3E8EB")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_title("Scenario replay: every item as the radar graded it (ringed = differs from the label)", fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def summary_md(s: dict, real: dict) -> str:
    c, a = s["citations"], s["alerts"]
    lines = [
        f"**Scenario replay** ({s['items']} fictional items, May to October 2026, {s['engine']} engine)",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| Right sections rewritten (exact match per change) | {s['routing']['exact_match']:.0%} "
        f"(precision {s['routing']['precision']:.0%}, recall {s['routing']['recall']:.0%}) |",
        f"| Materiality graded as labelled | {s['materiality_accuracy']:.0%} |",
        f"| High-impact changes caught | {a['high_caught']} |",
        f"| Alert precision / recall | {a['precision']:.0%} / {a['recall']:.0%} |",
        f"| Noise kept out of alerts | {s['noise_kept_out']} |",
        f"| Duplicates merged into the first report | {s['duplicates_merged']} |",
        f"| Injected instruction quarantined | {s['injection_quarantined']} (leaks into report: {c['injection_leaks']}) |",
        f"| New competitor found | {s['new_competitor_found']} |",
        f"| Sentences with a valid citation | {c['cited']}/{c['sentences_checked']} |",
        f"| Unsupported numbers / licensed wording copied | {c['unsupported_numbers']} / {c['licence_breaches']} |",
        f"| Untouched sections left exactly as approved | {s['untouched_sections_preserved']} |",
        f"| Sections rewritten per edition (of 7) | {s['sections_rewritten_per_version']} |",
        f"| Days from publication to alert (median, max) | {s['freshness_days']['median']:g}, {s['freshness_days']['max']:g} "
        "(news daily, FDA weekly, licensed monthly) |",
        "",
        f"**Real FDA data** ({real['events_labelled']} troponin 510(k) decisions and recalls, Jan 2023 to Oct 2026)",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| Point-of-care vs central-lab, as labelled | {real['scope_accuracy']:.0%} |",
        f"| Point-of-care events alerted | {real['point_of_care_alerted']} |",
        f"| Central-lab events kept out | {real['central_lab_kept_out']} |",
        f"| Sentences with a valid citation | {real['share_cited']:.0%} |",
    ]
    if s.get("cost_usd"):
        lines += ["", f"Claude engine cost for the scenario: ${s['cost_usd']:.2f} "
                      f"({s['tokens']['input']:,} input / {s['tokens']['output']:,} output tokens)."]
    return "\n".join(lines)


def update_readme(md: str) -> None:
    p = REPO / "README.md"
    txt = p.read_text(encoding="utf-8")
    new = re.sub(r"(<!-- EVAL:START -->).*?(<!-- EVAL:END -->)", lambda m: f"{m.group(1)}\n{md}\n{m.group(2)}", txt, flags=re.S)
    p.write_text(new, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay evaluation")
    ap.add_argument("--mode", choices=["deterministic", "claude"], default="deterministic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--update-readme", action="store_true")
    a = ap.parse_args()
    client = None
    if a.mode == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("set ANTHROPIC_API_KEY to run the Claude engine")
        from radar.llm.claude import make_client

        client = make_client()
    s, _ = run_scenario(a.mode, client, a.model)
    real, _ = run_real()
    OUT.mkdir(parents=True, exist_ok=True)
    tag = "" if a.mode == "deterministic" else "_claude"
    json.dump({"scenario": s, "real": real}, open(OUT / f"results{tag}.json", "w"), indent=1, default=str)
    md = summary_md(s, real)
    (OUT / f"summary{tag}.md").write_text(md + "\n", encoding="utf-8")
    chart(s["rows"], OUT / f"scenario_timeline{tag}.png")
    print(md)
    misses = [r for r in s["rows"] if not (r["level_ok"] and r["sections_ok"])]
    for r in misses:
        print(f"  differs: {r['cluster']} level {r['pred_level']} (label {r['label_level']}) sections {r['pred_sections']} "
              f"(label {r['label_sections']})")
    for r in real["rows"]:
        if r["label_alert"] != r["pred_alert"] or r["label_in_scope"] != r["pred_in_scope"]:
            print(f"  real differs: {r['id']} {r['pred_level']} {r['reasons'][:2]}")
    if a.update_readme:
        update_readme(md)
    print(Counter(r["pred_level"] for r in s["rows"]))


if __name__ == "__main__":
    main()
