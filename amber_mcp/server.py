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
  sentences, and this layer's only job is to not blur them. Which is also why
  there is no screen read here before the ladder: `status_tool` asks one question
  -- climb the ladder and say which rung stopped -- and speaks the answer it
  gets. A pre-read of its own would both buy a second `dumpsys window` (the
  read-once rule `session.py` declares governs this file too) and front-run rung
  1, whose reconnect is the whole reason a wireless serial is worth having: with
  the wire down, the pre-read raises first and `adb connect` never runs, so the
  tool would tell a human to do the one thing it is able to do itself.
* Nothing here can place a call. No start intent, no call or dial action, no
  telephone URI -- asserted against this module's own source in
  `tests/unit/test_server.py`, not just trusted. Measured on the device 2026-11-07:
  AmberIT does not accept the intent that places a call (that route is always the
  cellular line), but it *does* accept the one that pre-fills its dialpad and places
  nothing. So there is still no call path to defend here, and the pre-fill belongs
  to plan 2's dialpad flow -- not to a module whose only tool is a read.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from amber_mcp import config, session
from amber_mcp.driver import Driver

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

    # One call, one answer: the ladder owns every rung, including rung 1's
    # liveness check and its reconnect, the lock sentence and the app sentence.
    # `pin=None` is not an omission -- a status poll does not unlock a phone by
    # force. Left at its default the ladder would consult `REDMI_PIN` and type it
    # whenever the pre-swipe read happens to say "not locked" over a dozing screen,
    # so a read-only poll could enter the user's device PIN without being asked.
    # The wake and the swipe stay, because they are how the ladder reads a screen
    # at all; the PIN is how it gets past a lock, and that is a different task.
    return session.say(session.ensure_ready(driver, pin=None))


# Registered by call, not by decorator: fastmcp 2.x's `@mcp.tool` replaces the
# name with a `FunctionTool`, which would make `server.status_tool` unawaitable.
# Passing the function positionally registers it under "status" while leaving the
# `status_tool()` -> coroutine the interface promises -- Android-MCP's own
# decorator form only works because its tools are never awaited directly.
mcp.tool(
    status_tool,
    name="status",
    description=(
        "Report whether the phone is reachable and what AmberIT is showing. Reading "
        "the screen costs three actions on the phone: it wakes the display, swipes "
        "the lock screen and launches AmberIT to the front. It never dials and never "
        "types your PIN."
    ),
    # `readOnlyHint` alone says the read changes nothing that matters; it is not the
    # precise licence the ladder runs under, because the wake, the swipe and the
    # launch above are actions. `destructiveHint=False` is the accurate half of that
    # claim and `readOnlyHint=True` stays -- the controller's ruling -- so a voice
    # client still auto-approves a poll instead of demanding confirmation on a read.
    annotations=ToolAnnotations(
        title="Phone status", readOnlyHint=True, destructiveHint=False
    ),
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
