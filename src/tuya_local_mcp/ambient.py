"""Ambient mode: Claude sets the room's mood on its own, without being asked.

The same guidance reaches Claude in three ways, depending on the client:

    Claude Code (plugin)   a SessionStart hook adds it to each session (signals.py)
    Claude Desktop / any   TUYA_LOCAL_MCP_AMBIENT=true adds it to the MCP server's
                           instructions, so it applies to every chat
    any client with MCP    the `ambient` MCP prompt switches it on for one chat
                           (Claude Desktop: the "+" menu)
"""

from __future__ import annotations

from datetime import datetime

from tuya_local_mcp import config


def local_time(now: datetime | None = None) -> str:
    """E.g. '23:41 (Tuesday)': enough for "is it late?" decisions."""
    return (now or datetime.now()).strftime("%H:%M (%A)")


def guidance(*, claude_code: bool, now: datetime | None = None) -> str:
    """The ambient-mode instructions.

    `now` is included when the text is produced at the start of a conversation. The
    server's instructions are produced once at startup instead, so they tell Claude
    to read the time from light_status, which always reports it.
    """
    from tuya_local_mcp import devices, presets

    try:
        targets = ", ".join(devices.target_names(devices.load_lights()))
    except config.ConfigError:
        return ""
    try:
        names = ", ".join(presets.load()[0])
    except config.ConfigError:
        names = "code, chill, night, focus"

    if now is not None:
        clock = f"Local time now: {local_time(now)}."
    else:
        clock = "light_status reports the local time; check it once if the hour matters."
    activity = "work" if claude_code else "work or conversation"
    lines = [
        "# Room lights (tuya-local-mcp ambient mode)",
        "You may set the mood of the user's room with the `set_light` tool without being",
        f"asked. {clock} Targets: {targets}. Presets: {names}.",
        "",
        "- Long debugging, studying or focused work: preset `focus` (or `code`).",
        "- Creative, playful or gaming topics: `cyber` or `gaming`.",
        "- Late at night (after about 22:00): `chill`, later `night`.",
        f"- Change the mood at most once per task, and only when the kind of {activity}",
        "  clearly changes. Never undo a light change the user made or asked for.",
    ]
    if claude_code:
        lines.append(
            "- Red/green build and test signals are automatic (hooks): do not do them yourself."
        )
    lines += [
        "- Do it quietly, alongside your answer. At most add a short tag at the end of your",
        "  reply, like `[LIGHT: focus]`.",
    ]
    return "\n".join(lines) + "\n"
