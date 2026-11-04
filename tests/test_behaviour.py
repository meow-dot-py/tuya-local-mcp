"""Protocol encoding, presets, timers and CLI parsing."""

from __future__ import annotations

import json

import pytest

from tuya_local_mcp import cli, presets, protocol, timers
from tuya_local_mcp.commands import Command, execute
from tuya_local_mcp.presets import PresetError
from tuya_local_mcp.state import InvalidState, LightState, make_state

from .conftest import MODERN_MAPPING

PROFILE = protocol.profile_from_mapping(MODERN_MAPPING)


def test_profile_from_wizard_mapping():
    assert protocol.Profile(
        switch="20", mode="21", brightness="22", brightness_max=1000, brightness_min=10,
        temp="23", temp_max=1000, colour="24", colour_format="hsv16",
    ) == PROFILE  # fmt: skip


def test_white_is_one_message():
    dps = protocol.encode(PROFILE, LightState(on=True, brightness=85, temp=90))
    assert dps == {"20": True, "21": "white", "22": 850, "23": 900}


def test_colour_uses_brightness_as_value():
    dps = protocol.encode(PROFILE, LightState(on=True, rgb=(255, 0, 0), brightness=50))
    assert dps == {"20": True, "21": "colour", "24": "000003e801f4"}


def test_brightness_only_keeps_colour_mode():
    current = LightState(on=True, mode="colour", rgb=(0, 0, 255), brightness=100)
    assert protocol.needs_current_state(PROFILE, LightState(on=True, brightness=20))
    dps = protocol.encode(PROFILE, LightState(on=True, brightness=20), current)
    assert dps["21"] == "colour" and dps["24"] == "00f003e800c8"


def test_decode_round_trip():
    state = protocol.decode(PROFILE, {"20": True, "21": "colour", "24": "000003e801f4"})
    assert state == LightState(on=True, mode="colour", rgb=(255, 0, 0), brightness=50)
    white = protocol.decode(PROFILE, {"20": False, "21": "white", "22": 10, "23": 1000})
    assert white == LightState(on=False, mode="white", brightness=1, temp=100)


def test_brightness_in_colour_mode_end_to_end(three_lights):
    three_lights.dps["s1"].update({"20": True, "21": "colour", "24": "00f003e803e8"})
    report = execute(Command("set", "setup", state=LightState(on=True, brightness=30)))
    assert report.ok
    assert three_lights.dps["s1"]["24"] == "00f003e8012c"


def test_toggle_is_a_group_action(three_lights):
    three_lights.dps["s1"]["20"] = True  # one of three on -> all off
    execute(Command("toggle"))
    assert [d["20"] for d in three_lights.dps.values()] == [False, False, False]
    execute(Command("toggle"))
    assert [d["20"] for d in three_lights.dps.values()] == [True, True, True]


# --- presets ------------------------------------------------------------------


def test_legacy_prototype_presets_still_load(home):
    legacy = {
        "code": {"mode": "white", "temp": 80, "brightness": 85, "rgb": None},
        "cyber": {"mode": "colour", "temp": None, "brightness": 90, "rgb": [255, 0, 255]},
    }
    (home / "presets.json").write_text(json.dumps(legacy), encoding="utf-8")
    loaded, warnings = presets.load()
    assert not warnings
    assert loaded["code"].state == LightState(on=True, brightness=85, temp=80)
    assert loaded["cyber"].state == LightState(on=True, brightness=90, rgb=(255, 0, 255))


def test_broken_preset_is_skipped_not_fatal(home):
    data = {"ok": {"brightness": 50}, "bad": {"brightness": 500}}
    (home / "presets.json").write_text(json.dumps(data), encoding="utf-8")
    loaded, warnings = presets.load()
    assert list(loaded) == ["ok"]
    assert "brightness must be between 1 and 100" in warnings[0]


@pytest.mark.parametrize(
    ("name", "config", "message"),
    [
        ("rgb", {"brightness": 5}, "reserved"),
        ("my scene", {"brightness": 5}, "single word"),
        ("x", {"brightnes": 5}, "unknown key"),
        ("x", {"rgb": [1, 2, 3], "temp": 5}, "not both"),
        ("x", {"targets": {"bed": {"rgb": [1, 2]}}}, "three numbers"),
        ("x", {"targets": {}, "brightness": 4}, "only takes 'targets'"),
    ],
)
def test_upsert_validation(home, name, config, message):
    with pytest.raises(PresetError, match=message):
        presets.save(name, config)
    assert not (home / "presets.json").exists()


def test_save_and_delete_preset(home):
    presets.save("Reading", {"temp": 30, "brightness": 60})
    stored = json.loads((home / "presets.json").read_text(encoding="utf-8"))
    assert stored["reading"] == {"temp": 30, "brightness": 60}
    assert "focus" in stored, "defaults are kept when the file is first created"
    assert presets.delete("reading") and not presets.delete("reading")


def test_make_state_rejects_off_with_values():
    with pytest.raises(InvalidState):
        make_state(on=False, brightness=5)


# --- timers & CLI -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90", 90), ("45s", 45), ("10m", 600), ("10min", 600), ("1h30m", 5400), ("1.5h", 5400)],
)
def test_parse_duration(text, seconds):
    assert timers.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "10x", "0", "30d"])
def test_parse_duration_rejects(text):
    with pytest.raises(ValueError):
        timers.parse_duration(text)


def test_command_json_round_trip():
    cmd = Command("set", "bed,setup", state=LightState(on=True, rgb=(1, 2, 3), brightness=40))
    assert Command.from_json(cmd.to_json()) == cmd


def test_sleep_validates_before_scheduling(three_lights, monkeypatch, capsys):
    scheduled = []
    monkeypatch.setattr(
        timers, "schedule", lambda s, c: scheduled.append((s, c)) or timers.Timer("ab12", 0, c)
    )
    assert cli.main(["bed", "sleep", "30m", "night"]) == 0
    assert scheduled == [(1800, Command("preset", "bed", preset="night"))]
    assert cli.main(["sleep", "10m"]) == 0
    assert scheduled[-1] == (600, Command("off"))
    assert cli.main(["sleep", "10m", "nigth"]) == 2
    assert cli.main(["sleep", "soon"]) == 2
    assert len(scheduled) == 2


def test_cli_parsing(three_lights, capsys):
    assert cli.main(["setup", "40", "10"]) == 0
    assert three_lights.dps["s1"]["22"] == 400 and three_lights.dps["s1"]["23"] == 100
    assert cli.main(["rgb", "300", "0", "0"]) == 0  # clamped with a warning
    assert "outside 0-255" in capsys.readouterr().out
    assert cli.main(["bed", "focus"]) == 0
    assert cli.main(["florp"]) == 2
    assert "is not a command, preset or light" in capsys.readouterr().err
    assert cli.main(["focsu"]) == 2
    assert "Did you mean: focus" in capsys.readouterr().err


def test_missing_devices_file_is_friendly(home, capsys):
    assert cli.main(["on"]) == 2
    assert "tuya-local-mcp init" in capsys.readouterr().err
