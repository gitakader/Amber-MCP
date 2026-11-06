# Amber-MCP

MCP server driving the AmberIT IP-calling app on one Android phone.

## Setup
1. Wireless debugging on the phone, then `adb connect <phone-ip>:<port>`
2. `uv sync`
3. `export AMBER_PHONE_SERIAL=<phone-ip>:<port>`

Optional: `REDMI_PIN` in the environment, used only if a wake-and-swipe fails to
open the lock screen. Never stored, never printed.

## Client config
Claude Desktop / Opencode / Hermes / JARVIS all take the same server. Use the absolute
uv path, not `uv`: a GUI-launched host gets a different PATH than this terminal.

    "amber-mcp": { "command": "C:/Users/Mukul/AppData/Local/hermes/bin/uv",
                   "args": ["run", "--directory", "E:/Amber-MCP", "amber-mcp"] }

## Tools
`status` — reachability and what AmberIT is showing. Nothing else is wired yet;
call placement arrives with the semantic-tools plan.

## Why the screen must be awake
AmberIT is a third-party signed APK whose only entry point is its own UI: it does
not handle `ACTION_CALL`, so there is no intent to fire past the screen. See
docs/superpowers/specs/2026-11-06-amber-mcp-design.md.
