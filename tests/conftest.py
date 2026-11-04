"""Shared test fixtures: a temporary config folder and fake bulbs (no network needed)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tuya_local_mcp import devices, engine

# DP mapping of a typical modern RGB+CCT bulb, as `tinytuya wizard` writes it.
MODERN_MAPPING = {
    "20": {"code": "switch_led", "type": "Boolean", "values": {}},
    "21": {"code": "work_mode", "type": "Enum", "values": {"range": ["white", "colour"]}},
    "22": {"code": "bright_value_v2", "type": "Integer", "values": {"min": 10, "max": 1000}},
    "23": {"code": "temp_value_v2", "type": "Integer", "values": {"min": 0, "max": 1000}},
    "24": {"code": "colour_data_v2", "type": "Json", "values": {}},
}


class FakeBulb:
    """Stands in for tinytuya.BulbDevice. Behaviour is configured per device id."""

    def __init__(self, fleet: Fleet, dev_id: str, ip: str):
        self.fleet, self.dev_id, self.ip = fleet, dev_id, ip

    def _reachable(self) -> bool:
        if self.dev_id in self.fleet.hiccup:  # fails once, then works
            self.fleet.hiccup.discard(self.dev_id)
            return False
        return self.fleet.reachable_ip.get(self.dev_id) == self.ip

    def status(self) -> dict[str, Any]:
        if not self._reachable():
            return {"Error": "Network Error: Device Unreachable", "Err": "905"}
        return {"dps": dict(self.fleet.dps[self.dev_id])}

    def set_multiple_values(self, dps: dict[str, Any]) -> dict[str, Any]:
        if not self._reachable():
            return {"Error": "Network Error: Device Unreachable", "Err": "905"}
        self.fleet.dps[self.dev_id].update(dps)
        self.fleet.writes.append((self.dev_id, dict(dps)))
        return {"dps": dps}

    def close(self) -> None:
        pass


class Fleet:
    """All fake bulbs, plus what the fake LAN scan would report."""

    def __init__(self) -> None:
        self.dps: dict[str, dict[str, Any]] = {}
        self.reachable_ip: dict[str, str] = {}  # where each bulb really is
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.scans = 0
        self.hiccup: set[str] = set()

    def add(self, dev_id: str, ip: str, *, on: bool = False, **dps: Any) -> None:
        self.reachable_ip[dev_id] = ip
        self.dps[dev_id] = {"20": on, "21": "white", "22": 500, "23": 500, **dps}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Tests must not depend on where they run (e.g. inside a Claude Code session)."""
    import os

    for key in list(os.environ):
        if key.startswith(("CLAUDE_PLUGIN_", "TUYA_LOCAL_MCP_")) or key in (
            "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDE_CODE_EXECPATH",
        ):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TUYA_LOCAL_MCP_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fleet(monkeypatch) -> Fleet:
    fleet = Fleet()
    monkeypatch.setattr(engine, "open_device", lambda light: FakeBulb(fleet, light.id, light.ip))

    def fake_scan(seconds: int = 8, want_ids=None) -> dict[str, dict[str, Any]]:
        fleet.scans += 1
        return {i: {"ip": ip, "version": "3.3"} for i, ip in fleet.reachable_ip.items() if ip}

    monkeypatch.setattr(devices, "scan_lan", fake_scan)
    return fleet


def write_devices(home, *entries: dict[str, Any]) -> None:
    full = [
        {"version": "3.3", "key": "0123456789abcdef", "mapping": MODERN_MAPPING, **e}
        for e in entries
    ]
    (home / "devices.json").write_text(json.dumps(full), encoding="utf-8")


@pytest.fixture
def three_lights(home, fleet):
    """ceiling / setup / bed, all reachable and off."""
    write_devices(
        home,
        {"name": "Ceiling Light", "id": "c1", "ip": "10.0.0.1"},
        {"name": "Luz Setup", "id": "s1", "ip": "10.0.0.2"},
        {"name": "Bedside", "id": "b1", "ip": "10.0.0.3", "zone": "bed"},
    )
    fleet.add("c1", "10.0.0.1")
    fleet.add("s1", "10.0.0.2")
    fleet.add("b1", "10.0.0.3")
    return fleet
