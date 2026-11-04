---
name: light
description: Control the room lights (Tuya / Smart Life bulbs on the LAN). Use when the user asks to turn lights on/off, dim them, change colour or mood, apply or edit a preset/scene, or schedule a light change.
argument-hint: "[target] <on | off | preset | brightness | colour | vibe> [in <time>]"
model: haiku
allowed-tools:
  - mcp__plugin_tuya-local_lights__power_light
  - mcp__plugin_tuya-local_lights__set_light
  - mcp__plugin_tuya-local_lights__light_status
  - mcp__plugin_tuya-local_lights__list_lights
  - mcp__plugin_tuya-local_lights__get_presets
  - mcp__plugin_tuya-local_lights__upsert_preset
  - mcp__plugin_tuya-local_lights__delete_preset
  - mcp__plugin_tuya-local_lights__scan_lan_ips
---

Control the lights with the `lights` MCP tools. Act at once, without asking for
confirmation.

## Targets
The MCP server's instructions list the valid targets (each light's zone or name,
plus `all`). Combine targets with commas: `bed,setup`. If a target is unknown, the
tool's error lists the valid ones: pick the closest match and retry once.

## One call, no lookups
Call `power_light` or `set_light` directly. Do not call `light_status`,
`list_lights` or `get_presets` first, except when:
1. the request is empty: call `power_light(state="toggle")`;
2. the user asks what the lights are doing, or to change one field of an existing preset;
3. a tool result reports a light as unreachable: call `scan_lan_ips` once, then retry.

## Mapping the request
- Preset or scene names: `set_light(preset="<name>", target=...)`. With a scene, a
  target narrows it: "focus for the bed" -> `target="bed", preset="focus"`.
- On / off / toggle: `power_light(state=...)`.
- Numbers: brightness 1-100; temp 0 (warmest) to 100 (coolest); colour `rgb=[r, g, b]`.
  Use temp or rgb, never both.
- Vibes in any language ("fireplace", "reading in bed", "modo estudio", "rainy
  afternoon"): translate to brightness + temp or rgb per target. Examples:
  fireplace -> rgb [255, 90, 20] at 35; reading -> temp 40 at 80; sunset -> rgb [255, 120, 40] at 60.
- Time ("in 10m", "en media hora"): `delay_seconds`. For a clock time ("at 23:00"),
  work out the seconds from now.
- Save a preset: `upsert_preset(name, config)`; flat for one setting
  (`{"temp": 20, "brightness": 50}`), `{"targets": {...}}` for a scene. To edit one,
  `get_presets` first, change only what was asked, then `upsert_preset`.
- Remove a preset: `delete_preset(name)`.

## Reply
One line: the tool result's `summary`, as is. No other text, unless something failed;
then add one short sentence with the fix.

Request: $ARGUMENTS
