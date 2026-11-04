"""Talking to the bulbs: one connection per light, all lights in parallel.

Every light gets one persistent TCP connection (port 6668) for the whole command,
so a read followed by a write costs a single handshake. Failures are reported per
light, never hidden. If a light is unreachable we scan the LAN once, save any IP
that moved, and retry only the lights whose IP actually changed.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from tuya_local_mcp import _tinytuya, devices, protocol
from tuya_local_mcp.devices import Light
from tuya_local_mcp.state import LightState

CONNECT_TIMEOUT = 1.5  # seconds to open the TCP connection (a LAN bulb answers in ms)
SOCKET_TIMEOUT = 2.0  # seconds to wait for each reply
RETRIES = 1  # one attempt per connection; the LAN scan + retry covers the rest

# tinytuya error codes that mean "could not talk to it" rather than "it said no".
_NETWORK_ERRORS = {"901", "902", "905", "914"}

UNREACHABLE_HINT = (
    "unreachable: not found on the LAN. Check it is powered on, and close the "
    "Smart Life app on your phone (it can hold the bulb's only local connection)."
)


class LightError(Exception):
    def __init__(self, message: str, *, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable


@dataclass
class Outcome:
    light: Light
    ok: bool
    state: LightState | None = None  # what the light is now doing (best knowledge)
    error: str | None = None
    unreachable: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"light": self.light.label, "name": self.light.name, "ok": self.ok}
        if self.state is not None:
            out.update(self.state.to_dict())
        if self.error:
            out["error"] = self.error
        return out


def open_device(light: Light) -> Any:
    """Create the tinytuya device. Tests replace this with a fake bulb."""
    tinytuya = _tinytuya.load()  # lazy, so commands that fail early stay instant

    device = tinytuya.BulbDevice(
        light.id,
        light.ip,
        light.key,
        version=light.version,
        connection_timeout=CONNECT_TIMEOUT,
        connection_retry_limit=RETRIES,
        connection_retry_delay=0.2,
    )
    device.set_socketPersistent(True)
    device.set_socketTimeout(SOCKET_TIMEOUT)
    device.set_socketRetryLimit(RETRIES)
    return device


class Connection:
    """An open line to one light, plus the knowledge of which DP does what."""

    def __init__(self, light: Light):
        if not light.ip:
            raise LightError("no IP address known yet", unreachable=True)
        self.light = light
        self.device = open_device(light)
        self.profile = protocol.profile_from_mapping(light.mapping)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.device.close()

    def read(self) -> LightState:
        result = self.device.status()
        _raise_for(result)
        if not isinstance(result, dict) or not isinstance(result.get("dps"), dict):
            raise LightError("the light sent no status")
        if self.profile is None:
            # No wizard mapping: status() has just let tinytuya detect the bulb type.
            self.profile = protocol.profile_from_dpset(getattr(self.device, "dpset", {}))
            if self.profile is None:
                raise LightError("could not work out what kind of light this is")
        return protocol.decode(self.profile, result["dps"])

    def write(self, desired: LightState) -> LightState:
        current = None
        if self.profile is None or protocol.needs_current_state(self.profile, desired):
            current = self.read()
        assert self.profile is not None
        try:
            dps = protocol.encode(self.profile, desired, current)
        except protocol.Unsupported as e:
            raise LightError(str(e)) from None
        _raise_for(self.device.set_multiple_values(dps))

        if desired.on is False:
            return LightState(on=False)
        rgb = desired.rgb
        if (
            rgb is None
            and desired.temp is None
            and current is not None
            and current.mode == "colour"
        ):
            rgb = current.rgb
        return LightState(on=True, brightness=desired.brightness, temp=desired.temp, rgb=rgb)


def _raise_for(result: Any) -> None:
    if isinstance(result, dict) and "Error" in result:
        code = str(result.get("Err", ""))
        raise LightError(str(result["Error"]), unreachable=code in _NETWORK_ERRORS)


Job = Callable[[Connection], LightState]


def _attempt(light: Light, job: Job) -> Outcome:
    conn: Connection | None = None
    try:
        conn = Connection(light)
        return Outcome(light, ok=True, state=job(conn))
    except LightError as e:
        return Outcome(light, ok=False, error=str(e), unreachable=e.unreachable)
    except OSError as e:  # sockets can raise directly on some platforms
        return Outcome(light, ok=False, error=str(e), unreachable=True)
    finally:
        if conn is not None:
            conn.close()


def _parallel(tasks: list[tuple[Light, Job]]) -> list[Outcome]:
    if not tasks:
        return []
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        return list(pool.map(lambda t: _attempt(*t), tasks))


def run(tasks: list[tuple[Light, Job]], *, repair: bool = True) -> list[Outcome]:
    """Run each light's job in parallel; repair IPs and retry once if any is unreachable."""
    outcomes = _parallel(tasks)
    if not repair or not any(o.unreachable for o in outcomes):
        return outcomes

    unreachable = [o.light.id for o in outcomes if o.unreachable]
    try:
        scan = devices.repair_ips(unreachable)
        fresh = {li.id: li for li in devices.load_lights()}
    except Exception:  # a failed scan must not hide the original errors
        scan, fresh = devices.ScanResult(), {}

    # Retry every light the scan heard: at a new IP, or at the same one if the
    # first failure was just a hiccup (the light is clearly alive).
    jobs = {light.id: job for light, job in tasks}
    retry = [
        (fresh[o.light.id], jobs[o.light.id])
        for o in outcomes
        if o.unreachable and o.light.id in scan.seen and o.light.id in fresh
    ]
    retried = {o.light.id: o for o in _parallel(retry)}

    final = []
    for o in outcomes:
        o = retried.get(o.light.id, o)
        if o.unreachable:
            o.error = UNREACHABLE_HINT
        final.append(o)
    return final


def read_job(conn: Connection) -> LightState:
    return conn.read()


def write_job(desired: LightState) -> Job:
    return lambda conn: conn.write(desired)


class Stopwatch:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    @property
    def ms(self) -> int:
        return round((time.perf_counter() - self.start) * 1000)
