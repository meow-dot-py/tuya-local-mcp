"""The actions both front-ends share.

The CLI and the MCP server both turn their input into a `Command`, run it with
`execute()`, and get back a `Report` describing what happened to every light.
Commands are plain data, so a delayed command can be handed to a background
process as JSON (see timers.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from tuya_local_mcp import devices, engine, presets
from tuya_local_mcp.devices import Light, UnknownTarget
from tuya_local_mcp.engine import Outcome
from tuya_local_mcp.state import RGB, LightState, describe, display_rgb, make_state

Action = Literal["on", "off", "toggle", "set", "preset", "status"]
ACTIONS: tuple[str, ...] = ("on", "off", "toggle", "set", "preset", "status")

PURPLE: RGB = (189, 147, 249)


class CommandError(ValueError):
    """The command cannot run as asked (unknown preset, preset without that target...)."""


@dataclass(frozen=True)
class Command:
    action: Action
    target: str = "all"
    preset: str | None = None
    state: LightState | None = None

    def to_json(self) -> str:
        data: dict[str, Any] = {"action": self.action, "target": self.target}
        if self.preset:
            data["preset"] = self.preset
        if self.state is not None:
            data["state"] = self.state.to_dict()
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str) -> Command:
        data = json.loads(text)
        if data.get("action") not in ACTIONS:
            raise CommandError(f"Unknown action {data.get('action')!r}.")
        state = None
        if isinstance(data.get("state"), dict):
            s = data["state"]
            state = make_state(
                on=s.get("on", True), brightness=s.get("brightness"),
                temp=s.get("temp"), rgb=s.get("rgb"),
            )  # fmt: skip
        return cls(data["action"], data.get("target") or "all", data.get("preset"), state)


@dataclass
class Report:
    title: str
    color: RGB
    outcomes: list[Outcome]
    skipped: list[str] = field(default_factory=list)  # scene targets with no matching light
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return all(o.ok for o in self.outcomes)

    def summary(self) -> str:
        """One plain line, e.g. '[FOCUS] setup: 85% · temp 90 | bed: unreachable'."""
        parts = []
        for o in self.outcomes:
            if o.ok and o.state is not None:
                parts.append(f"{o.light.label}: {describe(o.state)}")
            else:
                parts.append(f"{o.light.label}: FAILED ({o.error})")
        if self.skipped:
            parts.append(f"no light for: {', '.join(self.skipped)}")
        return f"[{self.title}] " + " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "summary": self.summary(),
            "lights": [o.to_dict() for o in self.outcomes],
            "elapsed_ms": self.elapsed_ms,
        }
        if self.skipped:
            out["skipped_targets"] = self.skipped
        if self.warnings:
            out["warnings"] = self.warnings
        return out


def execute(cmd: Command, lights: list[Light] | None = None) -> Report:
    """Run a command against the real lights. Raises UnknownTarget / CommandError on bad input."""
    watch = engine.Stopwatch()
    lights = lights if lights is not None else devices.load_lights()
    report = _dispatch(cmd, lights)
    report.elapsed_ms = watch.ms
    return report


def _dispatch(cmd: Command, lights: list[Light]) -> Report:
    if cmd.action == "status":
        outcomes = engine.run([(li, engine.read_job) for li in devices.resolve(lights, cmd.target)])
        return Report("STATUS", (139, 233, 253), outcomes)

    if cmd.action in ("on", "off"):
        state = LightState(on=cmd.action == "on")
        targets = devices.resolve(lights, cmd.target)
        outcomes = engine.run([(li, engine.write_job(state)) for li in targets])
        color = (80, 250, 123) if state.on else (110, 118, 129)
        return Report(cmd.action.upper(), color, outcomes)

    if cmd.action == "toggle":
        return _toggle(devices.resolve(lights, cmd.target))

    if cmd.action == "set":
        if cmd.state is None:
            raise CommandError("Nothing to set: give a brightness, temp or rgb.")
        targets = devices.resolve(lights, cmd.target)
        outcomes = engine.run([(li, engine.write_job(cmd.state)) for li in targets])
        return Report(describe(cmd.state).upper(), display_rgb(cmd.state), outcomes)

    if cmd.action == "preset":
        return _apply_preset(cmd, lights)

    raise CommandError(f"Unknown action {cmd.action!r}.")


def _toggle(targets: list[Light]) -> Report:
    """Group toggle: if any target is on, turn them all off; otherwise turn them all on.

    Inverting each light on its own would leave a mixed group still mixed.
    """
    reads = engine.run([(li, engine.read_job) for li in targets])
    reachable = [o.light for o in reads if o.ok]
    failed = [o for o in reads if not o.ok]
    turn_on = not any(o.state is not None and o.state.on for o in reads if o.ok)
    state = LightState(on=turn_on)
    writes = engine.run([(li, engine.write_job(state)) for li in reachable], repair=False)
    color = (80, 250, 123) if turn_on else (110, 118, 129)
    return Report(f"TOGGLE → {'ON' if turn_on else 'OFF'}", color, writes + failed)


def plan_preset(
    preset: presets.Preset, lights: list[Light], target: str = "all"
) -> tuple[list[tuple[Light, LightState]], list[str]]:
    """Decide which light gets which setting. Returns (assignments, scene targets with no light).

    `target` narrows a scene: `l bed focus` applies only focus's bed setting.
    """
    wanted = devices.resolve(lights, target)
    if not preset.is_scene:
        assert preset.state is not None
        return [(li, preset.state) for li in wanted], []

    assert preset.targets is not None
    wanted_ids = {li.id for li in wanted}
    assigned: list[tuple[Light, LightState]] = []
    skipped: list[str] = []
    for scene_target, setting in preset.targets.items():
        try:
            matches = devices.resolve(lights, scene_target)
        except UnknownTarget:
            skipped.append(scene_target)
            continue
        for li in matches:
            if li.id in wanted_ids and all(li.id != a.id for a, _ in assigned):
                assigned.append((li, setting))

    if not assigned:
        scene_targets = ", ".join(preset.targets)
        if target.strip().lower() in devices.ALL_TARGETS:
            raise CommandError(
                f"Scene '{preset.name}' sets {scene_targets}, but none of those match your "
                f"lights ({', '.join(devices.target_names(lights)[1:])}). Edit the scene's "
                'targets or add a "zone" to your lights in devices.json.'
            )
        raise CommandError(
            f"Scene '{preset.name}' has no setting for '{target}' ({scene_targets})."
        )
    # When the person narrowed the scene to some lights, missing zones are not news.
    if target.strip().lower() not in devices.ALL_TARGETS:
        skipped = []
    return assigned, skipped


def preset_color(preset: presets.Preset) -> RGB:
    if preset.state is not None:
        return display_rgb(preset.state)
    for setting in (preset.targets or {}).values():
        if setting.on is not False:
            return display_rgb(setting)
    return PURPLE


def _apply_preset(cmd: Command, lights: list[Light]) -> Report:
    available, warnings = presets.load()
    name = (cmd.preset or "").strip().lower()
    preset = available.get(name)
    if preset is None:
        names = ", ".join(sorted(available)) or "none yet"
        raise CommandError(f"No preset called '{cmd.preset}'. Available: {names}.")
    assigned, skipped = plan_preset(preset, lights, cmd.target)
    outcomes = engine.run([(li, engine.write_job(st)) for li, st in assigned])
    return Report(name.upper(), preset_color(preset), outcomes, skipped, warnings)
