"""AmberIT over MCP.

One honest tool -- `status` -- shipped before any call logic exists, because it
needs no selectors. Proving the server, the serial plumbing and the four client
configs end to end is most of this project's risk, and one real read-only tool is
the cheapest way to discharge it while Tasks 6-7 wait on the phone.

Tool bodies are one to three lines a voice assistant can speak: no JSON, no
braces. Android-MCP's pattern is followed -- FastMCP, string returns,
ToolAnnotations hints -- because that is the proven shape for this transport
(`E:/Android-MCP/src/android_mcp/__main__.py`), and this project pins fastmcp 2.x.

Two layering rules hold this file together:

* `TransportError` is never swallowed back into a generic reach string. The
  driver raises it for a broken screen read; `session.ensure_ready` converts it
  to the adb rung with its own human sentence. "the phone is locked", "wireless
  debugging dropped" and "AmberIT is not on screen" are different spoken
  sentences, and this layer's only job is to not blur them. The one string this
  file writes -- the locked sentence, before the ladder runs -- exists because
  `status` must not swipe or type to unlock a phone it is only reporting on.
* Nothing here can place a call. No start intent, no call or dial action, no
  telephone URI -- asserted against this module's own source in
  `tests/unit/test_server.py`, not just trusted. AmberIT is third-party signed and
  handles none of those intents (spec), so there is no dial path to defend here.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from amber_mcp import config, session
from amber_mcp.driver import Driver, TransportError

INSTRUCTIONS = (
    "Drives the AmberIT IP calling app on one Android phone over adb. "
    "Ask for status first when unsure the phone is ready; it never dials. "
    "Call placement is not yet available from this build."
)

mcp = FastMCP(name="Amber-MCP", instructions=INSTRUCTIONS)

# Not constructed at import time: an MCP host starts the server before the phone
# is plugged in, so building a Driver (which resolves adb and opens a u2 session)
# on import would crash every launch. One Driver per process, made on first use,
# so one phone is never driven twice.
_driver: Optional[Driver] = None


def get_driver() -> Driver:
    """One driver per process, built lazily and reused, with a sentence -- not a
    traceback -- when no serial is configured."""
    global _driver
    if _driver is None:
        serial = config.serial_from_env()
        if not serial:
            raise ConnectionError(
                "No phone configured. Set AMBER_PHONE_SERIAL to <ip>:<port> "
                "after `adb connect`."
            )
        driver = Driver(serial)
        driver.connect()
        _driver = driver
    return _driver


def _spoken(text: str) -> str:
    """Flatten to one line a voice assistant can speak: braces out, whitespace
    collapsed, length cut. The PIN is never in `text`, and no rung detail here
    carries it."""
    flat = " ".join(str(text).replace("{", " ").replace("}", " ").split())
    return flat[:160]


async def status_tool() -> str:
    try:
        driver = get_driver()
    except (ConnectionError, RuntimeError) as exc:
        # A get_driver failure means there is no session at all -- no serial, or
        # uiautomator2 could not attach. This is the only place a reach string is
        # produced; a TransportError from a later read never lands here, because
        # this try wraps only construction.
        return _spoken(f"I can't reach the phone. {exc}")

    # One read of the screen, before the ladder, so `status` can name a locked
    # phone without touching it. A broken read raises TransportError -- a hung
    # dumpsys is "wireless debugging dropped", rung-1 news -- so it goes through
    # `say()` on the adb rung rather than being re-swallowed into the reach string
    # above, which would be a different claim than the one the driver made.
    try:
        focus = driver.current_focus()
    except TransportError as exc:
        return session.say(session.Readiness(False, "adb", str(exc)[:90]))

    # Derive lock-ness from this one read (the ladder's own read-once rule), not
    # by calling `is_keyguard_up()`, which would spend a second dumpsys to answer
    # a question the focus string in hand already answered.
    if session._keyguard_shown(focus):
        return "The phone is locked, so I can't use AmberIT. Unlock it and try again."

    # Past the lock, let the ladder speak the rest -- adb, app, or ready -- each
    # rung with its own sentence. A ready phone reports the activity it saw.
    return session.say(session.ensure_ready(driver))


# Registered by call, not by decorator: fastmcp 2.x's `@mcp.tool` replaces the
# name with a `FunctionTool`, which would make `server.status_tool` unawaitable.
# Passing the function positionally registers it under "status" while leaving the
# `status_tool()` -> coroutine the interface promises -- Android-MCP's own
# decorator form only works because its tools are never awaited directly.
mcp.tool(
    status_tool,
    name="status",
    description=(
        "Report whether the phone is reachable and what AmberIT is showing. Never dials."
    ),
    annotations=ToolAnnotations(title="Phone status", readOnlyHint=True),
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
