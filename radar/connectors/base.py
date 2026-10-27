"""Connector interface and the small HTTP helper the public connectors share."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from radar.schemas import RawItem, WatchBrief

USER_AGENT = "medtech-market-radar/0.1 (+https://github.com/muralimohan85/medtech-market-radar)"


class SourceUnavailable(RuntimeError):
    pass


def http_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 30,
             retries: int = 2, pause: float = 1.0) -> bytes:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=":+[],")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
            if e.code in (401, 403) and attempt == 0 and "api_key" not in url:
                break                                   # blocked or needs a key; retrying will not help
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(pause * (2 ** attempt))
    raise SourceUnavailable(f"{url.split('?')[0]}: {last}")


def http_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 30) -> dict:
    return json.loads(http_get(url, params, headers, timeout))


@dataclass
class PullResult:
    items: list[RawItem] = field(default_factory=list)
    status: str = "ok"              # ok | snapshot | unreachable | error
    note: str = ""


class Connector:
    """Pull items published after `since` (exclusive) up to `until` (inclusive)."""

    name: str = ""
    label: str = ""
    licence: str = "public"

    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg
        self.label = cfg.get("label", name)
        self.licence = cfg.get("licence", "public")

    def pull(self, brief: WatchBrief, since: str | None, until: str) -> PullResult:  # pragma: no cover - interface
        raise NotImplementedError

    def _item(self, **kw) -> RawItem:
        return RawItem(source=self.name, licence=self.licence, **kw)


def in_window(d: str, since: str | None, until: str) -> bool:
    return (since is None or d > since) and d <= until
