import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """Keep digests and databases out of the repo during tests."""
    monkeypatch.setenv("RADAR_WORKSPACE", str(tmp_path / "ws"))
    import radar.config
    import radar.report.digest

    monkeypatch.setattr(radar.config, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(radar.report.digest, "WORKSPACE", tmp_path / "ws")


@pytest.fixture(scope="session")
def replayed():
    """The full scenario replayed once, auto-approved (shared, read-only)."""
    from radar.engine import Radar

    r = Radar.for_scenario()
    r.run("2026-10-27")
    return r
