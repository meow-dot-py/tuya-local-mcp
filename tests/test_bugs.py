"""Regression tests for the four bugs found in the original prototype."""

from __future__ import annotations

import pytest

from tuya_local_mcp import cli, devices
from tuya_local_mcp.commands import Command, CommandError, execute
from tuya_local_mcp.devices import UnknownTarget

from .conftest import write_devices


# Bug 1: `l` with no arguments started the MCP server and hung in a terminal.
def test_no_args_in_terminal_toggles(three_lights, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "serve", lambda: pytest.fail("must not start the MCP server"))
    assert cli.main([]) == 0
    assert all(dps["20"] is True for dps in three_lights.dps.values())


def test_no_args_from_claude_code_serves(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    called = []
    monkeypatch.setattr(cli, "serve", lambda: called.append(True) or 0)
    assert cli.main([]) == 0
    assert called


# Bug 2: failures were reported as success.
def test_unreachable_light_is_reported_as_failure(three_lights, capsys):
    three_lights.reachable_ip["b1"] = "10.0.0.99"  # bed moved...
    three_lights.reachable_ip["c1"] = ""  # ...and the ceiling is unplugged

    report = execute(Command("on"))

    by_id = {o.light.id: o for o in report.outcomes}
    assert by_id["s1"].ok
    assert by_id["b1"].ok, "IP repair should have found the bed at its new IP"
    assert not by_id["c1"].ok and "unreachable" in by_id["c1"].error
    assert not report.ok
    assert three_lights.scans == 1
    assert devices.load_lights()[2].ip == "10.0.0.99", "new IP saved to devices.json"
    assert cli.main(["on"]) == 1, "CLI exit code signals the failure"


def test_light_alive_at_same_ip_is_retried_after_a_hiccup(three_lights):
    three_lights.hiccup.add("s1")
    report = execute(Command("on", "setup"))
    assert report.ok and three_lights.scans == 1


# Bug 3: a scene ignored the target (`l bed focus` applied the whole scene).
def test_scene_narrowed_by_target(three_lights):
    execute(Command("preset", "bed", preset="focus"))
    touched = {dev_id for dev_id, _ in three_lights.writes}
    assert touched == {"b1"}


def test_scene_without_setting_for_target_is_an_error(home, fleet):
    write_devices(home, {"name": "Desk", "id": "d1", "ip": "10.0.0.5"})
    fleet.add("d1", "10.0.0.5")
    with pytest.raises(CommandError, match="none of those match"):
        execute(Command("preset", preset="focus"))


# Bug 4: with one bulb, any target (and any typo) controlled it.
def test_single_light_does_not_answer_to_other_targets(home, fleet):
    write_devices(home, {"name": "Luz Setup", "id": "s1", "ip": "10.0.0.2"})
    fleet.add("s1", "10.0.0.2", on=True)
    with pytest.raises(UnknownTarget, match="Valid targets: all, luz setup"):
        execute(Command("off", "bed"))
    assert fleet.dps["s1"]["20"] is True


def test_partial_words_do_not_match(three_lights):
    lights = devices.load_lights()
    with pytest.raises(UnknownTarget):
        devices.resolve(lights, "s")
    assert [li.id for li in devices.resolve(lights, "setup")] == ["s1"]
    assert [li.id for li in devices.resolve(lights, "luz setup")] == ["s1"]
    assert [li.id for li in devices.resolve(lights, "bed,ceiling")] == ["b1", "c1"]


def test_scene_skips_missing_zones_but_applies_the_rest(home, fleet):
    write_devices(home, {"name": "Luz Setup", "id": "s1", "ip": "10.0.0.2"})
    fleet.add("s1", "10.0.0.2")
    report = execute(Command("preset", preset="focus"))
    assert report.ok
    assert report.skipped == ["ceiling", "bed"]
    assert fleet.dps["s1"]["21"] == "white" and fleet.dps["s1"]["22"] == 850
