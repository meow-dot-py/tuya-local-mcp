"""Build/test signals driven by Claude Code hook events."""

from __future__ import annotations

import json

import pytest

from tuya_local_mcp import cli, signals


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(signals.time, "sleep", lambda seconds: None)
    # Run the detached worker inline, so tests can see its effect.
    monkeypatch.setattr(signals, "_spawn", lambda args: signals.run_worker(args[1:]))
    for key in ("SIGNAL_TARGET", "SIGNAL_COMMANDS", "SIGNALS", "AMBIENT"):
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{key}", raising=False)
        monkeypatch.delenv(f"TUYA_LOCAL_MCP_{key}", raising=False)


def bash_event(command: str, *, failed: bool, **extra) -> str:
    event = {
        "hook_event_name": "PostToolUseFailure" if failed else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        **extra,
    }
    return json.dumps(event)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("uv run pytest -q", True),
        ("npm test", True),
        ("pnpm run build", True),
        ("cargo test --all", True),
        ("go vet ./...", True),
        ("git status", False),
        ("ls tests/", False),
        ("cat pytest.ini", True),  # best effort: mentions pytest
    ],
)
def test_signal_commands(command, expected):
    assert signals.is_signal_command(command) is expected


def test_fail_then_pass_restores_previous_state(three_lights):
    three_lights.dps["s1"].update({"20": True, "21": "white", "22": 300, "23": 0})
    three_lights.dps["b1"].update({"20": False})

    signals.handle_hook(bash_event("pytest", failed=True))
    for dev_id in ("c1", "s1", "b1"):
        assert three_lights.dps[dev_id]["21"] == "colour"
        assert three_lights.dps[dev_id]["24"] == "000003e803e8"  # pure red, full brightness

    writes_before = len(three_lights.writes)
    signals.handle_hook(bash_event("pytest", failed=True))  # still failing: no change
    assert len(three_lights.writes) == writes_before

    signals.handle_hook(bash_event("pytest", failed=False))
    assert three_lights.dps["s1"]["21"] == "white"
    assert three_lights.dps["s1"]["22"] == 300 and three_lights.dps["s1"]["23"] == 0
    assert three_lights.dps["b1"]["20"] is False
    greens = [w for _, w in three_lights.writes if w.get("24") == "008a03e803e8"]
    assert len(greens) == 3, "green flash before restoring"


def test_pass_without_failure_does_nothing(three_lights):
    signals.handle_hook(bash_event("pytest", failed=False))
    assert three_lights.writes == []


@pytest.mark.parametrize(
    "event",
    [
        bash_event("git push", failed=True),
        bash_event("pytest", failed=True, is_interrupt=True),
        json.dumps({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                    "tool_input": {"command": "pytest", "run_in_background": True}}),
        json.dumps({"hook_event_name": "PostToolUseFailure", "tool_name": "Edit"}),
        "",
        "not json",
    ],
)  # fmt: skip
def test_ignored_events(three_lights, event):
    assert signals.handle_hook(event) == 0
    assert three_lights.writes == []


def test_signal_target_option(three_lights, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SIGNAL_TARGET", "setup")
    signals.handle_hook(bash_event("make", failed=True))
    assert {dev_id for dev_id, _ in three_lights.writes} == {"s1"}


def test_signals_can_be_turned_off(three_lights, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SIGNALS", "false")
    signals.handle_hook(bash_event("pytest", failed=True))
    assert three_lights.writes == []


def test_hook_errors_never_escape(home, monkeypatch):
    monkeypatch.setattr(signals, "_spawn", lambda args: 1 / 0)
    assert signals.handle_hook(bash_event("pytest", failed=True)) == 0
    assert "ZeroDivisionError" in (home / "hook.log").read_text(encoding="utf-8")


def test_worker_errors_are_logged(home, monkeypatch):
    monkeypatch.setattr(signals, "signal_fail", lambda: 1 / 0)
    assert signals.run_worker(["fail"]) == 0
    assert "ZeroDivisionError" in (home / "hook.log").read_text(encoding="utf-8")


def test_hook_spawns_worker_and_returns(three_lights, monkeypatch):
    spawned = []
    monkeypatch.setattr(signals, "_spawn", spawned.append)
    signals.handle_hook(bash_event("pytest", failed=True))
    assert spawned == [["_signal", "fail"]]
    assert three_lights.writes == [], "the hook itself never touches the lights"
    signals.handle_hook(bash_event("pytest", failed=False))
    assert len(spawned) == 1, "a pass with nothing failing does not even spawn"


def test_ambient_context_only_when_enabled(three_lights, monkeypatch, capsys):
    start = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
    signals.handle_hook(start)
    assert capsys.readouterr().out == ""

    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_AMBIENT", "true")
    signals.handle_hook(start)
    out = json.loads(capsys.readouterr().out)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "Targets: all, ceiling light, setup, bed" in context or "Targets: all" in context
    assert "focus" in context


def test_cli_signal_command(three_lights, capsys):
    assert cli.main(["signal", "fail", "bed"]) == 0
    assert {dev_id for dev_id, _ in three_lights.writes} == {"b1"}
    assert cli.main(["signal", "pass"]) == 0
    assert "restored 1 light" in capsys.readouterr().out
    assert cli.main(["signal", "maybe"]) == 2
