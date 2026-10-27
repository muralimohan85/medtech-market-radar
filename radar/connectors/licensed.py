"""Licensed market research databases, configured rather than coded.

Each vendor is a block in config/sources.yaml: where the records are, how to
authenticate, and which of the vendor's fields map to the radar's market-estimate
fields. `rest_json` calls the vendor API with a key from an environment variable;
`file_json` reads an export you downloaded (the bundled fictional samples use it).

Licensed records keep their licence tag, so their wording is never quoted in the
report and they are left out of exports meant for people outside your organisation.
"""

from __future__ import annotations

import json
import os

from radar.config import REPO
from radar.connectors.base import Connector, PullResult, SourceUnavailable, http_json, in_window
from radar.schemas import WatchBrief

ESTIMATE_FIELDS = ("region", "year", "value_low", "value_high", "cagr", "cagr_period", "segment", "unit")


def dig(obj, path: str):
    for part in (path or "").split("."):
        if not part:
            continue
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


class _Mapped(Connector):
    def records(self, brief: WatchBrief) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def to_item(self, rec: dict):
        m = self.cfg.get("map", {})
        get = lambda k: dig(rec, m[k]) if k in m else None  # noqa: E731
        fields = {k: get(k) for k in ESTIMATE_FIELDS if get(k) is not None}
        fields.setdefault("unit", "USD bn")
        fields["metric"] = "market_value"
        published = str(get("published") or "")[:10]
        title = get("title") or "Market estimate"
        lo, hi = fields.get("value_low"), fields.get("value_high")
        size = f"USD {lo} billion" if lo == hi else f"USD {lo}-{hi} billion"
        text = (f"{fields.get('region', 'Global')} {fields.get('segment', 'market')} value {size} in {fields.get('year')}; "
                f"forecast growth {fields.get('cagr')}% a year ({fields.get('cagr_period', '')}).")
        rid = get("id") or f"{title}-{published}"
        return self._item(id=f"{self.name}-{rid}", kind="market_estimate", published=published, title=title, text=text,
                          fields=fields)

    def pull(self, brief: WatchBrief, since: str | None, until: str, offline: bool = False) -> PullResult:
        try:
            recs = self.records(brief)
        except SourceUnavailable as e:
            return PullResult(status="unreachable", note=str(e))
        items = [self.to_item(r) for r in recs]
        return PullResult([i for i in items if in_window(i.published, since, until)])


class FileJSON(_Mapped):
    def records(self, brief: WatchBrief) -> list[dict]:
        p = REPO / self.cfg["path"]
        if not p.exists():
            raise SourceUnavailable(f"{p} not found")
        return dig(json.load(open(p, encoding="utf-8")), self.cfg.get("records_path", "")) or []


class RestJSON(_Mapped):
    def records(self, brief: WatchBrief) -> list[dict]:
        key = os.getenv(self.cfg.get("auth_env", ""), "")
        if not key:
            raise SourceUnavailable(f"set {self.cfg.get('auth_env')} to use {self.label}")
        params = {k: str(v).format(**brief.model_dump()) for k, v in (self.cfg.get("params") or {}).items()}
        data = http_json(self.cfg["base_url"].rstrip("/") + self.cfg.get("endpoint", ""), params,
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        return dig(data, self.cfg.get("records_path", "")) or []
