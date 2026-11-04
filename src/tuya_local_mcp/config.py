"""Where tuya-local-mcp keeps its files, and safe JSON reading/writing.

All user files live in one folder, outside the repository, so secrets can never
be committed by accident:

    $TUYA_LOCAL_MCP_HOME           if set
    ~/.config/tuya-local-mcp       otherwise (same path on Windows, macOS and Linux)

Inside it:

    devices.json   your bulbs and their local keys (written by `tuya-local-mcp init`)
    presets.json   your presets and scenes (created on the first save)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ENV_HOME = "TUYA_LOCAL_MCP_HOME"


class ConfigError(Exception):
    """A config file is missing or malformed. The message is written for humans."""


def home_dir() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "tuya-local-mcp"


def devices_path() -> Path:
    return home_dir() / "devices.json"


def presets_path() -> Path:
    return home_dir() / "presets.json"


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"{path} is not valid JSON (line {e.lineno}, column {e.colno}: {e.msg})."
        ) from e


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON so the file is never left half-written.

    devices.json holds the only local copy of your bulbs' keys, so a crash or two
    processes writing at once must not corrupt it. We write to a temporary file in
    the same folder and then swap it in with an atomic rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
