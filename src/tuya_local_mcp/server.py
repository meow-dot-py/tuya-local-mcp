"""The MCP server (stdio transport), for Claude Code, Claude Desktop or any MCP client.

Register it once:

    Claude Code:     claude mcp add tuya-local -s user -- tuya-local-mcp serve
                     (or install the plugin, which includes it)
    Claude Desktop:  see "Claude Desktop" in the README

Set TUYA_LOCAL_MCP_AMBIENT=true in the server's environment to include the ambient
mode guidance in its instructions (useful in clients without hooks). The `ambient`
prompt switches it on for a single conversation instead.

Every tool returns a dict with a ready-made one-line `summary` (the same line the
CLI prints, without colours) so Claude can confirm an action without rephrasing.
Bad input raises an error with a message that says how to fix it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from tuya_local_mcp import __version__, ambient, devices, presets, timers
from tuya_local_mcp.commands import Command, CommandError, execute, plan_preset
from tuya_local_mcp.config import ConfigError, home_dir, presets_path
from tuya_local_mcp.state import describe, make_state


def _instructions() -> str:
    """Tell Claude the targets and presets up front, so it can act without a lookup call."""
    lines = [
        "Local LAN control of Tuya / Smart Life lights. Act directly: power_light and "
        "set_light do not need a status or list call first.",
        "brightness is 1-100, temp is 0 (warm) to 100 (cool), rgb is [r, g, b] 0-255.",
        "Use delay_seconds for anything that should happen later; timers survive this session.",
    ]
    try:
        lights = devices.load_lights()
        described = ", ".join(f"{li.label}" + (f" ({li.name})" if li.zone else "") for li in lights)
        lines.append(f"Targets: all, {described}. Combine with commas, e.g. 'bed,setup'.")
    except ConfigError as e:
        lines.append(f"No lights are configured yet: {e}")
    try:
        available, _ = presets.load()
        lines.append(
            f"Presets at startup: {', '.join(available)}. presets.json can change while "
            "running; call get_presets if a name is unknown."
        )
    except ConfigError:
        pass
    if _ambient_enabled():
        lines += ["", ambient.guidance(claude_code=False)]
    return "\n".join(lines)


def _ambient_enabled() -> bool:
    import os

    value = os.environ.get("TUYA_LOCAL_MCP_AMBIENT", "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def build_server() -> Any:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    mcp = MCPServer("tuya-local-mcp", instructions=_instructions())

    def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a tool whose input errors reach Claude with their message.

        The SDK hides the text of unexpected exceptions (it only says "Error executing
        tool"), so our own errors, which explain how to fix the input, become ToolError.
        """

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except (ValueError, ConfigError) as e:
                raise ToolError(str(e)) from None

        return mcp.tool()(wrapper)

    @tool
    def list_lights() -> dict:
        """List lights (name, zone, every word that targets them, IP) and preset names."""
        lights = devices.load_lights()
        available, warnings = presets.load()
        return {
            "lights": [
                {"name": li.name, "zone": li.zone, "targets": sorted(li.aliases), "ip": li.ip}
                for li in lights
            ],
            "presets": sorted(available),
            "warnings": warnings,
            "config_dir": str(home_dir()),
        }

    @tool
    def light_status(target: str = "all") -> dict:
        """Read what each target light is doing now (on, mode, brightness, temp, rgb),
        plus the user's local time."""
        report = execute(Command("status", target)).to_dict()
        report["local_time"] = ambient.local_time()
        return report

    @tool
    def power_light(
        state: Literal["on", "off", "toggle"], target: str = "all", delay_seconds: int = 0
    ) -> dict:
        """Turn lights on, off, or toggle them as a group (any on -> all off, else all on).

        delay_seconds > 0 schedules it instead, in a background process.
        """
        return _run_or_schedule(Command(state, target), delay_seconds)

    @tool
    def set_light(
        target: str = "all",
        preset: str | None = None,
        brightness: int | None = None,
        temp: int | None = None,
        rgb: list[int] | None = None,
        delay_seconds: int = 0,
    ) -> dict:
        """Apply a preset/scene, or set brightness (1-100) with temp (0 warm-100 cool) OR rgb.

        With a scene preset, target narrows it: target='bed', preset='focus' applies only
        focus's bed setting. Use either preset or manual values, not both.
        """
        if preset and any(v is not None for v in (brightness, temp, rgb)):
            raise ValueError("Use either preset or brightness/temp/rgb, not both.")
        if preset:
            return _run_or_schedule(Command("preset", target, preset=preset.lower()), delay_seconds)
        if all(v is None for v in (brightness, temp, rgb)):
            raise ValueError("Give a preset, or at least one of brightness, temp or rgb.")
        state = make_state(brightness=brightness, temp=temp, rgb=rgb)
        return _run_or_schedule(Command("set", target, state=state), delay_seconds)

    @tool
    def get_presets() -> dict:
        """Return presets.json as stored (built-in defaults if you have not saved any yet)."""
        return presets.load_raw()

    @tool
    def upsert_preset(name: str, config: dict) -> dict:
        """Create or replace a preset. The config is validated before saving.

        Single setting:  {"temp": 20, "brightness": 50, "description": "optional"}
                         {"rgb": [255, 0, 128], "brightness": 80}
        Scene:           {"targets": {"ceiling": {"on": false},
                                      "setup": {"rgb": [255, 0, 128], "brightness": 80},
                                      "bed": {"temp": 0, "brightness": 15}}}
        Keys per setting: on, brightness (1-100), temp (0-100), rgb. temp and rgb are exclusive.
        """
        saved = presets.save(name, config)
        return {"saved": saved.name, "scene": saved.is_scene, "path": str(presets_path())}

    @tool
    def delete_preset(name: str) -> dict:
        """Delete a preset from presets.json."""
        if not presets.delete(name):
            raise ValueError(f"No preset called '{name}'.")
        return {"deleted": name.strip().lower()}

    @tool
    def scan_lan_ips() -> dict:
        """Listen for Tuya broadcasts (~6 s) and save any light IP that changed.

        Commands already do this automatically when a light is unreachable.
        """
        result = devices.repair_ips()
        return {"changed": result.changed, "not_found": result.missing}

    @mcp.prompt()
    def ambient_mode() -> str:
        """Ambient lights: let Claude set the room's mood on its own during this chat."""
        text = ambient.guidance(claude_code=False, now=datetime.now())
        if not text:
            return "No lights are configured yet; run `tuya-local-mcp init` first."
        return text + "\nReply only with one short line confirming ambient lights are on."

    return mcp


def _run_or_schedule(cmd: Command, delay_seconds: int) -> dict:
    lights = devices.load_lights()
    # Validate everything first, so a delayed command cannot fail silently later.
    if cmd.action == "preset":
        available, _ = presets.load()
        if cmd.preset not in available:
            names = ", ".join(sorted(available))
            raise CommandError(f"No preset called '{cmd.preset}'. Available: {names}.")
        plan_preset(available[cmd.preset], lights, cmd.target)
    else:
        devices.resolve(lights, cmd.target)

    if delay_seconds > 0:
        if delay_seconds > timers.MAX_DELAY:
            raise ValueError("delay_seconds can be at most 7 days (604800).")
        timers.schedule(delay_seconds, cmd)
        when = timers.format_duration(delay_seconds)
        what = cmd.preset or (describe(cmd.state) if cmd.state else cmd.action)
        return {
            "scheduled": True,
            "in": when,
            "summary": f"[TIMER] in {when}: {what} ({cmd.target})",
        }
    return execute(cmd, lights).to_dict()


def run_server() -> None:
    import logging

    # tinytuya and the SDK log to stderr; keep it quiet unless something is wrong.
    logging.basicConfig(level=logging.WARNING)
    build_server().run()


__all__ = ["__version__", "build_server", "run_server"]
