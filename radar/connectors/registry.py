"""Build connectors from config/sources.yaml, or a replay of a scenario file."""

from __future__ import annotations

from pathlib import Path

from radar.config import REPO, load_yaml, sources_config
from radar.connectors.base import Connector, PullResult, in_window
from radar.connectors.licensed import FileJSON, RestJSON
from radar.connectors.public import RSS, ClinicalTrialsGov, OpenFDA510k, OpenFDARecall, PubMed
from radar.schemas import RawItem, WatchBrief

TYPES = {
    "openfda_510k": OpenFDA510k,
    "openfda_recall": OpenFDARecall,
    "clinicaltrials_gov": ClinicalTrialsGov,
    "pubmed": PubMed,
    "rss": RSS,
    "file_json": FileJSON,
    "rest_json": RestJSON,
}


class Replay(Connector):
    """Releases scenario items for one source on their published dates."""

    def __init__(self, name: str, cfg: dict, items: list[RawItem]):
        super().__init__(name, cfg)
        self.items = [i for i in items if i.source == name]

    def pull(self, brief: WatchBrief, since: str | None, until: str, offline: bool = False) -> PullResult:
        return PullResult([i for i in self.items if in_window(i.published, since, until)])


def load_scenario(path: str | Path) -> tuple[dict, list[RawItem]]:
    d = load_yaml(REPO / path if not Path(path).is_absolute() else path)
    cfg = sources_config()
    items = []
    for it in d["items"]:
        lic = (it.get("fields") or {}).get("licence") or cfg.get(it["source"], {}).get("licence", "public")
        items.append(RawItem(id=it["id"], source=it["source"], kind=it["kind"], published=str(it["published"]),
                             title=it.get("title", ""), text=it.get("text", ""), url=it.get("url", ""),
                             fields=it.get("fields") or {}, licence=lic))
    return d, items


def build(brief: WatchBrief, scenario_items: list[RawItem] | None = None) -> dict[str, Connector]:
    """Connectors for a watch. With scenario items, every source replays the scenario instead."""
    cfg = sources_config()
    out: dict[str, Connector] = {}
    for name in brief.sources:
        if name == "upload" and scenario_items is None:
            continue                       # live uploads arrive through Radar.add_upload, not a pull
        c = cfg.get(name, {"type": "replay", "label": name})
        if scenario_items is not None or c["type"] == "replay":
            out[name] = Replay(name, c, scenario_items or [])
        else:
            out[name] = TYPES[c["type"]](name, c)
    return out
