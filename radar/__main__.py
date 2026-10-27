"""Command line.

    python -m radar replay                       # fictional scenario, May to October 2026, auto-approved
    python -m radar run hs_troponin_poc_live     # pull real public sources now and open a draft if anything changed
    python -m radar status hs_troponin_poc_live
    python -m radar approve hs_troponin_poc_live 3 --name "Your Name" [--note "..."]
    python -m radar export hs_troponin_poc_live --audience external

Schedule `run` with cron or a CI job for continuous refresh; drafts wait for approval.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from radar.config import WORKSPACE
from radar.engine import Radar


def _print_version(r: Radar, v: dict | None) -> None:
    if not v:
        print("No section needed rewriting.")
        return
    c = v["checks"]
    print(f"v{v['n']} ({v['status']}) as of {v['as_of']}: rewrote {', '.join(v['changed']) or 'nothing'}")
    print(f"  checks: {c.get('cited')}/{c.get('sentences')} sentences cited, {c.get('judgement_calls')} judgement calls, "
          f"blocked={c.get('blocked')}")
    for i in v["event_ids"]:
        e = r.store.event(i)
        if e and e.level != "noise":
            print(f"  [{e.level}] {e.date} {e.title}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="radar", description="MedTech Market Radar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("replay")
    for name in ("run", "status", "approve", "export"):
        p = sub.add_parser(name)
        p.add_argument("watch")
        if name == "run":
            p.add_argument("--day", default=date.today().isoformat())
            p.add_argument("--offline", action="store_true", help="use committed snapshots instead of live APIs")
            p.add_argument("--engine", choices=["deterministic", "claude"], default="deterministic")
        if name == "approve":
            p.add_argument("n", type=int)
            p.add_argument("--name", required=True)
            p.add_argument("--note", default="")
            p.add_argument("--remove-judgement", action="store_true")
        if name == "export":
            p.add_argument("--audience", choices=["internal", "external"], default="internal")
    a = ap.parse_args()

    if a.cmd == "replay":
        r = Radar.for_scenario()
        r.run("2026-10-27")
        for v in r.store.versions():
            print(f"v{v['n']:>2} {v['as_of']}  {v['status']:<9} {', '.join(v['changed'])}")
        print(f"{len(r.store.alerts())} immediate alerts; digests in {WORKSPACE / 'outbox'}")
        return

    kw = {}
    if getattr(a, "engine", "") == "claude":
        from radar.llm.claude import make_client

        kw = {"engine": "claude", "client": make_client()}
    r = Radar.live(a.watch, offline=getattr(a, "offline", False), **kw)
    if a.cmd == "run":
        if not r.store.latest():
            v = r.bootstrap(a.day)
            print("First edition published.")
            _print_version(r, v)
            return
        r.tick(a.day)
        for p in r.store.pulls():
            print(f"  {p['connector']:<20} {p['last']}  {p['items']:>3} items  {p['status'][:80]}")
        _print_version(r, r.draft(a.day))
    elif a.cmd == "status":
        for v in r.store.versions()[-5:]:
            print(f"v{v['n']} {v['as_of']} {v['status']} {', '.join(v['changed'])}")
    elif a.cmd == "approve":
        _print_version(r, r.approve(a.n, a.name, a.note, a.remove_judgement))
    elif a.cmd == "export":
        from radar.report import export

        v = r.store.latest("published")
        out = WORKSPACE / "exports"
        out.mkdir(parents=True, exist_ok=True)
        stem = out / f"{r.brief.id}_v{v['n']}_{a.audience}"
        Path(f"{stem}.docx").write_bytes(export.to_docx(v["report"], r.store, f"Market report: {r.brief.name}", a.audience, v["n"]))
        Path(f"{stem}.pptx").write_bytes(export.to_pptx(v["report"], r.store, f"Market update: {r.brief.name}",
                                                         [e for e in (r.store.event(i) for i in v["event_ids"]) if e], a.audience))
        Path(f"{stem}.xlsx").write_bytes(export.to_xlsx(v["report"], r.store, r.store.events(), a.audience))
        print(f"wrote {stem}.docx/.pptx/.xlsx")


if __name__ == "__main__":
    main()
