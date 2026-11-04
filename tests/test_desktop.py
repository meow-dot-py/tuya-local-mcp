"""The plugin steps aside when Claude Desktop's config already provides the server."""

from __future__ import annotations

import json

import pytest

from tuya_local_mcp import desktop, server, signals


@pytest.fixture
def desktop_app(tmp_path, monkeypatch):
    """Pretend we run inside a Code tab session of the desktop app (Store-style data dir)."""
    data = tmp_path / "Claude-Data" / "claude_abc"
    cli = data / "claude-code" / "2.1.317" / "x" / "claude.exe"
    cli.parent.mkdir(parents=True)
    for key in ("APPDATA", "LOCALAPPDATA", "CLAUDE_PLUGIN_ROOT", "TUYA_LOCAL_MCP_AMBIENT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", str(cli))
    monkeypatch.setattr(desktop.Path, "home", lambda: tmp_path / "home")
    # Process tree: our nearest `claude` is the app's embedded CLI, a child of the app.
    monkeypatch.setattr(desktop, "_claude_ancestor", lambda: (str(cli), True))

    def write(servers: dict) -> None:
        (data / "claude_desktop_config.json").write_text(
            json.dumps({"mcpServers": servers, "preferences": {}}), encoding="utf-8"
        )

    return write


ENTRY = {"command": "C:\\bin\\tuya-local-mcp.exe", "args": ["serve"]}


def test_finds_our_entry_next_to_the_embedded_cli(desktop_app):
    desktop_app({"other": {"command": "node", "args": ["x.js"]}, "lights": ENTRY})
    assert desktop.desktop_server() == ENTRY


def test_no_entry_or_not_in_desktop(desktop_app, monkeypatch):
    desktop_app({"other": {"command": "node"}})
    assert desktop.desktop_server() is None
    desktop_app({"lights": ENTRY})
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")  # the terminal CLI
    assert desktop.desktop_server() is None


def test_terminal_claude_inside_the_app_is_not_the_app(desktop_app, monkeypatch):
    """`claude` run from a terminal in the app inherits the app's env but not its servers."""
    desktop_app({"lights": ENTRY})
    monkeypatch.setattr(desktop, "_claude_ancestor", lambda: ("C:/bin/claude.exe", False))
    assert desktop.desktop_server() is None
    monkeypatch.setattr(desktop, "_claude_ancestor", lambda: None)  # tree unreadable
    assert desktop.desktop_server() is None, "when unsure, prefer duplicates over no tools"


def test_process_table_reads_this_process():
    table = desktop._process_table()
    import os

    assert os.getpid() in table and table[os.getpid()][0] == os.getppid()


def test_plugin_server_goes_idle(desktop_app, monkeypatch, three_lights):
    desktop_app({"lights": ENTRY})
    assert not desktop.plugin_should_step_aside(), "not the plugin's copy"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugins/tuya-local")
    assert desktop.plugin_should_step_aside()
    import asyncio

    tools = asyncio.run(server.build_server().list_tools())
    assert tools == []


def test_plugin_server_normal_without_desktop_entry(desktop_app, monkeypatch, three_lights):
    desktop_app({})
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugins/tuya-local")
    import asyncio

    tools = asyncio.run(server.build_server().list_tools())
    assert len(tools) == 8


def test_hook_leaves_ambient_to_desktop_entry(desktop_app, monkeypatch, three_lights, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_AMBIENT", "true")
    start = json.dumps({"hook_event_name": "SessionStart"})

    desktop_app({"lights": ENTRY})  # desktop entry without ambient: the hook provides it
    signals.handle_hook(start)
    assert "ambient mode" in capsys.readouterr().out

    desktop_app({"lights": {**ENTRY, "env": {"TUYA_LOCAL_MCP_AMBIENT": "true"}}})
    signals.handle_hook(start)
    assert capsys.readouterr().out == ""
