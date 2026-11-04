"""Ambient-mode guidance for Claude Code and for clients without hooks."""

from __future__ import annotations

from datetime import datetime

from tuya_local_mcp import ambient, server


def test_guidance_variants(three_lights):
    night = datetime(2026, 11, 3, 23, 41)
    code = ambient.guidance(claude_code=True, now=night)
    chat = ambient.guidance(claude_code=False, now=night)
    assert "Local time now: 23:41 (Tuesday)" in code
    assert "Targets: all, ceiling light, luz setup, bed" in code
    assert "hooks" in code and "hooks" not in chat
    assert "work or conversation" in chat

    startup = ambient.guidance(claude_code=False)
    assert "light_status reports the local time" in startup


def test_no_guidance_without_lights(home):
    assert ambient.guidance(claude_code=False) == ""


def test_server_instructions_include_ambient_only_when_enabled(three_lights, monkeypatch):
    monkeypatch.delenv("TUYA_LOCAL_MCP_AMBIENT", raising=False)
    assert "ambient mode" not in server._instructions()
    monkeypatch.setenv("TUYA_LOCAL_MCP_AMBIENT", "true")
    text = server._instructions()
    assert "Targets: all" in text and "ambient mode" in text
