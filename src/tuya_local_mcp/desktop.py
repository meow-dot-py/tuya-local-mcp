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
import subprocess
import sys
from pathlib import Path
from typing import Any


def in_desktop_app() -> bool:
    """True inside a Code tab session of the Claude desktop app.

    The app marks its sessions with CLAUDE_CODE_ENTRYPOINT=claude-desktop, but a
    `claude` started from a terminal inside the app inherits that mark too, and that
    one does NOT get the app's servers. So we also check the process tree: the
    nearest `claude` above us must itself be a child of the desktop app (`Claude`).
    """
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop":
        return False
    found = _claude_ancestor()
    return found is not None and found[1]


def _claude_ancestor() -> tuple[str, bool] | None:
    """(path of the nearest `claude` ancestor, whether ITS parent is a `claude` too).

    None when the process tree cannot be read; callers then assume "not inside the
    app", which at worst shows duplicate tools instead of hiding them.
    """
    try:
        table = _process_table()
    except Exception:
        return None
    pid = os.getppid()
    for _ in range(12):
        entry = table.get(pid)
        if entry is None:
            return None
        parent, name = entry
        if _is_claude(name):
            above = table.get(parent)
            return _full_path(pid, name), bool(above and _is_claude(above[1]))
        pid = parent
    return None


def _is_claude(name: str) -> bool:
    return Path(name).name.lower() in ("claude", "claude.exe")


def _process_table() -> dict[int, tuple[int, str]]:
    """{pid: (parent pid, executable name or path)} for every process."""
    if sys.platform == "win32":
        return _process_table_windows()
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "comm="],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout  # fmt: skip
    table: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2].strip())
    return table


def _process_table_windows() -> dict[int, tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]  # fmt: skip

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snapshot = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError("process snapshot failed")
    table: dict[int, tuple[int, str]] = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = k32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
    finally:
        k32.CloseHandle(ctypes.c_void_p(snapshot))
    return table


def _full_path(pid: int, name: str) -> str:
    """Full executable path of a process (best effort; falls back to its name)."""
    if sys.platform != "win32":
        return name
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return name
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass
    return name


def in_plugin() -> bool:
    """True when started by the Claude Code plugin (its MCP server or hooks)."""
    return bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))


def config_candidates() -> list[Path]:
    """Where claude_desktop_config.json may live, most specific first."""
    paths: list[Path] = []
    # The embedded Claude Code lives inside the app's data folder; walk up from it.
    found = _claude_ancestor()
    for exe in (found[0] if found else None, os.environ.get("CLAUDE_CODE_EXECPATH")):
        if exe and Path(exe).is_absolute():
            for parent in list(Path(exe).parents)[:6]:
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
