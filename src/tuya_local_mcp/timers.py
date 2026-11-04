"""Delayed commands ("turn off in 30m") that survive the terminal or Claude Code closing.

A timer is a small detached background process plus a file describing it in
<config>/timers/<id>.json. The process checks every couple of seconds that its file
still exists; cancelling a timer just deletes the file, and the process quietly
exits. No process killing needed, which also works where that is not allowed.

The command is validated *before* the timer starts (by the callers), so a typo fails
right away instead of silently 30 minutes later.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tuya_local_mcp import config
from tuya_local_mcp.commands import Command
from tuya_local_mcp.state import describe

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


POLL_SECONDS = 2  # how quickly a cancelled timer notices
STALE_AFTER = 300  # a timer file this long past its time belongs to a process that died


@dataclass(frozen=True)
class Timer:
    id: str
    due: float  # Unix time
    command: Command

    @property
    def remaining(self) -> int:
        return max(0, round(self.due - time.time()))

    @property
    def what(self) -> str:
        cmd = self.command
        if cmd.preset:
            return cmd.preset
        return describe(cmd.state) if cmd.state is not None else cmd.action

    def summary(self) -> str:
        at = datetime.fromtimestamp(self.due).strftime("%H:%M:%S")
        left = format_duration(self.remaining)
        return f"{self.id}: {self.what} ({self.command.target}) at {at}, in {left}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "what": self.what,
            "target": self.command.target,
            "at": datetime.fromtimestamp(self.due).strftime("%H:%M:%S"),
            "in": format_duration(self.remaining),
        }


def timers_dir() -> Path:
    return config.home_dir() / "timers"


def schedule(seconds: int, cmd: Command) -> Timer:
    """Register a timer and start the detached process that will run `cmd`."""
    folder = timers_dir()
    timer_id = secrets.token_hex(2)
    while (folder / f"{timer_id}.json").exists():
        timer_id = secrets.token_hex(2)
    timer = Timer(timer_id, time.time() + seconds, cmd)
    config.write_json_atomic(
        folder / f"{timer_id}.json",
        {"id": timer_id, "due": timer.due, "command": json.loads(cmd.to_json())},
    )
    spawn_detached([HIDDEN_COMMAND, timer_id])
    return timer


def _load(path: Path) -> Timer | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Timer(
            str(data["id"]), float(data["due"]), Command.from_json(json.dumps(data["command"]))
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_timers() -> list[Timer]:
    """Pending timers, soonest first. Leftovers of crashed timers are cleaned up."""
    folder = timers_dir()
    if not folder.exists():
        return []
    timers = []
    for path in folder.glob("*.json"):
        timer = _load(path)
        if timer is None or timer.due < time.time() - STALE_AFTER:
            path.unlink(missing_ok=True)
            continue
        timers.append(timer)
    return sorted(timers, key=lambda t: t.due)


def cancel(timer_id: str | None = None) -> list[Timer]:
    """Cancel one timer by id, or all of them (None or "all"). Returns what was cancelled."""
    pending = list_timers()
    if timer_id and timer_id.strip().lower() != "all":
        wanted = timer_id.strip().lower()
        pending = [t for t in pending if t.id == wanted]
        if not pending:
            ids = ", ".join(t.id for t in list_timers()) or "none pending"
            raise ValueError(f"No timer '{timer_id}'. Pending: {ids}.")
    for timer in pending:
        (timers_dir() / f"{timer.id}.json").unlink(missing_ok=True)
    return pending


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
    """Entry point of the background process: `_run-later <timer id>`."""
    from tuya_local_mcp.commands import execute

    path = timers_dir() / f"{argv[0]}.json"
    timer = _load(path)
    if timer is None:
        return 1
    while True:
        if not path.exists():
            return 0  # cancelled
        left = timer.due - time.time()
        if left <= 0:
            break
        time.sleep(min(POLL_SECONDS, left))
    path.unlink(missing_ok=True)
    return 0 if execute(timer.command).ok else 1
