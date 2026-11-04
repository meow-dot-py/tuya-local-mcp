"""tuya-local-mcp: control Tuya / Smart Life bulbs over your LAN, no cloud involved.

The package is split by responsibility so each piece can be read on its own:

    config.py    where files live, and safe JSON reading/writing
    devices.py   loading devices.json, resolving "setup" / "bed,ceiling" to lights, LAN scans
    state.py     LightState (what a light is doing / should do) and input validation
    protocol.py  translating a LightState to Tuya data points (DPs) and back
    presets.py   loading, validating and saving presets.json
    engine.py    talking to the bulbs in parallel, with automatic IP repair
    commands.py  the actions both front-ends share (on/off/toggle/preset/set/status)
    timers.py    delayed commands that survive the terminal or Claude Code closing
    render.py    TrueColor terminal output
    cli.py       the `tuya-local-mcp` / `l` command line
    server.py    the MCP server for Claude Code
"""

__version__ = "0.1.0"
