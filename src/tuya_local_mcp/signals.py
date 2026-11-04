"""Build/test signals and Claude Code hook handling.

The flow, driven by Claude Code hooks (see plugin/hooks/hooks.json):

    a test/build command fails  ->  remember how the lights were, turn them red
    it passes again             ->  flash green, then put the lights back as they were
    it passes, nothing failed   ->  nothing happens (no flicker on every green run)

What counts as a test/build command is a regular expression (DEFAULT_COMMANDS),
which you can replace. Settings come from the plugin's options (shown in Claude
Code's /config) or from environment variables, in this order:

    CLAUDE_PLUGIN_OPTION_SIGNAL_TARGET   or  TUYA_LOCAL_MCP_SIGNAL_TARGET    default "all"
    CLAUDE_PLUGIN_OPTION_SIGNAL_COMMANDS or  TUYA_LOCAL_MCP_SIGNAL_COMMANDS  default below
    CLAUDE_PLUGIN_OPTION_SIGNALS         or  TUYA_LOCAL_MCP_SIGNALS          default true
    CLAUDE_PLUGIN_OPTION_AMBIENT         or  TUYA_LOCAL_MCP_AMBIENT          default false

Hooks must never disturb a Claude Code session: every error is swallowed and
written to hook.log in the config folder instead.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from typing import Any

from tuya_local_mcp import config

RED = (255, 0, 0)
GREEN = (0, 255, 80)
GREEN_SECONDS = 4
STALE_AFTER = 3 * 3600  # a red light older than this is not "restored" any more

DEFAULT_COMMANDS = (
    r"\b(pytest|py\.test|tox|nox|unittest|jest|vitest|mocha|playwright|cypress"
    r"|(npm|pnpm|yarn|bun)( run)? (test|build|typecheck)"
    r"|cargo (test|build|check|nextest)|go (test|build|vet)"
    r"|make|cmake --build|ninja|gradlew?|mvn|dotnet (test|build)"
    r"|tsc|mypy|pyright|swift (test|build)|xcodebuild|docker (compose )?build|deploy)\b"
)


def setting(name: str, default: str) -> str:
    for key in (f"CLAUDE_PLUGIN_OPTION_{name}", f"TUYA_LOCAL_MCP_{name}"):
        value = os.environ.get(key)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def flag(name: str, default: bool) -> bool:
    return setting(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def is_signal_command(command: str) -> bool:
    try:
        pattern = re.compile(setting("SIGNAL_COMMANDS", DEFAULT_COMMANDS))
    except re.error:
        pattern = re.compile(DEFAULT_COMMANDS)
    return bool(pattern.search(command))


# --- red / green ------------------------------------------------------------------


def _state_path():
    return config.home_dir() / "signal-state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = config.read_json(path)
        return data if isinstance(data, dict) else {}
    except config.ConfigError:
        return {}


def signal_fail(target: str | None = None) -> str:
    """Turn the target red, remembering what it looked like (only on the first failure)."""
    from tuya_local_mcp import devices, engine
    from tuya_local_mcp.state import LightState

    target = target or setting("SIGNAL_TARGET", "all")
    state = _load_state()
    if state.get("failing") and time.time() - state.get("since", 0) < STALE_AFTER:
        return "already red"

    lights = devices.resolve(devices.load_lights(), target)
    before = engine.run([(li, engine.read_job) for li in lights])
    saved = {o.light.id: o.state.to_dict() for o in before if o.ok and o.state is not None}
    config.write_json_atomic(
        _state_path(),
        {"failing": True, "since": time.time(), "target": target, "saved": saved},
    )
    red = LightState(on=True, rgb=RED, brightness=100)
    engine.run([(o.light, engine.write_job(red)) for o in before if o.ok], repair=False)
    return f"red ({target})"


def signal_pass(green_seconds: float = GREEN_SECONDS) -> str:
    """After a failure: flash green, then restore the lights as they were before the red."""
    from tuya_local_mcp import devices, engine
    from tuya_local_mcp.state import LightState

    state = _load_state()
    if not state.get("failing"):
        return "nothing to restore"
    _state_path().unlink(missing_ok=True)
    if time.time() - state.get("since", 0) > STALE_AFTER:
        return "red state was stale; left the lights alone"

    by_id = {li.id: li for li in devices.load_lights()}
    saved: dict[str, dict[str, Any]] = state.get("saved") or {}
    lights = [by_id[i] for i in saved if i in by_id]

    green = LightState(on=True, rgb=GREEN, brightness=100)
    engine.run([(li, engine.write_job(green)) for li in lights], repair=False)
    time.sleep(green_seconds)
    tasks = [(li, engine.write_job(_restorable(saved[li.id]))) for li in lights]
    engine.run(tasks)
    return f"restored {len(tasks)} light(s)"


def _restorable(data: dict[str, Any]):
    """Turn a saved status back into something we can send (scenes become plain light)."""
    from tuya_local_mcp.state import LightState

    if data.get("on") is False:
        return LightState(on=False)
    rgb = data.get("rgb")
    if isinstance(rgb, list) and len(rgb) == 3:
        return LightState(on=True, rgb=(rgb[0], rgb[1], rgb[2]), brightness=data.get("brightness"))
    return LightState(on=True, brightness=data.get("brightness"), temp=data.get("temp"))


# --- ambient mode ---------------------------------------------------------------------


def ambient_context() -> str:
    """Instructions added to each Claude Code session when ambient mode is on."""
    from tuya_local_mcp import devices, presets

    try:
        targets = ", ".join(devices.target_names(devices.load_lights()))
    except config.ConfigError:
        return ""
    try:
        names = ", ".join(presets.load()[0])
    except config.ConfigError:
        names = "code, chill, night, focus"
    now = datetime.now().strftime("%H:%M")
    return f"""\
# Room lights (tuya-local-mcp ambient mode)
You can set the mood of the user's room with the tuya-local-mcp `set_light` tool,
without being asked. Local time at session start: {now}. Targets: {targets}.
Presets: {names}.

- Long debugging or focused work: preset `focus` (or `code`).
- Creative or playful work: `cyber` or `gaming`.
- Late at night (after about 22:00): `chill`, later `night`.
- Change the mood at most once per task, and only when the kind of work clearly
  changes. Never undo a light change the user made or asked for.
- Red/green build and test signals are automatic (hooks): do not do them yourself.
- Call the tool in parallel with your other work, with no commentary. At most add a
  short tag at the end of your reply, like `[LIGHT: focus]`.
"""


# --- hook entry point ------------------------------------------------------------------

WORKER_COMMAND = "_signal"


def _spawn(cli_args: list[str]) -> None:
    from tuya_local_mcp.timers import spawn_detached

    spawn_detached(cli_args)


def run_worker(argv: list[str]) -> int:
    """Entry point of the detached process: `_signal fail|pass`."""
    try:
        result = signal_fail() if argv[:1] == ["fail"] else signal_pass()
        _log(f"  {argv[:1]} -> {result}")
    except Exception:
        _log(traceback.format_exc())
    return 0


def handle_hook(raw: str) -> int:
    """Entry point for `tuya-local-mcp hook`: reads the hook event JSON from stdin."""
    try:
        event = json.loads(raw) if raw.strip() else {}
        name = event.get("hook_event_name")

        if name == "SessionStart":
            if flag("AMBIENT", False):
                context = ambient_context()
                if context:
                    out = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                  "additionalContext": context}}  # fmt: skip
                    print(json.dumps(out))
            return 0

        if name not in ("PostToolUse", "PostToolUseFailure") or event.get("tool_name") != "Bash":
            return 0
        if not flag("SIGNALS", True):
            return 0
        tool_input = event.get("tool_input") or {}
        command = str(tool_input.get("command") or "")
        if tool_input.get("run_in_background") or not is_signal_command(command):
            return 0

        if name == "PostToolUseFailure" and event.get("is_interrupt"):
            return 0
        kind = "fail" if name == "PostToolUseFailure" else "pass"
        if kind == "pass" and not _load_state().get("failing"):
            return 0  # the common case: nothing failed before, nothing to do
        _log(f"{kind}: {command[:80]}")
        # Change the lights in a separate process: the hook may be ended at any time.
        _spawn([WORKER_COMMAND, kind])
    except Exception:
        _log(traceback.format_exc())
    return 0


def _log(message: str) -> None:
    """Keep a small log next to the config, so hook problems can be diagnosed."""
    try:
        path = config.home_dir() / "hook.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 100_000:
            path.unlink()
        stamp = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {message.rstrip()}\n")
    except OSError:
        print(message, file=sys.stderr)
