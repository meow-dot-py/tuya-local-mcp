"""Delayed commands ("turn off in 30m") that survive the terminal or Claude Code closing.

A timer is a small detached background process: it sleeps, then runs one
Command. The command is validated *before* the timer starts, so a typo fails
right away instead of silently 30 minutes later.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

from tuya_local_mcp.commands import Command

HIDDEN_COMMAND = "_run-later"
MAX_DELAY = 7 * 24 * 3600

# Longest unit spellings first, so "10min" is not read as "10m" + "in".
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(hours?|hrs|hr|h|minutes?|mins|min|m|seconds?|secs|sec|s)?")
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    """'90' / '90s' / '10m' / '1h30m' / '1.5h' -> seconds. Raises ValueError."""
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned:
        raise ValueError("empty duration")
    total = 0.0
    pos = 0
    while pos < len(cleaned):
        match = _DURATION.match(cleaned, pos)
        if not match or match.end() == pos:
            raise ValueError(f"'{text}' is not a duration (try 90, 45s, 10m or 1h30m)")
        number, unit = match.groups()
        total += float(number) * _UNIT_SECONDS[(unit or "s")[0]]
        pos = match.end()
    seconds = round(total)
    if not 1 <= seconds <= MAX_DELAY:
        raise ValueError("a timer must be between 1 second and 7 days")
    return seconds


def format_duration(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return "".join(f"{v}{u}" for v, u in ((h, "h"), (m, "m"), (s, "s")) if v) or "0s"


def schedule(seconds: int, cmd: Command) -> None:
    """Start a detached process that runs `cmd` after `seconds`."""
    spawn_detached([HIDDEN_COMMAND, str(seconds), cmd.to_json()])


def spawn_detached(cli_args: list[str]) -> None:
    """Run `python -m tuya_local_mcp <cli_args>` in a process that outlives its parent.

    Used for timers and for hook work: Claude Code may end a hook (or the whole
    session) at any moment, and a half-finished light change must not be left behind.
    """
    args = [sys.executable, "-m", "tuya_local_mcp", *cli_args]
    options: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform != "win32":
        # Own session: closing the terminal (SIGHUP) does not kill it.
        subprocess.Popen(args, start_new_session=True, **options)
        return
    # No console window, Ctrl+C in the parent does not reach it, and (when the parent's
    # job object allows it) it leaves that job, so killing the parent's tree spares it.
    base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
    except OSError:
        subprocess.Popen(args, creationflags=base, **options)


def run_later(argv: list[str]) -> int:
    """Entry point of the background process: `_run-later <seconds> <command json>`."""
    from tuya_local_mcp.commands import execute

    seconds, payload = int(argv[0]), argv[1]
    cmd = Command.from_json(payload)
    time.sleep(seconds)
    return 0 if execute(cmd).ok else 1
