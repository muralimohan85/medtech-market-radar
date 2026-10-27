"""The evidence store: one SQLite file per watch.

Everything the report says traces back to a row here. Sources keep their licence tag
and any text quarantined by the sanitiser; facts keep the source, date, licence and
confidence; events group items that describe the same change; versions hold every
report draft and published edition with its approval.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from radar.schemas import Event, Fact, RawItem, Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, connector TEXT, kind TEXT, title TEXT, url TEXT, published TEXT, fetched TEXT,
  licence TEXT, content_hash TEXT, text TEXT, fields TEXT, quarantined TEXT, event_id TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY, source_id TEXT, entity TEXT, topic TEXT, claim TEXT, as_of TEXT, licence TEXT,
  data TEXT, confidence REAL, event_id TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(id UNINDEXED, claim, entity, topic);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, kind TEXT, entity TEXT, title TEXT, date TEXT, detected TEXT, sections TEXT, score REAL,
  level TEXT, in_scope INTEGER, reasons TEXT, item_ids TEXT, fact_ids TEXT, duplicate_of TEXT, new_entity TEXT,
  quarantined TEXT, engine TEXT, version INTEGER
);
CREATE TABLE IF NOT EXISTS entities (
  name TEXT PRIMARY KEY, role TEXT, aliases TEXT, tracked INTEGER, discovered TEXT, first_event TEXT
);
CREATE TABLE IF NOT EXISTS versions (
  n INTEGER PRIMARY KEY, created TEXT, as_of TEXT, status TEXT, report TEXT, hash TEXT, parent INTEGER,
  changed TEXT, event_ids TEXT, checks TEXT, approval TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS pulls (connector TEXT PRIMARY KEY, last TEXT, items INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, event_id TEXT, level TEXT, message TEXT, channel TEXT
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ sources
    def has_content(self, content_hash: str) -> str | None:
        r = self.db.execute("SELECT id FROM sources WHERE content_hash=?", (content_hash,)).fetchone()
        return r["id"] if r else None

    def add_source(self, item: RawItem, fetched: str, text: str, quarantined: list[str], event_id: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item.id, item.source, item.kind, item.title, item.url, item.published, fetched, item.licence,
             item.content_hash, text, json.dumps(item.fields, default=str), json.dumps(quarantined), event_id))
        self.db.commit()

    def set_source_event(self, source_id: str, event_id: str) -> None:
        self.db.execute("UPDATE sources SET event_id=? WHERE id=?", (event_id, source_id))
        self.db.commit()

    def source(self, source_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return self._row(r, ("fields", "quarantined")) if r else None

    def sources(self) -> list[dict]:
        return [self._row(r, ("fields", "quarantined")) for r in self.db.execute("SELECT * FROM sources ORDER BY published")]

    # ------------------------------------------------------------ facts
    def add_fact(self, f: Fact) -> None:
        self.db.execute("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (f.id, f.source_id, f.entity, f.topic, f.claim, f.as_of, f.licence, json.dumps(f.data, default=str),
                         f.confidence, f.event_id))
        self.db.execute("DELETE FROM facts_fts WHERE id=?", (f.id,))
        self.db.execute("INSERT INTO facts_fts VALUES (?,?,?,?)", (f.id, f.claim, f.entity, f.topic))
        self.db.commit()

    def bump_confidence(self, fact_ids: list[str], by: float = 0.1) -> None:
        for fid in fact_ids:
            self.db.execute("UPDATE facts SET confidence=MIN(0.99, confidence+?) WHERE id=?", (by, fid))
        self.db.commit()

    def fact(self, fid: str) -> Fact | None:
        r = self.db.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
        return self._fact(r) if r else None

    def facts(self, as_of: str | None = None, topic: str | list[str] | None = None, entity: str | None = None) -> list[Fact]:
        q, args = "SELECT * FROM facts WHERE 1=1", []
        if as_of:
            q += " AND as_of<=?"
            args.append(as_of)
        if topic:
            topics = [topic] if isinstance(topic, str) else topic
            q += f" AND topic IN ({','.join('?' * len(topics))})"
            args += topics
        if entity is not None:
            q += " AND entity=?"
            args.append(entity)
        return [self._fact(r) for r in self.db.execute(q + " ORDER BY as_of, id", args)]

    def search(self, query: str, limit: int = 10, as_of: str | None = None) -> list[Fact]:
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.db.execute("SELECT id FROM facts_fts WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                               (match, limit * 3)).fetchall()
        out = [f for f in (self.fact(r["id"]) for r in rows) if f and (not as_of or f.as_of <= as_of)]
        return out[:limit]

    # ------------------------------------------------------------ events
    def add_event(self, e: Event, version: int | None = None) -> None:
        d = asdict(e)
        self.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (d["id"], d["kind"], d["entity"], d["title"], d["date"], d["detected"], json.dumps(d["sections"]),
                         d["score"], d["level"], int(d["in_scope"]), json.dumps(d["reasons"]), json.dumps(d["item_ids"]),
                         json.dumps(d["fact_ids"]), d["duplicate_of"], d["new_entity"], json.dumps(d["quarantined"]),
                         d["engine"], version))
        self.db.commit()

    def events(self, since: str | None = None, unversioned: bool = False) -> list[Event]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if since:
            q += " AND detected>?"
            args.append(since)
        if unversioned:
            q += " AND version IS NULL"
        return [self._event(r) for r in self.db.execute(q + " ORDER BY detected, date, id", args)]

    def event(self, eid: str) -> Event | None:
        r = self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return self._event(r) if r else None

    def mark_events(self, ids: list[str], version: int) -> None:
        for i in ids:
            self.db.execute("UPDATE events SET version=? WHERE id=?", (version, i))
        self.db.commit()

    def event_version(self, eid: str) -> int | None:
        r = self.db.execute("SELECT version FROM events WHERE id=?", (eid,)).fetchone()
        return r["version"] if r else None

    # ------------------------------------------------------------ entities
    def upsert_entity(self, name: str, role: str, aliases: list[str], tracked: bool, discovered: str = "",
                      first_event: str = "") -> None:
        old = self.entity(name)
        if old:
            self.db.execute("UPDATE entities SET role=?, aliases=?, tracked=? WHERE name=?",
                            (role, json.dumps(sorted(set(old["aliases"]) | set(aliases))), int(tracked), name))
        else:
            self.db.execute("INSERT INTO entities VALUES (?,?,?,?,?,?)",
                            (name, role, json.dumps(aliases), int(tracked), discovered, first_event))
        self.db.commit()

    def entity(self, name: str) -> dict | None:
        r = self.db.execute("SELECT * FROM entities WHERE name=?", (name,)).fetchone()
        return self._row(r, ("aliases",)) if r else None

    def entities(self) -> list[dict]:
        return [self._row(r, ("aliases",)) for r in self.db.execute("SELECT * FROM entities ORDER BY name")]

    # ------------------------------------------------------------ versions
    def save_version(self, n: int, created: str, report: Report, status: str, parent: int | None, changed: list[str],
                     event_ids: list[str], checks: dict, note: str = "", approval: dict | None = None) -> None:
        self.db.execute("INSERT OR REPLACE INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (n, created, report.as_of, status, json.dumps(report.to_dict()), report.content_hash, parent,
                         json.dumps(changed), json.dumps(event_ids), json.dumps(checks), json.dumps(approval or {}), note))
        self.db.commit()

    def version(self, n: int) -> dict | None:
        r = self.db.execute("SELECT * FROM versions WHERE n=?", (n,)).fetchone()
        return self._version(r) if r else None

    def versions(self) -> list[dict]:
        return [self._version(r) for r in self.db.execute("SELECT * FROM versions ORDER BY n")]

    def latest(self, status: str | None = None) -> dict | None:
        q = "SELECT * FROM versions" + (" WHERE status=?" if status else "") + " ORDER BY n DESC LIMIT 1"
        r = self.db.execute(q, (status,) if status else ()).fetchone()
        return self._version(r) if r else None

    def set_version_status(self, n: int, status: str, approval: dict | None = None, note: str | None = None) -> None:
        v = self.version(n)
        self.db.execute("UPDATE versions SET status=?, approval=?, note=? WHERE n=?",
                        (status, json.dumps(approval if approval is not None else v["approval"]),
                         note if note is not None else v["note"], n))
        self.db.commit()

    # ------------------------------------------------------------ pulls, settings, alerts
    def last_pull(self, connector: str) -> str | None:
        r = self.db.execute("SELECT last FROM pulls WHERE connector=?", (connector,)).fetchone()
        return r["last"] if r else None

    def record_pull(self, connector: str, at: str, items: int, status: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO pulls VALUES (?,?,?,?)", (connector, at, items, status))
        self.db.commit()

    def pulls(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM pulls ORDER BY connector")]

    def get(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def put(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, default=str)))
        self.db.commit()

    def add_alert(self, at: str, event_id: str, level: str, message: str, channel: str) -> None:
        self.db.execute("INSERT INTO alerts (at, event_id, level, message, channel) VALUES (?,?,?,?,?)",
                        (at, event_id, level, message, channel))
        self.db.commit()

    def alerts(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM alerts ORDER BY id")]

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _row(r: sqlite3.Row, json_cols: tuple[str, ...]) -> dict:
        d = dict(r)
        for c in json_cols:
            d[c] = json.loads(d[c]) if d.get(c) else ([] if c != "fields" else {})
        return d

    @staticmethod
    def _fact(r: sqlite3.Row) -> Fact:
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        return Fact(**d)

    @staticmethod
    def _event(r: sqlite3.Row) -> Event:
        d = dict(r)
        d.pop("version", None)
        for c in ("sections", "reasons", "item_ids", "fact_ids", "quarantined"):
            d[c] = json.loads(d[c] or "[]")
        d["in_scope"] = bool(d["in_scope"])
        return Event(**d)

    @staticmethod
    def _version(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["report"] = Report.from_dict(json.loads(d["report"]))
        for c in ("changed", "event_ids"):
            d[c] = json.loads(d[c] or "[]")
        for c in ("checks", "approval"):
            d[c] = json.loads(d[c] or "{}")
        return d
