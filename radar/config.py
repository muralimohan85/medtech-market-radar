"""Paths, model prices and config loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"
DATA = REPO / "data"
WORKSPACE = Path(os.getenv("RADAR_WORKSPACE", REPO / "workspace"))   # databases, uploads, outbox; never committed

DEFAULT_MODEL = os.getenv("RADAR_MODEL", "claude-sonnet-5-5")
# USD per million tokens
MODEL_PRICES = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.1},
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5, "cache_write": 0.125, "cache_read": 0.01},
}


def load_yaml(path: Path | str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=None)
def rules() -> dict:
    return load_yaml(CONFIG / "rules.yaml")


@lru_cache(maxsize=None)
def licences() -> dict:
    return load_yaml(CONFIG / "licences.yaml")["profiles"]


@lru_cache(maxsize=None)
def sources_config() -> dict:
    return load_yaml(CONFIG / "sources.yaml")["connectors"]


def section_titles() -> dict[str, str]:
    return dict(rules()["sections"])


SECTION_ORDER = ["summary", "market", "segments", "competition", "pricing", "pipeline", "regulatory"]
