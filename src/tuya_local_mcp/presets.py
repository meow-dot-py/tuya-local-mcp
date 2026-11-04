"""Presets and scenes (presets.json).

A preset is either one setting for whatever lights you target:

    "chill": {"temp": 0, "brightness": 20}
    "cyber": {"rgb": [255, 0, 255], "brightness": 90, "description": "neon magenta"}

or a scene, with different settings per light (keys are targets, see devices.py):

    "focus": {"targets": {
        "ceiling": {"on": false},
        "setup":   {"temp": 90, "brightness": 85},
        "bed":     {"temp": 0,  "brightness": 15}
    }}

Allowed keys per setting: on, brightness (1-100), temp (0-100), rgb ([r, g, b]).
"description" is optional on any preset. "mode" from older files is accepted and
ignored (the mode follows from rgb vs temp).

Until you save your own presets.json, the built-in defaults below are used.
presets.json is read on every command, so edits apply immediately.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from tuya_local_mcp import config
from tuya_local_mcp.state import InvalidState, LightState, make_state

# Names that would clash with CLI commands or targets.
RESERVED_NAMES = frozenset(
    {
        "all", "on", "off", "toggle", "t", "status", "scan", "sleep", "rgb",
        "presets", "lights", "init", "serve", "help", "version",
    }
)  # fmt: skip

DEFAULT_PRESETS: dict[str, Any] = {
    "code": {"description": "Bright, cool white for working", "temp": 80, "brightness": 85},
    "chill": {"description": "Dim, warm and relaxed", "temp": 0, "brightness": 20},
    "night": {"description": "Barely-there warm glow", "temp": 0, "brightness": 5},
    "cyber": {"description": "Neon magenta", "rgb": [255, 0, 255], "brightness": 90},
    "red": {"description": "Pure red", "rgb": [255, 0, 0], "brightness": 100},
    "focus": {
        "description": "Ceiling off, cool desk light, soft warm bedside",
        "targets": {
            "ceiling": {"on": False},
            "setup": {"temp": 90, "brightness": 85},
            "bed": {"temp": 0, "brightness": 15},
        },
    },
    "gaming": {
        "description": "Ceiling off, magenta desk, blue bedside",
        "targets": {
            "ceiling": {"on": False},
            "setup": {"rgb": [255, 0, 255], "brightness": 100},
            "bed": {"rgb": [0, 0, 255], "brightness": 40},
        },
    },
    "movie": {
        "description": "Everything off except a faint bedside glow",
        "targets": {
            "ceiling": {"on": False},
            "setup": {"on": False},
            "bed": {"temp": 0, "brightness": 5},
        },
    },
}

_SETTING_KEYS = {"on", "brightness", "temp", "rgb", "mode"}


@dataclass(frozen=True)
class Preset:
    name: str
    description: str | None = None
    state: LightState | None = None  # a single setting for any target
    targets: dict[str, LightState] | None = None  # a scene: target -> setting

    @property
    def is_scene(self) -> bool:
        return self.targets is not None


class PresetError(ValueError):
    """Invalid preset name or contents. The message is written for humans."""


def normalize_name(name: str) -> str:
    key = name.strip().lower()
    if not key or any(c.isspace() for c in key) or "," in key:
        raise PresetError(f"Preset names must be a single word without commas, got {name!r}.")
    if key.rstrip("%").lstrip("-").isdigit():
        raise PresetError(f"'{name}' looks like a brightness value; pick a word instead.")
    if key in RESERVED_NAMES:
        raise PresetError(f"'{key}' is a reserved command name; pick another preset name.")
    return key


def parse_preset(name: str, raw: Any) -> Preset:
    where = f"preset '{name}'"
    if not isinstance(raw, dict):
        raise PresetError(f"{where} must be an object.")
    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise PresetError(f"{where}: description must be text.")

    try:
        if "targets" in raw:
            extra = set(raw) - {"targets", "description"}
            if extra:
                raise PresetError(
                    f"{where}: a scene only takes 'targets' (and 'description'); "
                    f"move {', '.join(sorted(extra))} inside a target."
                )
            targets = raw["targets"]
            if not isinstance(targets, dict) or not targets:
                raise PresetError(f"{where}: 'targets' must map target names to settings.")
            parsed = {
                str(t).strip().lower(): _parse_setting(cfg, f"{where}, target '{t}'")
                for t, cfg in targets.items()
            }
            return Preset(name=name, description=description, targets=parsed)

        body = {k: v for k, v in raw.items() if k != "description"}
        return Preset(name=name, description=description, state=_parse_setting(body, where))
    except InvalidState as e:
        raise PresetError(str(e)) from None


def _parse_setting(cfg: Any, where: str) -> LightState:
    if not isinstance(cfg, dict):
        raise PresetError(f'{where} must be an object like {{"brightness": 50}}.')
    unknown = set(cfg) - _SETTING_KEYS
    if unknown:
        raise PresetError(
            f"{where}: unknown key(s) {', '.join(sorted(unknown))}. "
            "Use on, brightness, temp or rgb."
        )
    return make_state(
        on=cfg.get("on", True),
        brightness=cfg.get("brightness"),
        temp=cfg.get("temp"),
        rgb=cfg.get("rgb"),
        where=where,
    )


def load_raw() -> dict[str, Any]:
    """presets.json as stored, or the built-in defaults if there is none yet."""
    path = config.presets_path()
    if not path.exists():
        return copy.deepcopy(DEFAULT_PRESETS)
    raw = config.read_json(path)
    if not isinstance(raw, dict):
        raise config.ConfigError(f"{path} must contain a JSON object of presets.")
    return raw


def load() -> tuple[dict[str, Preset], list[str]]:
    """All valid presets plus warnings for broken ones (a typo never breaks the rest)."""
    presets: dict[str, Preset] = {}
    warnings: list[str] = []
    for name, raw in load_raw().items():
        key = str(name).strip().lower()
        try:
            presets[key] = parse_preset(key, raw)
        except PresetError as e:
            warnings.append(f"Skipping {e}")
    return presets, warnings


def save(name: str, raw: dict[str, Any]) -> Preset:
    """Validate and store a preset (create or replace). Returns the parsed preset."""
    key = normalize_name(name)
    preset = parse_preset(key, raw)
    stored = load_raw()
    stored[key] = raw
    config.write_json_atomic(config.presets_path(), stored)
    return preset


def delete(name: str) -> bool:
    key = name.strip().lower()
    stored = load_raw()
    if key not in stored:
        return False
    del stored[key]
    config.write_json_atomic(config.presets_path(), stored)
    return True
