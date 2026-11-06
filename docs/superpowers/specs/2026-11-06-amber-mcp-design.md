# Amber-MCP — design

An MCP server that lets a voice assistant place and follow up on calls made through the
**AmberIT IP line** on a specific Redmi/MIUI phone, by driving the AmberIT app's own UI.

## Why this exists

The phone's cellular line is already reachable from JARVIS through
`E:\Mark-LV-main\plugins\android_phone.py`, which places calls with
`am start -a android.intent.action.CALL -d tel:<number>`, and reads the call log,
device contacts, notifications, SMS, volume and settings over the shell.

AmberIT's **IP line** is not reachable that way. Proven on the device on 2026-11-06:

| Probe | Result |
|---|---|
| `cmd package query-activities -a android.intent.action.CALL -d 'tel:<test-number>'` | resolves to `com.android.server.telecom.components.UserCallActivity` and `com.eyecon.global` — **`com.amberit.dialer` is absent**. The two that did appear are the positive control: the query works, AmberIT genuinely does not handle the intent. |
| `cmd package query-activities --components -a android.intent.action.MAIN -p com.amberit.dialer` | only `SplashScreenActivity` and `NotificationSettingsActivity` are exported. There is no "dial this number" entry point. |
| `dumpsys package com.amberit.dialer` | `versionName=1.3.2`, launcher `com.revesoft.itelmobiledialer.ui.feature.splash.SplashScreenActivity`, live activity `ui2.dashboard.AgniDashboardActivity` |

So the app's own screen is the only door, and that makes Amber-MCP's job exactly one thing:
**turn AmberIT's UI into a handful of reliable commands.**

## Non-goals

Deliberately excluded, each by the user's decision during design review:

- **Bulk / lead-list autodialing.** No queue, no campaign, no journal, no resume. The
  assistant is in the loop one command at a time.
- **Cellular fallback.** `call()` uses the IP line only. When AmberIT cannot be driven, the
  call does not happen and the reason is returned. Silent fallback to SIM minutes is worse
  than a failure the assistant can say out loud.
- **A confirmation gate before dialling.** Direct execution. What is kept instead is proof
  (see Verification) and loud failure.
- **A new device layer.** Amber-MCP does not re-implement screen reading; the code is
  copied from Android-MCP, which is MIT licensed.
- Anything else Mark-LV already does (SMS, notifications, settings, volume, files).

## Proven constraints

These are measured on the target device, not assumed. Each one has already broken an
automation attempt somewhere.

1. **`uiautomator dump` is broken on this MIUI build.** It throws
   `FileNotFoundException: /data/system/theme_config/theme_compatibility.xml` from
   `miui.content.res.ThemeCompatibilityLoader`, then prints
   `UI hierchary dumped to: /sdcard/d.xml` **while writing a 0-byte file**. The success
   message is a lie; the only sound path is uiautomator2's instrumentation server.
   Mark-LV documents the same finding independently.
2. **`wm dismiss-keyguard` does not unlock this phone.** After it, `mCurrentFocus` stayed on
   `NotificationShade` and `dumpsys power` reported `mWakefulness=Dozing`.
3. **`screencap` returns 0 bytes while the display is dozing** and a real image once awake.
   A screenshot taken without asserting wake state is a blank frame that will be
   misread as "the screen is black" rather than "we never woke it".
4. **VoIP calls are invisible to `dumpsys telephony.registry mCallState`.** That counter
   tracks cellular. It is `0` forever during an AmberIT call, so the answer/no-answer
   decision cannot come from there. Mark-LV's `call_state()` is correct — for cellular only.
5. **WiFi ADB drops.** The serial is `<phone-ip>:<port>` and the port is not stable.
6. **Git Bash rewrites device paths in adb arguments.** `adb shell uiautomator dump
   /sdcard/d.xml` became `/Files/Git/sdcard/window_dump.xml`. Needs `MSYS_NO_PATHCONV=1`.
   This is a hazard for hand-run probes; the server calls adb directly, so it does not
   inherit it.

## Architecture

```
JARVIS · Claude · Opencode · Hermes
        │  MCP over stdio (one server config line reused by all four)
        ▼
E:\Amber-MCP
  amber_mcp/server.py     MCP tool definitions. No device knowledge.
  amber_mcp/session.py    "is the phone ready?" — one check, used by every tool.
  amber_mcp/driver.py     uiautomator2 session, adb discovery + reconnect, tap/type/
                          snapshot/wait_for. Copied from Android-MCP where possible.
  amber_mcp/screens.py    AmberIT selector table: screen → element → selector.
```

Boundaries, so each file can be read and tested alone:

- `server.py` knows **verbs**. It never mentions a resource-id or a coordinate.
- `screens.py` knows **where things are**. It is a table of selectors and expected
  transitions, with no control flow. When AmberIT ships a new UI, this file and only this
  file changes.
- `driver.py` knows **how to touch a phone**. It knows nothing about AmberIT.
- `session.py` owns readiness: wake the screen, bring AmberIT to the foreground, verify the
  focused activity, and report which of the three failed.

One device, one writer: a single in-process lock serialises tools. If a second MCP client
calls while a call is in progress it waits. Two clients tapping at once leaves the app half
way through a dialling screen, which is the worst possible state to be in.

## Tools

Every result is 1–3 lines of text a voice assistant can speak, not JSON. A round trip that
needs parsing is a round trip that gets truncated.

| Tool | Behaviour | Returns |
|---|---|---|
| `call(target, scope="all")` | Number-shaped target → dialpad. Name → search, then exactly one match dials. | "Dialling <number> on AmberIT." / a question (below) / a reason |
| `hangup()` | Press End on the live AmberIT call. | "Call ended, 04:12." |
| `answer()` | Press Accept on an incoming AmberIT call. | "Answered." |
| `status()` | Which AmberIT screen is up, registered or not, call up or not and its timer. | "AmberIT ready, registered, balance ৳42." |
| `balance()` | Read the credit shown on the dashboard. | "৳42." |
| `history(limit=5)` | Recent AmberIT calls, from its own call screen. | up to `limit` lines |
| `contacts_search(query, scope="all")` | Search, with scope set explicitly. | matched names + numbers |

### Name resolution rules

`call("rakin")`:

1. 0 matches → return "No AmberIT contact matches 'rakin'." **Dial nothing.**
2. 1 match → dial it.
3. 2+ matches → **dial nothing** and return the candidates. Guessing here means an
   unintended person's phone rings, which is exactly the failure this system cannot take
   back. The assistant asks, then calls again with the number.

### Contact scope

Within AmberIT's search there are two scopes, and their meaning was stated by the user:

- **AmberIT** — only contacts who run an Amber IT service.
- **All** — AmberIT **plus** the phone's normal contacts, i.e. a superset.

Default is **All**. Because All ⊇ AmberIT, a name match prefers the AmberIT-side result: it
is the one that routes over the IP line, which is the reason this server exists.

Scope is **always set explicitly by the flow**, never inherited from whatever the app last
showed. AmberIT remembers the previous scope, so inheriting makes every later search
silently query the wrong list and return plausible-looking wrong results with no error.

## Verification: what counts as proof

`am start` returning 0 and printing `Starting: Intent{…}` is not proof that anything
happened. A comment in Mark-LV records a real incident from 2026-10-21: a dial was reported
as "no call went out" because the check gave up after 4 seconds, while the phone wrote the
call-log row at +18 s. Amber-MCP inherits that lesson:

- Every state claim comes from a **read-back**, not from the write's return code.
- Activity identity from `dumpsys window | grep mCurrentFocus` — stable, and does not
  depend on translated labels surviving an app update.
- An IP call is in progress when AmberIT's focused activity is its in-call screen — that one
  is mandatory — **and** at least one corroborating signal: `dumpsys audio` reporting
  `MODE_IN_COMMUNICATION`, or the on-screen timer having advanced between two reads.
- Answer vs ring-no-answer is decided by whether the audio path ever reached
  `MODE_IN_COMMUNICATION` — not by a snapshot of text.
- Anything that conflicts is reported **UNKNOWN** with the screenshot attached. No
  guessing. A wrong "no answer" discards a lead; an honest unknown can be reviewed.
- **`content://call_log` is the cellular provider and probably does not see IP calls at
  all.** Mark-LV's `latest_call_epoch()` works because its calls are SIM calls. Amber-MCP
  must not borrow it as IP proof without measuring, which is part of the discovery call:
  record whether an AmberIT call writes a row there. If it does not, the only proof is
  AmberIT's own in-call screen and its own call list.

This is not yet evidence: which of `dumpsys audio`, `dumpsys notification`, or the in-call
activity name actually distinguishes these states on *this* app has **not** been observed.
Step 1 of the plan is to place one real call to a number the user nominates, record the raw
output of all three signals, and only then write the classifier. Until that capture exists
the classifier is a hypothesis and must not be merged as if it were tested.

## Screen readiness

`session.ensure_ready()` runs a ladder and reports which rung failed:

1. `adb get-state` — reconnect `<phone-ip>:<port>` from config/env if the serial is gone.
2. `input keyevent KEYCODE_WAKEUP`.
3. Unlock, **no PIN first**: wake + swipe up. This works when the lock is swipe-only.
   If it fails and `REDMI_PIN` is set in the environment, type it as keyevents.
   If neither works, stop with "the phone is locked; REDMI_PIN is not set".
   Neither branch has been tested on this device yet — `wm dismiss-keyguard` failing is the
   only measurement.
4. Launch AmberIT and assert `AgniDashboardActivity` (or the in-call activity, if a call is
   live) is focused.

The PIN is read from `REDMI_PIN` at call time and never written to a file, never logged, and
never included in any tool output. On a shared Windows account, user environment variables
are readable by that user's other processes — acceptable for a personal phone, not for a
machine other people use.

## Failure policy

- Device offline / locked / app not logged in → **say which one**, and dial nothing.
- AmberIT crashes or a dialog appears mid-flow → force-stop, relaunch, report the interrupted
  action. Never leave the app on a random screen.
- Every dial is appended to `E:\Amber-MCP\logs\calls.jsonl` (timestamp, target, number,
  verdict, raw signal samples). This is a record, not a permission check; nothing is blocked
  by it.
- Timeouts bound every wait. No tool may hang an assistant's voice turn indefinitely; the
  ceiling is a config value per action, generous enough for the +18 s call-log case.

## Testing

- **Unit** (no device): selector table resolves; name→0/1/many branches; scope is always
  emitted; UNKNOWN on conflicting signals; number-vs-name routing.
- **Signal fixture tests**: the raw `dumpsys audio` / `notification` / focus captures from
  the discovery call are checked in as fixtures, and the classifier is tested against them.
  This is the only way the classifier can be tested at all without dialling.
- **Live, one call**: exactly the nominated safe number, end to end, verifying via AmberIT's
  own call screen. Any live test that dials must be explicitly approved in the moment.
- **Deliberate FAIL→PASS flip**: each check is first shown failing against a wrong expectation
  and then passing, so a check that can never fail is not mistaken for a working one.

## Prerequisites on the phone

- Wireless debugging on; `adb connect <phone-ip>:<port>`; serial in config/env.
- AmberIT installed and already logged in — the app is third-party and signed, so
  re-authenticating it is out of scope.
- Screen awake during use. Whether the no-PIN unlock ladder is enough is unknown until
  tested; if it is not, the practical answers are setting the lock to swipe-only, or
  `REDMI_PIN`, or a dedicated automation phone.
- AmberIT must not be battery-optimised away. Current Doze whitelist state is **unverified** —
  the probe returned nothing, which is a bad query, not a finding.

## Open items carried into the plan

1. **Selector discovery** — the real resource-ids and texts for dialpad, Call, End, search
   box and both scopes. Requires one session with the phone unlocked and the app driven.
   Nothing in `screens.py` may be invented before this.
2. **Signal capture call** — as above, decides whether `status()` and outcome detection are
   possible at all.
3. **GitHub repo does not exist yet**, and `gh` is not installed, so nothing can be pushed
   to `github.com/gitakader/Amber-MCP` until either the repo is created on the web or
   `winget install GitHub.cli` is approved.
4. **`balance()` and `history()` may not exist in the app at all.** Assumed from typical
   softphone layouts. If discovery finds no such screen, the tool is cut rather than faked.
