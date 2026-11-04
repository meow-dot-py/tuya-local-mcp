"""The `tuya-local-mcp` command (alias it to `l` or `light`).

    l                      toggle all lights (any on -> all off, else all on)
    l [target] on|off|toggle
    l [target] <preset>                   e.g. l focus, l bed night
    l [target] <brightness>% [temp]       e.g. l 40, l setup 85 90
    l [target] rgb <r> <g> <b> [brightness]
    l [target] sleep <duration> [command] e.g. l sleep 30m, l bed sleep 1h night
    l [target] status
    l presets | lights | scan | init | serve | help

With no arguments and no terminal attached (that is, when Claude Code starts it),
the program runs as an MCP server instead. `serve` forces that.
"""

from __future__ import annotations

import difflib
import subprocess
import sys

from tuya_local_mcp import __version__, config, devices, presets, render, timers
from tuya_local_mcp.commands import Command, CommandError, execute, plan_preset
from tuya_local_mcp.devices import Light, UnknownTarget
from tuya_local_mcp.presets import Preset, PresetError
from tuya_local_mcp.state import (
    BRIGHTNESS_RANGE,
    CHANNEL_RANGE,
    TEMP_RANGE,
    InvalidState,
    LightState,
    clamp,
)

USAGE = """\
Usage: l [target] <command>

  (nothing)                    toggle: any light on -> all off, otherwise all on
  on | off | toggle            power
  <preset>                     apply a preset or scene, e.g. `l focus`, `l bed night`
  <brightness>[%] [temp]       white light, e.g. `l 40` or `l 85 90` (temp 0 warm - 100 cool)
  rgb <r> <g> <b> [bright]     colour, e.g. `l rgb 255 0 128`
  sleep <duration> [command]   run later: `l sleep 30m` (off), `l bed sleep 1h night`
  status                       what each light is doing right now

  presets                      list presets with a colour preview
  lights                       list your lights and the names you can target
  scan                         find lights whose IP changed (automatic on errors too)
  init                         one-time: fetch your bulbs' local keys with the tinytuya wizard
  serve                        run the MCP server for Claude Code
  signal fail|pass [target]    try the build/test signal: red, then green and restore
  hook                         Claude Code hook entry point (reads the event from stdin)

Targets: all (default), a light's zone or name, or a word of its name.
Combine with commas: `l bed,setup off`. Config folder: {home}
"""

POWER_WORDS = {"on", "off", "toggle", "t"}
ACTION_WORDS = POWER_WORDS | {"status", "rgb", "sleep"}
GLOBAL_WORDS = {"presets", "lights", "scan", "init", "serve", "help", "version", "signal", "hook"}


class UsageError(ValueError):
    pass


def entry() -> None:
    sys.exit(main(sys.argv[1:]))


def main(argv: list[str]) -> int:
    if not argv:
        # A person at a terminal wants the toggle; Claude Code (stdin is a pipe) wants MCP.
        if not sys.stdin.isatty():
            return serve()
        argv = ["toggle"]

    _utf8_output()
    first = argv[0].lower()
    if first == timers.HIDDEN_COMMAND:
        return timers.run_later(argv[1:])
    if first == "_signal":
        from tuya_local_mcp.signals import run_worker

        return run_worker(argv[1:])
    if first in ("serve", "--serve", "mcp"):
        return serve()
    if first in ("help", "-h", "--help"):
        print(USAGE.format(home=config.home_dir()))
        return 0
    if first in ("version", "--version", "-v"):
        print(f"tuya-local-mcp {__version__}")
        return 0
    if first == "hook":
        from tuya_local_mcp.signals import handle_hook

        return handle_hook(sys.stdin.read())

    try:
        if first == "init":
            return init()
        if first == "presets":
            return list_presets()
        if first == "signal":
            return signal(argv[1:])
        return run(argv)
    except (UsageError, config.ConfigError, UnknownTarget, CommandError, PresetError,
            InvalidState) as e:  # fmt: skip
        print(render.error(str(e)), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def run(argv: list[str]) -> int:
    lights = devices.load_lights()
    available, _ = presets.load()
    target, words = split_target(argv, lights, available)
    word = words[0].lower() if words else "toggle"

    if word == "lights":
        return list_lights(lights)
    if word == "scan":
        return scan()
    if word == "sleep":
        return schedule(words[1:], target, lights, available)

    cmd = parse_action(words or ["toggle"], target, available)
    report = execute(cmd, lights)
    print(render.status(report) if cmd.action == "status" else render.report(report))
    return 0 if report.ok else 1


def split_target(
    argv: list[str], lights: list[Light], available: dict[str, Preset]
) -> tuple[str, list[str]]:
    """Separate an optional leading target from the command words."""
    first = argv[0].lower()
    known_word = first in ACTION_WORDS or first in GLOBAL_WORDS or first in available
    if known_word or _is_number(first):
        # A light may share its name with a preset: it is a target only if a command follows.
        if (
            len(argv) > 1
            and first not in ACTION_WORDS | GLOBAL_WORDS
            and devices.is_target(lights, first)
        ):
            return first, argv[1:]
        return "all", argv
    if devices.is_target(lights, first):
        return first, argv[1:]

    vocabulary = sorted(
        ACTION_WORDS | GLOBAL_WORDS | set(available) | set(devices.target_names(lights))
    )
    close = difflib.get_close_matches(first, vocabulary, 3)
    hint = f" Did you mean: {', '.join(close)}?" if close else " Run `l help` for usage."
    raise UsageError(f"'{argv[0]}' is not a command, preset or light.{hint}")


def parse_action(words: list[str], target: str, available: dict[str, Preset]) -> Command:
    """Turn the command words (after the target) into a Command. Clamps numbers with a warning."""
    word, rest = words[0].lower(), words[1:]

    if word in POWER_WORDS or word == "status":
        _no_extra(word, rest)
        action = "toggle" if word == "t" else word
        return Command(action, target)  # type: ignore[arg-type]

    if word == "rgb":
        if len(rest) not in (3, 4) or not all(_is_number(w) for w in rest):
            raise UsageError("Usage: l [target] rgb <r> <g> <b> [brightness], e.g. l rgb 255 0 128")
        r, g, b = (_clamped(f"rgb {c}", rest[i], CHANNEL_RANGE) for i, c in enumerate("rgb"))
        bri = _clamped("brightness", rest[3], BRIGHTNESS_RANGE) if len(rest) == 4 else None
        return Command("set", target, state=LightState(on=True, brightness=bri, rgb=(r, g, b)))

    if _is_number(word):
        if len(rest) > 1 or (rest and not _is_number(rest[0])):
            raise UsageError("Usage: l [target] <brightness> [temp], e.g. l 40 or l setup 85 90")
        bri = _clamped("brightness", word, BRIGHTNESS_RANGE)
        temp = _clamped("temp", rest[0], TEMP_RANGE) if rest else None
        return Command("set", target, state=LightState(on=True, brightness=bri, temp=temp))

    if word in available:
        _no_extra(word, rest)
        return Command("preset", target, preset=word)

    raise UsageError(f"'{words[0]}' is not a command or preset. Run `l help` for usage.")


def schedule(
    words: list[str], target: str, lights: list[Light], available: dict[str, Preset]
) -> int:
    if not words:
        raise UsageError("Usage: l [target] sleep <duration> [command], e.g. l sleep 30m")
    try:
        seconds = timers.parse_duration(words[0])
    except ValueError as e:
        raise UsageError(str(e)) from None
    inner = words[1:] or ["off"]
    if inner[0].lower() in GLOBAL_WORDS | {"sleep"}:
        raise UsageError(f"'{inner[0]}' cannot be scheduled.")

    cmd = parse_action(inner, target, available)
    # Validate now so a typo fails immediately, not silently when the timer fires.
    devices.resolve(lights, target)
    if cmd.action == "preset":
        plan_preset(available[cmd.preset or ""], lights, target)

    timers.schedule(seconds, cmd)
    what = " ".join(inner)
    print(f"{render.tag('TIMER', (230, 180, 80))} in {timers.format_duration(seconds)}: "
          f"{what} {render.dim(f'({target})')}")  # fmt: skip
    return 0


def list_lights(lights: list[Light]) -> int:
    for li in lights:
        zone = f" zone={li.zone}" if li.zone else ""
        names = ", ".join(sorted(li.aliases))
        print(f"{render.paint('●', render.CYAN)} {li.name}{zone}  {render.dim(li.ip or 'no IP')}"
              f"  {render.dim('targets: ' + names)}")  # fmt: skip
    return 0


def list_presets() -> int:
    available, warnings = presets.load()
    for w in warnings:
        print(render.warning(w))
    for p in available.values():
        print(render.preset_line(p))
    if not config.presets_path().exists():
        print(render.dim(f"(built-in defaults; your own go in {config.presets_path()})"))
    return 0


def scan() -> int:
    print(render.dim("Listening for Tuya broadcasts on the LAN..."))
    result = devices.repair_ips()
    tag = render.tag("SCAN", (80, 200, 255))
    if result.changed:
        moved = ", ".join(f"{n} -> {ip}" for n, ip in result.changed.items())
        print(f"{tag} updated: {moved}")
    else:
        print(f"{tag} {render.dim('all IPs up to date')}")
    if result.missing:
        print(render.warning(f"not found: {', '.join(result.missing)} (off at the wall switch?)"))
    return 0 if not result.missing else 1


def init() -> int:
    home = config.home_dir()
    home.mkdir(parents=True, exist_ok=True)
    print(f"""\
{render.tag("INIT", render.CYAN)} One-time setup: get the local key of each bulb.

  You need a free Tuya IoT developer account linked to your Smart Life app
  (step-by-step guide in the README). The tinytuya wizard will ask for:

    API Key / API Secret   the project's Access ID / Access Secret
    any Device ID          one of your bulbs' IDs from the Tuya project
    Your Region            eu, us, us-e, in or cn (your data center)
    DP Name mappings?      Y   (lets each change go out as one message)
    Poll local devices?    Y   (finds the IPs; force-close the Smart Life app first)

  Files are saved in {home}
  They contain secrets: never commit or share them.
""")
    code = subprocess.call([sys.executable, "-m", "tinytuya", "wizard"], cwd=home)
    if code != 0:
        print(render.error("The wizard did not finish."), file=sys.stderr)
        return code
    lights = devices.load_lights()
    print(f"\n{render.tag('READY', (80, 250, 123))} {len(lights)} device(s) configured:")
    return list_lights(lights)


def signal(args: list[str]) -> int:
    from tuya_local_mcp import signals

    kind = args[0].lower() if args else ""
    if kind not in ("fail", "pass") or len(args) > 2:
        raise UsageError("Usage: l signal fail [target]  |  l signal pass")
    if kind == "fail":
        result = signals.signal_fail(args[1] if len(args) > 1 else None)
        print(f"{render.tag('SIGNAL', render.RED)} {result}")
    else:
        result = signals.signal_pass()
        print(f"{render.tag('SIGNAL', (0, 255, 80))} {result}")
    return 0


def serve() -> int:
    from tuya_local_mcp.server import run_server  # imports the MCP SDK only when needed

    run_server()
    return 0


def _utf8_output() -> None:
    """Print symbols like ● and · safely even when output is piped on Windows (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def _is_number(text: str) -> bool:
    return text.rstrip("%").lstrip("-").isdigit()


def _clamped(name: str, text: str, bounds: tuple[int, int]) -> int:
    value = int(text.rstrip("%"))
    fixed = clamp(value, *bounds)
    if fixed != value:
        print(render.warning(f"{name} {value} is outside {bounds[0]}-{bounds[1]}, using {fixed}."))
    return fixed


def _no_extra(word: str, rest: list[str]) -> None:
    if rest:
        raise UsageError(f"'{word}' takes no arguments (got: {' '.join(rest)}).")
