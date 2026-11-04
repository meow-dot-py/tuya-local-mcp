"""Your lights: loading devices.json, picking lights by name, and fixing IPs.

devices.json is the file `tinytuya wizard` produces: a list of devices with
their id, local key, IP, protocol version and a DP "mapping". tuya-local-mcp only
needs these fields per light:

    {"name": "Desk Lamp", "id": "...", "key": "...", "ip": "192.168.1.50", "version": "3.3"}

and understands one optional extra field you can add yourself:

    "zone": "setup"     a short name to call the light by (defaults to its name)

How a target such as "setup" or "bed,ceiling" picks lights:

    all / *                 every light
    a zone                  lights with that "zone"
    a full name             "desk lamp" matches "Desk Lamp"
    a word of the name      "desk" or "lamp" matches "Desk Lamp"
    several, comma-separated

Matching is case-insensitive and whole-word only, so a typo like "s" never
matches anything by accident. A target that matches nothing is an error that
lists the valid names; it never falls back to "some" light.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from tuya_local_mcp import _tinytuya, config

ALL_TARGETS = ("all", "*")
DEFAULT_VERSION = 3.3


@dataclass(frozen=True)
class Light:
    id: str
    name: str
    key: str
    ip: str
    version: float
    zone: str | None = None
    mapping: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def label(self) -> str:
        """What we call this light in output: its zone if it has one, else its name."""
        return self.zone or self.name

    @property
    def aliases(self) -> frozenset[str]:
        words = [w for w in re.split(r"[\s_\-./]+", self.name.lower()) if w]
        names = {self.name.lower(), *words}
        if self.zone:
            names.add(self.zone.lower())
        return frozenset(names)


class UnknownTarget(ValueError):
    def __init__(self, word: str, lights: Iterable[Light]):
        lights = list(lights)
        valid = target_names(lights)
        close = difflib.get_close_matches(word, sorted({a for li in lights for a in li.aliases}), 3)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        super().__init__(f"No light called '{word}'. Valid targets: {', '.join(valid)}.{hint}")
        self.word = word


def load_lights() -> list[Light]:
    path = config.devices_path()
    if not path.exists():
        raise config.ConfigError(
            f"No lights configured yet ({path} does not exist).\n"
            "Run `tuya-local-mcp init` to fetch your bulbs' local keys once, "
            "or copy devices.example.json there and fill it in."
        )
    raw = config.read_json(path)
    if not isinstance(raw, list):
        raise config.ConfigError(f"{path} must contain a JSON list of devices.")

    lights = []
    for i, dev in enumerate(raw):
        if not isinstance(dev, dict):
            raise config.ConfigError(f"{path}: entry #{i + 1} is not an object.")
        missing = [k for k in ("id", "key") if not dev.get(k)]
        if missing:
            label = dev.get("name") or f"entry #{i + 1}"
            raise config.ConfigError(f"{path}: {label} is missing {', '.join(missing)}.")
        lights.append(
            Light(
                id=str(dev["id"]),
                name=str(dev.get("name") or dev["id"]),
                key=str(dev["key"]),
                ip=str(dev.get("ip") or ""),
                version=_parse_version(dev.get("version")),
                zone=(str(dev["zone"]).strip().lower() or None) if dev.get("zone") else None,
                mapping=dev.get("mapping") if isinstance(dev.get("mapping"), dict) else {},
            )
        )
    if not lights:
        raise config.ConfigError(f"{path} has no devices in it.")
    return lights


def _parse_version(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_VERSION


def target_names(lights: Iterable[Light]) -> list[str]:
    """The names worth suggesting to a person: 'all' plus each light's label."""
    labels: list[str] = []
    for light in lights:
        if light.label.lower() not in labels:
            labels.append(light.label.lower())
    return ["all", *labels]


def is_target(lights: list[Light], text: str) -> bool:
    try:
        resolve(lights, text)
    except UnknownTarget:
        return False
    return True


def resolve(lights: list[Light], target: str | None) -> list[Light]:
    """Turn a target like 'setup' or 'bed,ceiling' into lights. Raises UnknownTarget."""
    words = [w.strip().lower() for w in (target or "all").split(",") if w.strip()] or ["all"]
    picked: list[Light] = []
    for word in words:
        matches = lights if word in ALL_TARGETS else [li for li in lights if word in li.aliases]
        if not matches:
            raise UnknownTarget(word, lights)
        for li in matches:
            if all(li.id != p.id for p in picked):
                picked.append(li)
    return picked


# --- IP repair -----------------------------------------------------------------
# Home routers hand out new IPs now and then. Every Tuya device broadcasts a small
# UDP packet (ports 6666/6667) every few seconds, so we can find where each light
# moved without asking the cloud.


def scan_lan(seconds: int = 8, want_ids: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """Listen for Tuya broadcasts. Returns {device_id: {"ip": ..., "version": ...}}.

    With `want_ids`, it stops as soon as all of those devices have been heard,
    usually within a few seconds (each device broadcasts about every 5 s).
    """
    _tinytuya.load()
    from tinytuya import scanner  # lazy: only needed here
    wanted = tuple(want_ids) if want_ids else None
    found = scanner.devices(verbose=False, scantime=seconds, poll=False, byID=True, wantids=wanted)
    out: dict[str, dict[str, Any]] = {}
    for dev_id, info in found.items():
        if isinstance(info, dict) and info.get("ip"):
            out[str(dev_id)] = {"ip": info["ip"], "version": info.get("version")}
    return out


@dataclass
class ScanResult:
    seen: set[str] = field(default_factory=set)  # ids of lights that answered
    changed: dict[str, str] = field(default_factory=dict)  # light name -> new ip
    missing: list[str] = field(default_factory=list)  # names of lights that did not answer


def repair_ips(want_ids: Iterable[str] | None = None, seconds: int = 8) -> ScanResult:
    """Scan the LAN and save any IP that changed (for `want_ids`, or every light)."""
    path = config.devices_path()
    raw = config.read_json(path)
    ids = list(want_ids) if want_ids else [str(d.get("id")) for d in raw if d.get("id")]
    seen = scan_lan(seconds, ids)
    result = ScanResult(seen=set(seen))
    dirty = False
    for dev in raw:
        dev_id = str(dev.get("id"))
        if dev_id not in ids:
            continue
        name = str(dev.get("name") or dev_id)
        hit = seen.get(dev_id)
        if not hit:
            result.missing.append(name)
            continue
        if hit["ip"] != dev.get("ip"):
            dev["ip"] = hit["ip"]
            result.changed[name] = hit["ip"]
            dirty = True
        if hit.get("version") and not dev.get("version"):
            dev["version"] = str(hit["version"])
            dirty = True
    if dirty:
        config.write_json_atomic(path, raw)
    return result
