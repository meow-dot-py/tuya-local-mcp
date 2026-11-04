# tuya-local-mcp

Zero-cloud LAN controller for Tuya / Smart Life bulbs: an MCP server for Claude Code plus a TrueColor CLI.

```text
$ l focus
[FOCUS] ceiling: off | setup: 85% · temp 90 | bed: 15% · temp 0  312 ms
```

- **Local only.** Commands go straight to the bulbs over your Wi-Fi (TCP 6668). The Tuya cloud is used once, to download each bulb's key, and never again.
- **A CLI you'll actually use.** `l` toggles the room, `l bed 20` dims the bed lamp, `l sleep 30m` turns everything off later. Output is painted in the colour the bulb shows.
- **Works in Claude Desktop too, on any plan**, including ambient mode.
- **Claude Code integration.** A `/light` command that understands "fireplace vibe on the desk in 10 minutes". Lights turn red when your tests fail and flash green when they pass again. An optional ambient mode lets Claude set the mood while it works.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Setup](#setup)
  1. [Install the command](#1-install-the-command)
  2. [Get your bulbs' local keys (once)](#2-get-your-bulbs-local-keys-once)
  3. [Try it](#3-try-it)
  4. [Add it to Claude Code](#4-add-it-to-claude-code), or [to Claude Desktop](#4b-or-add-it-to-claude-desktop-any-plan-including-free)
- [Using the CLI](#using-the-cli)
- [Lights, zones and targets](#lights-zones-and-targets)
- [Presets and scenes](#presets-and-scenes)
- [Claude Code features](#claude-code-features)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Development](#development)

---

## How it works

One program, two ways in. When you type a command, it's a CLI. When Claude Code starts it (no terminal attached), it's an MCP server. Both share the same engine:

```text
   you, in a terminal                     Claude Code
   ──────────────────                     ───────────
   l bed night                            /light "reading in bed"
         │                                 │            │
         │                          MCP tools (stdio)   hooks: test failed / passed
         ▼                                 ▼            ▼
   ┌────────────────────────────────────────────────────────────┐
   │ tuya-local-mcp                                             │
   │                                                            │
   │   cli.py ─┐                     ┌─ server.py   signals.py  │
   │           ▼                     ▼                          │
   │      commands.py  ── presets.json, devices.json            │
   │           │          (in ~/.config/tuya-local-mcp)         │
   │           ▼                                                │
   │      engine.py: one connection per light, all in parallel  │
   │        • builds the whole change as ONE message            │
   │        • light unreachable? listen for its LAN broadcast,  │
   │          save the new IP, retry                            │
   └───────────┬───────────────────────┬────────────────────────┘
               │ TCP 6668              │ UDP 6666/6667 (discovery)
               ▼                       ▼
        ┌────────────┐  ┌────────────┐  ┌────────────┐
        │  ceiling   │  │   setup    │  │    bed     │   your Wi-Fi bulbs
        └────────────┘  └────────────┘  └────────────┘
```

Delayed commands (`l sleep 30m`, or Claude's `delay_seconds`) run in a small background process, so they still fire after you close the terminal or Claude Code.

## Requirements

- Tuya-based Wi-Fi bulbs that work with the **Smart Life** or **Tuya Smart** app. Many cheap "smart bulbs" are Tuya inside, and the app tells you: if it's Smart Life, it works.
- The computer on the same local network as the bulbs.
- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A free Tuya IoT developer account, used once to download the keys (step 2).
- For the Claude Code features: [Claude Code](https://code.claude.com).

## Setup

### 1. Install the command

```bash
uv tool install git+https://github.com/meow-dot-py/tuya-local-mcp
```

This puts `tuya-local-mcp` on your PATH. Check it with `tuya-local-mcp help`.

To update later, run `uv tool upgrade tuya-local-mcp`.

### 2. Get your bulbs' local keys (once)

Each bulb encrypts its local traffic with its own key. The only way to get that key is through Tuya's developer cloud. You do this once, and again only if you re-pair a bulb or add new ones.

**a. Create a cloud project**

1. Sign up at [platform.tuya.com](https://platform.tuya.com) (free).
2. Go to **Cloud → Development → Create Cloud Project**.
   - Development method: **Smart Home**.
   - Data center: the one your app account uses. If you're unsure, it's usually the closest to you: *Central Europe*, *Western America*, *Eastern America*, *India* or *China*.
3. When asked for API services, keep the defaults. Only two are needed: **IoT Core** and **Authorization Token Management**. The others the template adds (Smart Home Basic Service, Device Status Notification, ...) are harmless. You can check them later under the project's **Service API** tab.
4. On the project's **Overview** tab, copy the **Access ID** and **Access Secret**.

> [!NOTE]
> IoT Core is a free trial that expires after about a month. It's only needed while the wizard downloads keys, so the lights keep working after it expires. When you add or re-pair a bulb later, renew it first: **Service API → IoT Core → Extend trial period** (free, after a short questionnaire).

**b. Link your Smart Life account**

1. In the project, open **Devices → Link App Account → Add App Account**. A QR code appears.
2. In the Smart Life app, go to **Me →** the scan icon (top right), and scan it.
3. Your bulbs now appear under **Devices**. Copy any one **Device ID**.

**c. Run the wizard**

```bash
tuya-local-mcp init
```

It runs the [tinytuya](https://github.com/jasonacox/tinytuya) wizard inside the config folder (`~/.config/tuya-local-mcp`) and asks for:

| Prompt | Answer |
|---|---|
| API Key | the Access ID |
| API Secret | the Access Secret |
| any Device ID | the Device ID you copied |
| Your Region | `eu`, `us`, `us-e`, `in` or `cn`, matching your data center |
| Download DP Name mappings? | **Y**. This lets each change go out as a single message |
| Poll local devices? | **Y**. This finds the IPs |

> [!IMPORTANT]
> **Force-close the Smart Life app on your phone** before polling and whenever you use the lights from here. Many bulbs accept only one local connection, and the app may be holding it.

When it finishes, you'll see your lights listed. The wizard also leaves `tinytuya.json`, `tuya-raw.json` and `snapshot.json` in the config folder. **They contain secrets: never share or commit them.**

<details>
<summary>Prefer to fill in devices.json by hand?</summary>

Copy [`devices.example.json`](devices.example.json) to `~/.config/tuya-local-mcp/devices.json` and fill in each bulb's `id`, `key`, `ip` and `version` (3.3, 3.4 or 3.5). Without the wizard's `mapping`, the first command to each bulb takes one extra round trip to detect its type.
</details>

### 3. Try it

```bash
tuya-local-mcp status
tuya-local-mcp presets
```

Then give it a short name. In **PowerShell** (`notepad $PROFILE`):

```powershell
function light { tuya-local-mcp @args }
Set-Alias -Name l -Value light
```

In **bash / zsh** (`~/.bashrc` or `~/.zshrc`):

```bash
alias l='tuya-local-mcp'
alias light='tuya-local-mcp'
```

From here on, the examples use `l`.

### 4. Add it to Claude Code

**The plugin (recommended)** gives you everything: the MCP tools, `/light`, the test/build signals and ambient mode. Inside Claude Code:

```text
/plugin marketplace add meow-dot-py/tuya-local-mcp
/plugin install tuya-local@tuya-local-mcp
```

Then open `/config` to adjust the plugin's options (see [Claude Code features](#claude-code-features)).

**MCP tools only**, if you don't want the hooks or skill:

```bash
claude mcp add tuya-local -s user -- tuya-local-mcp serve
```

> [!NOTE]
> Use one or the other. The plugin already includes the MCP server, so registering both gives Claude every tool twice.

### 4b. Or add it to Claude Desktop (any plan, including free)

The Claude desktop app can use the MCP server in normal chats, so you can say "make it cosy in here" or "lights off in 20 minutes" without Claude Code.

1. Find the full path of the command. In PowerShell: `(Get-Command tuya-local-mcp).Source`. In bash/zsh: `which tuya-local-mcp`.
2. In Claude Desktop, open **Settings → Developer → Edit Config** and add the server to `claude_desktop_config.json`:

   ```json
   {
     "mcpServers": {
       "tuya-local": {
         "command": "C:\\Users\\you\\.local\\bin\\tuya-local-mcp.exe",
         "args": ["serve"],
         "env": { "TUYA_LOCAL_MCP_AMBIENT": "false" }
       }
     }
   }
   ```

   Use the full path from step 1 (on Windows, double each backslash). The desktop app doesn't always see the same PATH as your terminal.

   Can't find the menu? Edit the file directly. Usually it's `%APPDATA%\Claude\claude_desktop_config.json` on Windows and `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS. Microsoft Store installs may keep it elsewhere, such as `%LOCALAPPDATA%\Claude-Data\claude_<id>\`. This PowerShell command finds it: `Get-ChildItem $env:APPDATA, $env:LOCALAPPDATA -Recurse -Depth 3 -Filter claude_desktop_config.json`. Add `mcpServers` next to whatever is already in the file.
3. Fully quit Claude Desktop (including from the system tray) and reopen it. **tuya-local** now appears under **+ → Connectors**.
4. Make sure its switch is on there, and under **+ → Connectors → Tool access** allow its tools (or choose **Always allow** the first time Claude uses one). Otherwise every light change, including ambient ones, waits for your approval.

Ambient mode works here too, in two ways:

- **Always on:** set `TUYA_LOCAL_MCP_AMBIENT` to `"true"` in the config above. Every chat starts with the ambient instructions, and Claude reads the local time from `light_status` when the hour matters.
- **For one chat:** **+ → Connectors → Add from tuya-local → Ambient mode** attaches the instructions, with the current time, to your message. Use it when the variable is off, or to refresh the time in a long-running chat.

Using both the plugin and this config entry? The desktop app also hands this server to its Code tab sessions, so the plugin notices it and steps aside there: its own copy of the server registers no tools, and its ambient hook stays quiet if the entry above has ambient on. You get each tool once. The terminal `claude` CLI doesn't read this file, so it keeps using the plugin's copy.

What you don't get outside Claude Code: the `/light` shortcut (you don't need it, just ask) and the red/green test signals (there are no commands to watch in a chat).

## Using the CLI

```text
l                         toggle: any light on -> all off, otherwise all on
l [target] on|off|toggle
l [target] <preset>       l focus · l bed night
l [target] <bri> [temp]   l 40 · l setup 85 90   (brightness 1-100, temp 0 warm - 100 cool)
l [target] rgb R G B [bri]   l rgb 255 0 128
l [target] sleep <time> [command]   l sleep 30m · l bed sleep 1h night
l [target] status

l presets                 your presets, with colour previews
l lights                  your lights and every name that targets them
l scan                    look for lights whose IP changed (also automatic on errors)
l init                    the one-time key download (step 2)
l signal fail|pass        try the red/green build signal by hand
```

Durations accept `90`, `45s`, `10m`, `1h30m` or `1.5h`. Out-of-range numbers are clamped with a warning. A mistyped word gets a suggestion (`'focsu' ... Did you mean: focus?`). The exit code is `0` on success, `1` if any light failed and `2` for a usage error, so the CLI works in scripts.

Colours turn off automatically when the output isn't a terminal or `NO_COLOR` is set.

## Lights, zones and targets

A **target** picks lights. It can be:

| Target | Picks |
|---|---|
| `all` (default) | every light |
| a **zone** | lights with that `"zone"` in devices.json |
| a full **name** | `"desk lamp"` matches *Desk Lamp* |
| a **word** of a name | `desk` or `lamp` matches *Desk Lamp* |
| `bed,setup` | several, comma-separated |

Matching is whole-word and case-insensitive, so `s` never matches by accident. An unknown target is an error that lists the valid ones; it never quietly picks some other light.

Give your bulbs short zone names by adding `"zone"` to their entries in `~/.config/tuya-local-mcp/devices.json`:

```json
{ "name": "Lámpara de noche", "zone": "bed", "id": "...", "key": "...", "ip": "192.168.1.52", "version": "3.3" }
```

## Presets and scenes

Presets live in `~/.config/tuya-local-mcp/presets.json` and are re-read on every command, so edits apply at once. Until you save your own, a built-in set is used (`code`, `chill`, `night`, `cyber`, `red`, `focus`, `gaming`, `movie`).

A **preset** is one setting for whatever you target:

```json
{
  "chill":   { "temp": 0, "brightness": 20, "description": "Dim, warm and relaxed" },
  "cyber":   { "rgb": [255, 0, 255], "brightness": 90 }
}
```

A **scene** gives each target its own setting:

```json
{
  "focus": {
    "targets": {
      "ceiling": { "on": false },
      "setup":   { "temp": 90, "brightness": 85 },
      "bed":     { "temp": 0,  "brightness": 15 }
    }
  }
}
```

Settings accept `on`, `brightness` (1-100), `temp` (0-100) and `rgb` (`[r, g, b]`, 0-255). Use either `temp` (white) or `rgb` (colour), not both. A target narrows a scene: `l bed focus` applies only focus's `bed` setting. Scene targets that match none of your lights are skipped and reported.

A broken preset is skipped with a warning; it never takes the others down with it. Claude can also create, edit and delete presets for you.

## Claude Code features

### `/light`

```text
/light focus
/light bed 20
/light fireplace vibe on the desk
/light apaga todo en media hora
/light save this as "reading": warm, 60%
```

It maps any phrasing, in any language, to a single tool call, with no status lookups first, and replies with one line in the CLI's format. Plugin skills can also be invoked as `/tuya-local:light`.

### Build and test signals

| What happens | The lights |
|---|---|
| A test or build command Claude runs **fails** | turn red (their previous state is saved) |
| It **passes** again | flash green for 4 s, then go back to exactly how they were |
| It passes and nothing had failed | don't change, so green runs don't flicker |

This uses Claude Code hooks, so it works every time and costs no tokens. Recognised commands include pytest, tox, jest, vitest, `npm|pnpm|yarn|bun test/build`, cargo, go, make, gradle, mvn, dotnet, tsc, mypy, pyright, swift, xcodebuild and `docker build`. If a red state is older than 3 hours, the next pass leaves your lights alone instead of restoring something stale.

### Ambient mode

When enabled, Claude gets a short instruction at the start of each session: shift to `focus` during long debugging, to `cyber` or `gaming` for creative work, and to `chill` or `night` late at night. It does this at most once per task, silently, with at most a `[LIGHT: focus]` tag at the end of a reply. It's off by default.

### Options (`/config` → tuya-local)

| Option | Default | |
|---|---|---|
| Build/test signals | on | turn the red/green signals on or off |
| Signal lights | `all` | which target shows them, e.g. `setup` |
| Signal commands (regex) | built-in list | your own regular expression for "test or build" commands |
| Ambient mood | off | let Claude set the mood on its own |

Outside Claude Code, see [Claude Desktop](#4b-or-add-it-to-claude-desktop-any-plan-including-free) for ambient mode. Without the plugin, set the same options as environment variables: `TUYA_LOCAL_MCP_SIGNALS`, `TUYA_LOCAL_MCP_SIGNAL_TARGET`, `TUYA_LOCAL_MCP_SIGNAL_COMMANDS` and `TUYA_LOCAL_MCP_AMBIENT`. To use the hooks without the plugin, copy the entries from [`plugin/hooks/hooks.json`](plugin/hooks/hooks.json) into your `settings.json`.

### MCP tools

| Tool | Does |
|---|---|
| `power_light(state, target, delay_seconds)` | on / off / group toggle |
| `set_light(target, preset \| brightness, temp, rgb, delay_seconds)` | apply a preset or scene, or set values |
| `light_status(target)` | what each light is doing now, plus the local time |
| `list_lights()` | lights, zones, the names that target them, IPs, presets |
| `get_presets()` / `upsert_preset(name, config)` / `delete_preset(name)` | manage presets.json, with validation |
| `scan_lan_ips()` | find moved lights and save their new IPs |

The server also offers an **`ambient_mode` prompt** that switches ambient mode on for one conversation, in any client that supports MCP prompts.

Every result includes a one-line `summary` and per-light `ok`/`error`, so a failure is never reported as success.

## Troubleshooting

**A light is "unreachable".** It's probably one of these:

1. **The Smart Life app is open** on a phone and holding the bulb's only local connection. Force-close it.
2. **The bulb is switched off at the wall.** Bulbs can't be reached without power.
3. **Its IP changed.** This is fixed automatically (the light is found by its broadcast and the new IP saved). You can also run `l scan`.
4. **Firewall.** Allow Python to receive UDP 6666/6667 (discovery) and connect to TCP 6668.

**"Check device key or version".** The bulb was re-paired in the app, which gives it a new key. Run `tuya-local-mcp init` again.

**The signals don't fire.** Check `~/.config/tuya-local-mcp/hook.log`. Every signal and every error is logged there. Make sure `tuya-local-mcp` is on the PATH that Claude Code sees, and try `l signal fail` / `l signal pass` by hand.

**How fast is it?** About 0.3-0.5 s per command, end to end. Most of that is Python starting and the bulb acknowledging the message (~200 ms on typical bulbs). Through the MCP server the process is already running, so only the bulb's time is left.

## Security

- Bulb keys and Tuya cloud credentials stay in `~/.config/tuya-local-mcp/`, outside any repository. Set `TUYA_LOCAL_MCP_HOME` to use another folder.
- Files there are written atomically, so a crash can't corrupt them.
- After the one-time setup, nothing talks to the internet. You can even revoke the Tuya cloud project.
- This repository's `.gitignore` excludes every file the wizard creates, in case you run it in the repo by mistake.

## Development

```bash
git clone https://github.com/meow-dot-py/tuya-local-mcp
cd tuya-local-mcp
uv sync
uv run pytest           # uses fake bulbs; no hardware or network needed
uv run ruff check . && uv run ruff format --check .
```

To use your working copy everywhere while you hack on it: `uv tool install --editable .`

To test the plugin from the repo without installing it: `claude --plugin-dir ./plugin`.

Each module has one job and a docstring that explains it. Start with [`src/tuya_local_mcp/__init__.py`](src/tuya_local_mcp/__init__.py) for the map.

## License

[MIT](LICENSE) © 2026 Johann 'M' Nestorida. Built on [tinytuya](https://github.com/jasonacox/tinytuya) and the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Not affiliated with Tuya.
