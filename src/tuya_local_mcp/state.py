"""LightState: one small description of a light, used everywhere.

The same type describes what we *want* a light to do (from the CLI, a preset or
Claude) and what a light *is* doing (decoded from its status). Values use human
scales, never Tuya's raw ones:

    on          True / False
    brightness  1-100 (%)
    temp        0-100 (%), 0 = warmest white, 100 = coolest white
    rgb         (r, g, b), each 0-255. Colour and white temperature are exclusive.
    mode        only when reading a light: "white", "colour", "scene" or "music"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RGB = tuple[int, int, int]

BRIGHTNESS_RANGE = (1, 100)
TEMP_RANGE = (0, 100)
CHANNEL_RANGE = (0, 255)


@dataclass(frozen=True)
class LightState:
    on: bool | None = None
    brightness: int | None = None
    temp: int | None = None
    rgb: RGB | None = None
    mode: str | None = None

    @property
    def is_off(self) -> bool:
        return self.on is False

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form, leaving out unset fields."""
        out: dict[str, Any] = {}
        if self.on is not None:
            out["on"] = self.on
        if self.mode is not None:
            out["mode"] = self.mode
        if self.brightness is not None:
            out["brightness"] = self.brightness
        if self.temp is not None:
            out["temp"] = self.temp
        if self.rgb is not None:
            out["rgb"] = list(self.rgb)
        return out


class InvalidState(ValueError):
    """User input that cannot become a LightState. The message is written for humans."""


def check_range(name: str, value: Any, lo: int, hi: int) -> int:
    """Strict check (presets and MCP): wrong values are errors, never silently changed."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidState(f"{name} must be a number between {lo} and {hi}, got {value!r}.")
    if not lo <= value <= hi:
        raise InvalidState(f"{name} must be between {lo} and {hi}, got {value}.")
    return int(value)


def clamp(value: int, lo: int, hi: int) -> int:
    """Lenient fix-up (CLI): the caller decides whether to warn about it."""
    return max(lo, min(hi, value))


def make_state(
    *,
    on: bool | None = True,
    brightness: Any = None,
    temp: Any = None,
    rgb: Any = None,
    where: str = "",
) -> LightState:
    """Build a validated LightState. `where` prefixes error messages, e.g. "preset 'focus'"."""
    prefix = f"{where}: " if where else ""
    try:
        if on is not None and not isinstance(on, bool):
            raise InvalidState(f"'on' must be true or false, got {on!r}.")
        if on is False:
            if any(v is not None for v in (brightness, temp, rgb)):
                raise InvalidState("a light that is off cannot also set brightness, temp or rgb.")
            return LightState(on=False)

        bri = (
            None if brightness is None else check_range("brightness", brightness, *BRIGHTNESS_RANGE)
        )
        tmp = None if temp is None else check_range("temp", temp, *TEMP_RANGE)
        col: RGB | None = None
        if rgb is not None:
            if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
                raise InvalidState(f"rgb must be three numbers like [255, 0, 128], got {rgb!r}.")
            channels = zip("rgb", rgb, strict=True)
            r, g, b = (check_range(f"rgb {ch}", v, *CHANNEL_RANGE) for ch, v in channels)
            col = (r, g, b)
        if col is not None and tmp is not None:
            raise InvalidState("use either rgb (colour) or temp (white), not both.")
        return LightState(on=on, brightness=bri, temp=tmp, rgb=col)
    except InvalidState as e:
        raise InvalidState(f"{prefix}{e}") from None


def describe(state: LightState) -> str:
    """Short human description, e.g. 'off', '85% · temp 90', 'rgb 255,0,128 · 70%'."""
    if state.on is False:
        return "off"
    parts: list[str] = []
    if state.rgb is not None:
        parts.append("rgb {},{},{}".format(*state.rgb))
    if state.brightness is not None:
        parts.append(f"{state.brightness}%")
    if state.temp is not None and state.rgb is None:
        parts.append(f"temp {state.temp}")
    if state.mode in ("scene", "music"):
        parts.append(state.mode)
    return " · ".join(parts) if parts else "on"


def display_rgb(state: LightState) -> RGB:
    """An on-screen colour that resembles what the bulb shows (used for terminal tags)."""
    if state.on is False:
        return (110, 118, 129)
    if state.rgb is not None:
        # Very dark colours would be unreadable in a terminal; lift them a bit.
        return (max(60, state.rgb[0]), max(60, state.rgb[1]), max(60, state.rgb[2]))
    base = temp_to_rgb(state.temp) if state.temp is not None else (255, 220, 150)
    if state.brightness is None:
        return base
    factor = 0.45 + 0.55 * (state.brightness / 100)
    return (int(base[0] * factor), int(base[1] * factor), int(base[2] * factor))


def temp_to_rgb(temp: int) -> RGB:
    """Approximate a white temperature on screen: 0 = warm amber, 100 = cool blue-white."""
    t = clamp(temp, *TEMP_RANGE) / 100
    return (int(255 - 55 * t), int(175 + 60 * t), int(75 + 180 * t))
