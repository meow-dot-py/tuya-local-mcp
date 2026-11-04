"""TrueColor terminal output: tags painted in the colour the bulb actually shows.

Colours are turned off automatically when output is not a terminal (pipes,
hooks, logs) or when NO_COLOR is set (https://no-color.org). FORCE_COLOR=1
turns them back on.
"""

from __future__ import annotations

import os
import sys

from tuya_local_mcp.commands import Report, preset_color
from tuya_local_mcp.engine import Outcome
from tuya_local_mcp.presets import Preset
from tuya_local_mcp.state import RGB, LightState, describe, display_rgb

RED: RGB = (255, 85, 85)
ORANGE: RGB = (255, 184, 108)
GREY: RGB = (110, 118, 129)
CYAN: RGB = (139, 233, 253)


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _enable_windows_ansi() -> None:
    """Old Windows consoles need ANSI escape processing switched on explicitly."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


COLOR = _color_enabled()
if COLOR:
    _enable_windows_ansi()


def paint(text: str, rgb: RGB, *, bold: bool = False) -> str:
    if not COLOR:
        return text
    weight = "\033[1m" if bold else ""
    return f"{weight}\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"


def dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if COLOR else text


def tag(label: str, rgb: RGB) -> str:
    return paint(f"[{label}]", rgb, bold=True)


def swatch(rgb: RGB) -> str:
    return paint("██", rgb) if COLOR else ""


def error(message: str) -> str:
    return f"{tag('ERROR', RED)} {message}"


def warning(message: str) -> str:
    return f"{tag('WARN', ORANGE)} {message}"


def _outcome(o: Outcome) -> str:
    if not o.ok:
        return f"{o.light.label}: {paint('✗ ' + (o.error or 'failed'), RED)}"
    state = o.state or LightState()
    if state.on is False:
        return f"{o.light.label}: {dim('off')}"
    return f"{o.light.label}: {paint(describe(state), display_rgb(state))}"


def report(r: Report) -> str:
    """One line like the old CLI: '[FOCUS] setup: 85% · temp 90 | bed: off  (48 ms)'."""
    lines = [warning(w) for w in r.warnings]
    body = " | ".join(_outcome(o) for o in r.outcomes)
    if r.skipped:
        body += dim(f"  (no light for: {', '.join(r.skipped)})")
    lines.append(f"{tag(r.title, r.color)} {body}{dim(f'  {r.elapsed_ms} ms')}")
    return "\n".join(lines)


def status(r: Report) -> str:
    """One line per light: '● Desk Lamp (setup)  on · 85% · temp 90  192.168.1.50'."""
    lines = []
    for o in r.outcomes:
        name = o.light.name if not o.light.zone else f"{o.light.name} ({o.light.zone})"
        if not o.ok:
            lines.append(f"{paint('✗', RED)} {name}  {paint(o.error or 'failed', RED)}")
            continue
        state = o.state or LightState()
        dot = paint("●", display_rgb(state)) if state.on else paint("○", GREY)
        lines.append(
            f"{dot} {name}  {paint(describe(state), display_rgb(state))}  {dim(o.light.ip)}"
        )
    lines.append(dim(f"{r.elapsed_ms} ms"))
    return "\n".join(lines)


def preset_line(p: Preset) -> str:
    color = preset_color(p)
    if p.state is not None:
        detail = paint(describe(p.state), display_rgb(p.state))
    else:
        detail = "  ".join(
            f"{t}: {paint(describe(s), display_rgb(s))}" for t, s in (p.targets or {}).items()
        )
    desc = f"  {dim(p.description)}" if p.description else ""
    return f"{swatch(color)} {tag(p.name.upper(), color)} {detail}{desc}"
