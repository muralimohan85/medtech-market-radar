"""Saved watches: create, list, update and delete watch briefs.

Built-in watches live in config/watches/ (committed). Watches you create are saved as
YAML in the workspace (workspace/watches/, never committed), next to each watch's
evidence database. Either kind can be opened by id with `WatchBrief.load(id)` or
`python -m radar run <id>`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

import radar.config as cfg
from radar.schemas import WatchBrief

CADENCES = ["immediate", "daily", "weekly", "monthly"]


def user_dir() -> Path:
    return cfg.WORKSPACE / "watches"


def builtin_dir() -> Path:
    return cfg.CONFIG / "watches"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:48] or "watch"


def find(watch_id: str) -> Path | None:
    for d in (user_dir(), builtin_dir()):
        p = d / f"{watch_id}.yaml"
        if p.exists():
            return p
    return None


def list_watches() -> list[dict]:
    """Every watch, yours first (newest first), then the built-in ones."""
    out = []
    for kind, d in (("yours", user_dir()), ("built-in", builtin_dir())):
        if not d.exists():
            continue
        rows = []
        for p in d.glob("*.yaml"):
            try:
                raw = cfg.load_yaml(p) or {}
                b = WatchBrief(**raw)
            except Exception:  # noqa: BLE001 - a broken file should not hide the others
                continue
            rows.append({"id": b.id, "name": b.name, "kind": kind, "path": p, "device_type": b.device_type,
                         "diagnostic_solution": b.diagnostic_solution, "regions": b.regions, "sources": b.sources,
                         "competitors": [c.name for c in b.competitors], "created": raw.get("created", ""),
                         "updated": raw.get("updated", "")})
        rows.sort(key=lambda r: r["updated"] or r["created"] or r["id"], reverse=(kind == "yours"))
        out += rows
    return out


def save(brief: dict, overwrite: bool = False) -> tuple[WatchBrief, Path]:
    """Validate and save a brief as one of your watches. Returns the brief and the file it was written to."""
    data = dict(brief)
    data["id"] = slugify(data.get("id") or data.get("name", ""))
    b = WatchBrief(**data)                                  # raises on an invalid brief
    if (builtin_dir() / f"{b.id}.yaml").exists():
        raise ValueError(f"'{b.id}' is a built-in watch; choose another name")
    unknown = [s for s in b.sources if s not in cfg.sources_config()]
    if unknown:
        raise ValueError(f"unknown sources: {', '.join(unknown)}")
    if not b.sources:
        raise ValueError("pick at least one source")
    p = user_dir() / f"{b.id}.yaml"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if p.exists():
        if not overwrite:
            raise FileExistsError(f"a watch called '{b.id}' already exists")
        data["created"] = (cfg.load_yaml(p) or {}).get("created", now)
    else:
        data["created"] = now
    data["updated"] = now
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return b, p


def update_sources(watch_id: str, sources: list[str], cadence: dict[str, str]) -> WatchBrief:
    p = user_dir() / f"{watch_id}.yaml"
    if not p.exists():
        raise PermissionError("built-in watches are read-only; save a copy to change its sources")
    data = cfg.load_yaml(p)
    data["sources"] = list(sources)
    data["cadence"] = {**data.get("cadence", {}), **{k: v for k, v in cadence.items() if k in sources}}
    return save(data, overwrite=True)[0]


def delete(watch_id: str) -> None:
    p = user_dir() / f"{watch_id}.yaml"
    if not p.exists():
        raise PermissionError("only watches you created can be deleted")
    p.unlink()
    db = cfg.WORKSPACE / f"{watch_id}.db"
    if db.exists():
        db.unlink()


def catalogue() -> list[dict]:
    """The sources a watch can use, from config/sources.yaml, with a suggested cadence."""
    from radar.safety import licence

    default = {"rss": "daily", "openfda_510k": "weekly", "openfda_recall": "weekly", "clinicaltrials_gov": "weekly",
               "pubmed": "weekly", "file_json": "monthly", "rest_json": "monthly", "replay": "monthly", "upload": "immediate"}
    out = []
    for name, c in cfg.sources_config().items():
        out.append({"name": name, "label": c.get("label", name), "type": c.get("type"), "licence": c.get("licence", "public"),
                    "group": "upload" if c.get("type") == "upload" else licence.kind_of(c.get("licence", "public")),
                    "cadence": default.get(c.get("type"), "weekly"),
                    "demo_only": c.get("type") == "replay"})
    return out
