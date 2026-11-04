"""Timers can be listed and cancelled; a cancelled timer never fires."""

from __future__ import annotations

import time

import pytest

from tuya_local_mcp import cli, timers
from tuya_local_mcp.commands import Command
from tuya_local_mcp.state import LightState


@pytest.fixture
def no_spawn(monkeypatch):
    """Register timers without starting background processes."""
    spawned = []
    monkeypatch.setattr(timers, "spawn_detached", spawned.append)
    return spawned


def test_schedule_registers_and_lists(home, no_spawn):
    first = timers.schedule(600, Command("off", "bed"))
    second = timers.schedule(60, Command("preset", preset="night"))
    assert no_spawn == [["_run-later", first.id], ["_run-later", second.id]]
    pending = timers.list_timers()
    assert [t.id for t in pending] == [second.id, first.id], "soonest first"
    assert pending[0].what == "night" and pending[1].what == "off"
    assert pending[0].to_dict()["in"] in ("1m", "59s")


def test_cancel_one_all_and_unknown(home, no_spawn):
    a = timers.schedule(600, Command("off"))
    b = timers.schedule(600, Command("on"))
    assert [t.id for t in timers.cancel(a.id)] == [a.id]
    assert [t.id for t in timers.list_timers()] == [b.id]
    with pytest.raises(ValueError, match=f"Pending: {b.id}"):
        timers.cancel("zzzz")
    assert [t.id for t in timers.cancel("all")] == [b.id]
    assert timers.list_timers() == [] and timers.cancel() == []


def test_cancelled_timer_never_fires(three_lights, no_spawn, monkeypatch):
    timer = timers.schedule(5, Command("on"))
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        timers.cancel(timer.id)  # cancelled while the worker waits

    monkeypatch.setattr(timers.time, "sleep", fake_sleep)
    assert timers.run_later([timer.id]) == 0
    assert three_lights.writes == [] and len(sleeps) == 1


def test_due_timer_fires_once(three_lights, no_spawn, monkeypatch):
    timer = timers.schedule(1, Command("set", "setup", state=LightState(on=True, brightness=40)))
    monkeypatch.setattr(timers.time, "sleep", lambda s: None)
    monkeypatch.setattr(timers.time, "time", lambda: timer.due + 1)
    assert timers.run_later([timer.id]) == 0
    assert three_lights.dps["s1"]["22"] == 400
    assert timers.list_timers() == [], "a fired timer is no longer pending"


def test_stale_and_broken_files_are_cleaned(home, no_spawn, monkeypatch):
    timers.schedule(60, Command("off"))
    (timers.timers_dir() / "bad1.json").write_text("{not json", encoding="utf-8")
    later = time.time() + 60 + timers.STALE_AFTER + 1
    monkeypatch.setattr(timers.time, "time", lambda: later)
    assert timers.list_timers() == []
    assert list(timers.timers_dir().glob("*.json")) == []


def test_cli_timers_and_cancel(three_lights, no_spawn, capsys):
    assert cli.main(["bed", "sleep", "10m", "night"]) == 0
    out = capsys.readouterr().out
    timer_id = out.split("[TIMER ")[1][:4]
    assert f"l cancel {timer_id}" in out
    assert cli.main(["timers"]) == 0
    assert "night (bed)" in capsys.readouterr().out
    assert cli.main(["cancel", "nope"]) == 2
    assert cli.main(["cancel"]) == 0
    assert f"CANCELLED {timer_id}" in capsys.readouterr().out
    assert cli.main(["timers"]) == 0
    assert "No pending timers" in capsys.readouterr().out
