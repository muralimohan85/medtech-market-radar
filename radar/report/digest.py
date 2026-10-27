"""The what-changed digest sent when a version is published.

Written to workspace/outbox as an .eml file (open it in any mail client). Set
RADAR_SMTP_HOST, RADAR_SMTP_PORT, RADAR_SMTP_USER, RADAR_SMTP_PASSWORD and
RADAR_SMTP_FROM to send it for real.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from radar.config import WORKSPACE, section_titles

LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_digest(radar, version: dict) -> dict:
    events = [e for e in (radar.store.event(i) for i in version["event_ids"]) if e and e.level in LEVEL_ORDER]
    events.sort(key=lambda e: (LEVEL_ORDER[e.level], e.date))
    highs = [e for e in events if e.level == "high"]
    titles = section_titles()
    if highs:
        headline = f"{len(highs)} high-impact change{'s' if len(highs) > 1 else ''}: {highs[0].title}"
    elif events:
        headline = f"{len(events)} update{'s' if len(events) > 1 else ''} to {radar.brief.name}"
    else:
        headline = f"{radar.brief.name}: report refreshed"
    lines = [f"{radar.brief.name}: version {version['n']} published {version['as_of']}", "",
             headline, ""]
    for e in events:
        lines.append(f"[{e.level.upper()}] {e.date}  {e.title}")
        if e.sections:
            lines.append("    changes: " + ", ".join(titles.get(s, s) for s in e.sections))
    if version["changed"]:
        lines += ["", "Sections rewritten: " + ", ".join(titles.get(s, s) for s in version["changed"])]
    unchanged = [titles[s] for s in titles if s not in version["changed"]]
    if unchanged:
        lines.append("Unchanged: " + ", ".join(unchanged))
    a = version.get("approval") or {}
    if a.get("name"):
        lines += ["", f"Approved by {a['name']}" + (f": {a['note']}" if a.get("note") else "")]
    lines += ["", "Every statement in the report links to its source. Licensed figures stay in the workspace."]
    return {"subject": f"[Market Radar] {headline}", "body": "\n".join(lines),
            "to": radar.brief.digest.get("recipients", []), "version": version["n"], "watch": radar.brief.id}


def deliver(dg: dict, brief) -> str:
    msg = EmailMessage()
    msg["Subject"], msg["To"] = dg["subject"], ", ".join(dg["to"])
    msg["From"] = os.getenv("RADAR_SMTP_FROM", "market-radar@localhost")
    msg.set_content(dg["body"])
    out = WORKSPACE / "outbox" / dg["watch"]
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v{dg['version']:03d}.eml"
    path.write_bytes(bytes(msg))
    host = os.getenv("RADAR_SMTP_HOST")
    if host and dg["to"]:
        with smtplib.SMTP(host, int(os.getenv("RADAR_SMTP_PORT", "587")), timeout=20) as s:
            s.starttls()
            if os.getenv("RADAR_SMTP_USER"):
                s.login(os.environ["RADAR_SMTP_USER"], os.getenv("RADAR_SMTP_PASSWORD", ""))
            s.send_message(msg)
        return f"sent to {len(dg['to'])} recipients"
    return f"written to {path}"
