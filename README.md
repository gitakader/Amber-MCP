# Amber-MCP

MCP server driving the AmberIT IP-calling app on one Android phone.

## Setup
1. Wireless debugging on the phone, then `adb connect <phone-ip>:<port>`
2. `uv sync`
3. `export AMBER_PHONE_SERIAL=<phone-ip>:<port>`

`status` wakes the screen, swipes the lock screen and brings AmberIT forward to read
them; it never enters the PIN. Bringing it forward is the part to know about: rung 4 of
the readiness ladder launches the app whenever it is not already the focused package, so
a `status` poll **takes the screen over from whatever else you had open** — your chat,
your browser, the launcher — and puts a signed-in AmberIT, balance visible, on top of it.
Nothing is dialled and nothing is typed, but the phone is not left as you had it, so do
not ask while you are holding it.
`REDMI_PIN` is only for a tool whose task is to reach the app when that swipe fails, and it is never stored and never printed.

## Client config
Claude Desktop / Opencode / Hermes / JARVIS all take the same server. Use the absolute
uv path, not `uv`: a GUI-launched host gets a different PATH than this terminal.

    "amber-mcp": { "command": "C:/Users/Mukul/AppData/Local/hermes/bin/uv",
                   "args": ["run", "--directory", "E:/Amber-MCP", "amber-mcp"] }

## Tools
`status` — reachability and what AmberIT is showing. It takes the screen over from
whatever else is open to read it (see Setup). Nothing else is wired yet;
call placement arrives with the semantic-tools plan.

## Why the screen must be awake
AmberIT is a third-party signed APK. Measured on the device: it does **not** handle
`android.intent.action.CALL`, so there is no intent that places a call past the screen — that
route is always the cellular line. It **does** handle `ACTION_DIAL` with `tel:` data, via
`TelIntentRouterActivity`, which brings the dialpad up with the number prefilled (leading `+`
stripped) and places nothing. So the app's own screen is still the only door to the IP line;
the deep link is just a shorter walk to the dialpad, and it replaces tapping twelve
anonymous keypad cells by geometry. See
docs/superpowers/specs/2026-11-06-amber-mcp-design.md and artifacts/SELECTORS.md.
