"""Import tinytuya without its cloud client.

`import tinytuya` also imports tinytuya.Cloud, which pulls in `requests` and costs
~280 ms, more than half of tinytuya's import time. tuya-local-mcp never talks to
the cloud (the one-time wizard runs in its own process), so before the first
import we register a tiny stand-in for that one submodule. Every CLI command
starts that much faster.
"""

from __future__ import annotations

import sys
import types
from typing import Any


class _CloudDisabled:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "tuya-local-mcp loads tinytuya without its cloud client. "
            "Run `python -m tinytuya wizard` for cloud features."
        )


def load() -> types.ModuleType:
    if "tinytuya" not in sys.modules and "tinytuya.Cloud" not in sys.modules:
        stub = types.ModuleType("tinytuya.Cloud")
        stub.Cloud = _CloudDisabled  # type: ignore[attr-defined]
        sys.modules["tinytuya.Cloud"] = stub
    import tinytuya

    return tinytuya
