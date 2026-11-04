"""Translating LightState to Tuya data points (DPs) and back.

A Tuya bulb exposes numbered "data points": e.g. on most modern bulbs DP 20 is
power, 21 the mode, 22 brightness, 23 white temperature and 24 colour. Older
bulbs use DPs 1-5 with different value ranges. The wizard's devices.json includes
a "mapping" that names every DP (switch_led, bright_value_v2, ...), so we can
build the whole change up front and send it in ONE message: no status read, no
flicker from applying colour and brightness in separate steps.

If a device has no mapping, the engine asks tinytuya to detect the bulb type
once (one extra round trip) and builds the Profile from that.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Any

from tuya_local_mcp import _tinytuya
from tuya_local_mcp.state import LightState


class Unsupported(ValueError):
    """The light cannot do what was asked (e.g. colour on a white-only bulb)."""


@dataclass(frozen=True)
class Profile:
    """Which DP controls what on one light, and the raw value ranges it uses."""

    switch: str
    mode: str | None = None
    brightness: str | None = None
    brightness_max: int = 1000
    brightness_min: int = 10
    temp: str | None = None
    temp_max: int = 1000
    colour: str | None = None
    colour_format: str = "hsv16"  # "hsv16" (modern) or "rgb8" (older bulbs)


_SWITCH_CODES = ("switch_led", "led_switch", "switch", "switch_1")
_MODE_CODES = ("work_mode",)
_BRIGHTNESS_CODES = ("bright_value_v2", "bright_value")
_TEMP_CODES = ("temp_value_v2", "temp_value")
_COLOUR_CODES = ("colour_data_v2", "colour_data")


def profile_from_mapping(mapping: dict[str, Any]) -> Profile | None:
    """Build a Profile from the wizard's DP mapping. None if it is not usable."""
    by_code: dict[str, tuple[str, dict[str, Any]]] = {}
    for dp, spec in mapping.items():
        if isinstance(spec, dict) and spec.get("code"):
            values = spec.get("values") if isinstance(spec.get("values"), dict) else {}
            by_code[str(spec["code"])] = (str(dp), values)

    def first(codes: tuple[str, ...]) -> tuple[str | None, dict[str, Any], str | None]:
        for code in codes:
            if code in by_code:
                return by_code[code][0], by_code[code][1], code
        return None, {}, None

    switch, _, _ = first(_SWITCH_CODES)
    if switch is None:
        return None
    mode, _, _ = first(_MODE_CODES)
    bright, bright_vals, _ = first(_BRIGHTNESS_CODES)
    temp, temp_vals, _ = first(_TEMP_CODES)
    colour, _, colour_code = first(_COLOUR_CODES)
    legacy = colour_code == "colour_data" or (bright is not None and int(bright) < 20)

    return Profile(
        switch=switch,
        mode=mode,
        brightness=bright,
        brightness_max=int(bright_vals.get("max", 255 if legacy else 1000)),
        brightness_min=int(bright_vals.get("min", 25 if legacy else 10)),
        temp=temp,
        temp_max=int(temp_vals.get("max", 255 if legacy else 1000)),
        colour=colour,
        colour_format="rgb8" if colour_code == "colour_data" else "hsv16",
    )


def profile_from_dpset(dpset: dict[str, Any]) -> Profile | None:
    """Build a Profile from tinytuya's own bulb detection (BulbDevice.dpset)."""
    if not dpset.get("switch"):
        return None
    vmax = int(dpset.get("value_max") or 1000)
    vmin = int(dpset.get("value_min") or 10)
    return Profile(
        switch=str(dpset["switch"]),
        mode=_opt(dpset.get("mode")),
        brightness=_opt(dpset.get("brightness")),
        brightness_max=vmax,
        brightness_min=vmin,
        temp=_opt(dpset.get("colourtemp")),
        temp_max=vmax,
        colour=_opt(dpset.get("colour")),
        colour_format=str(dpset.get("value_hexformat") or "hsv16"),
    )


def _opt(value: Any) -> str | None:
    return str(value) if value else None


# --- encoding -------------------------------------------------------------------


def needs_current_state(profile: Profile, desired: LightState) -> bool:
    """Only "change the brightness, keep everything else" needs to know the current mode.

    In white mode brightness is its own DP; in colour mode it is the V of the colour.
    """
    return (
        desired.on is not False
        and desired.brightness is not None
        and desired.rgb is None
        and desired.temp is None
        and profile.colour is not None
    )


def encode(
    profile: Profile, desired: LightState, current: LightState | None = None
) -> dict[str, Any]:
    """Build the DP values that make the light match `desired`, as one message."""
    if desired.on is False:
        return {profile.switch: False}

    dps: dict[str, Any] = {profile.switch: True}
    keep_colour = (
        desired.rgb is None
        and desired.temp is None
        and desired.brightness is not None
        and current is not None
        and current.mode == "colour"
        and current.rgb is not None
    )

    if desired.rgb is not None or keep_colour:
        if profile.colour is None:
            raise Unsupported("this light has no colour mode")
        rgb = desired.rgb if desired.rgb is not None else current.rgb  # type: ignore[union-attr]
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        if desired.brightness is not None:
            v = desired.brightness / 100
        dps[profile.colour] = _colour_hex(h, s, max(v, 0.01), profile.colour_format)
        if profile.mode:
            dps[profile.mode] = "colour"
        return dps

    if desired.temp is not None or desired.brightness is not None:
        if profile.mode:
            dps[profile.mode] = "white"
        if desired.brightness is not None and profile.brightness:
            dps[profile.brightness] = _scale_up(
                desired.brightness, profile.brightness_max, profile.brightness_min
            )
        if desired.temp is not None and profile.temp:
            dps[profile.temp] = _scale_up(desired.temp, profile.temp_max, 0)
    return dps


def _scale_up(percent: int, raw_max: int, raw_min: int) -> int:
    return max(raw_min, round(raw_max * percent / 100))


def _scale_down(raw: Any, raw_max: int) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw_max <= 0:
        return None
    return round(raw * 100 / raw_max)


def _colour_hex(h: float, s: float, v: float, fmt: str) -> str:
    BulbDevice = _tinytuya.load().BulbDevice

    return BulbDevice.hsv_to_hexvalue(min(h, 1.0), min(s, 1.0), min(v, 1.0), fmt)


# --- decoding -------------------------------------------------------------------


def decode(profile: Profile, dps: dict[str, Any]) -> LightState:
    """Turn a status() DP dict into a LightState with human scales."""
    on = dps.get(profile.switch)
    mode = dps.get(profile.mode) if profile.mode else None
    brightness = temp = None
    rgb = None

    if mode == "colour" and profile.colour and isinstance(dps.get(profile.colour), str):
        BulbDevice = _tinytuya.load().BulbDevice

        raw = dps[profile.colour]
        try:
            h, s, v = BulbDevice.hexvalue_to_hsv(raw, profile.colour_format)
            r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)  # the hue at full value
            rgb = (round(r * 255), round(g * 255), round(b * 255))
            brightness = max(1, round(v * 100))
        except ValueError:
            pass
    else:
        if profile.brightness:
            brightness = _scale_down(dps.get(profile.brightness), profile.brightness_max)
            if brightness is not None:
                brightness = max(1, min(100, brightness))
        if profile.temp:
            temp = _scale_down(dps.get(profile.temp), profile.temp_max)

    return LightState(
        on=on if isinstance(on, bool) else None,
        brightness=brightness,
        temp=temp,
        rgb=rgb,
        mode=mode if isinstance(mode, str) else ("white" if profile.mode is None else None),
    )
