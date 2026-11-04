"""Avoiding duplicates when both the plugin and Claude Desktop's config are set up.

The Claude desktop app runs its Code tab with an embedded Claude Code, and it hands
those sessions every MCP server from claude_desktop_config.json as well. So someone
who uses the plugin (for Claude Code) AND the desktop config entry (for the chat)
would get every light tool twice in Code sessions, and the ambient guidance twice.

When that is the case, the plugin's copy steps aside: its MCP server starts with no
tools, and its SessionStart hook leaves ambient mode to the desktop entry. Nothing
changes in the terminal CLI, which never reads claude_desktop_config.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def in_desktop_app() -> bool:
    """True inside a Code tab session of the Claude desktop app."""
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop"


def in_plugin() -> bool:
    """True when started by the Claude Code plugin (its MCP server or hooks)."""
    return bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))


def config_candidates() -> list[Path]:
    """Where claude_desktop_config.json may live, most specific first."""
    paths: list[Path] = []
    # The embedded Claude Code lives inside the app's data folder; walk up from it.
    exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
    if exec_path:
        for parent in list(Path(exec_path).parents)[:6]:
            paths.append(parent / "claude_desktop_config.json")
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.extend(sorted((Path(local) / "Claude-Data").glob("*/claude_desktop_config.json")))
    home = Path.home()
    paths.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    paths.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return paths


def desktop_server() -> dict[str, Any] | None:
    """Our server's entry in claude_desktop_config.json, if this Code session gets it."""
    if not in_desktop_app():
        return None
    for path in config_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            continue
        for entry in servers.values():
            if isinstance(entry, dict) and _runs_us(entry):
                return entry
    return None


def _runs_us(entry: dict[str, Any]) -> bool:
    parts = [str(entry.get("command", ""))] + [str(a) for a in entry.get("args") or []]
    return any("tuya-local-mcp" in p or "tuya_local_mcp" in p for p in parts)


def desktop_ambient_on(entry: dict[str, Any] | None) -> bool:
    env = (entry or {}).get("env") or {}
    value = str(env.get("TUYA_LOCAL_MCP_AMBIENT", "")).strip().lower()
    return value in ("1", "true", "yes", "on")


def plugin_should_step_aside() -> bool:
    """The plugin's MCP server is redundant: the desktop entry already serves this session."""
    return in_plugin() and desktop_server() is not None
