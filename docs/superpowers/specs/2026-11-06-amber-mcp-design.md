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
| `cmd package query-activities --components -a android.intent.action.MAIN -p com.amberit.dialer` | only `SplashScreenActivity` and `NotificationSettingsActivity` are exported. There is no exported **activity alias** for "dial this number". |
| `dumpsys package com.amberit.dialer` | `versionName=1.3.2`, launcher `com.revesoft.itelmobiledialer.ui.feature.splash.SplashScreenActivity`, live activity `ui2.dashboard.AgniDashboardActivity` |

**Corrected on 2026-11-07, against the same device.** The first two rows were read as "the
app's own screen is the only door", and that was too strong: `MAIN`-exported components are
not the only way into an app. `ACTION_DIAL` with `tel:` data **is** handled, by
`com.revesoft.itelmobiledialer.ui2.dashboard.TelIntentRouterActivity`, and firing it
explicitly leaves the dialpad up with the number prefilled in `nvNumber` — with the leading
`+` stripped, which is why any number read-back has to compare digits rather than strings.
No call was placed by that probe (`mCallState` stayed `0`, `dumpsys audio` stayed
`MODE_NORMAL`), so the deep link is a shortcut to a screen, not a dial. The measured
distinction that survives is the one that matters for cost: `ACTION_CALL` — the intent that
actually places a call — does not reach AmberIT, so it still means the cellular line.

So the door is the screen, and the deep link is a shorter walk to it: **Amber-MCP's job stays
exactly one thing — turn AmberIT's UI into a handful of reliable commands** — but the dialpad
half of that UI no longer has to be reached by tapping twelve anonymous `ViewGroup` cells by
geometry. See `artifacts/SELECTORS.md`.

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

> **Not implemented, and not to be assumed by plan 2 (measured 2026-11-07).** There is no
> lock anywhere in the shipped tree: `server.py` holds one lazily-built `Driver` singleton
> (`get_driver()`), which stops one phone being *connected* twice, and that is not
> serialisation — two concurrent `status_tool()` coroutines interleave freely today, and an
> `await` inside a future `call()` flow would let another client's tap land between two of
> its own. The paragraph above states the intent; the code does not do it yet. The cost of
> building plan 2 on the assumption that it does is the exact state this section calls the
> worst possible one, reached by the multi-client configuration the four client configs
> invite (Claude Desktop, Opencode, Hermes and JARVIS are all pointed at the same server).

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
   If neither works, stop — with one of two sentences, because they are different facts and
   the original wording ("REDMI_PIN is not set") was the lie this branch shipped to its only
   user: *no PIN is configured*, or *this action does not unlock by PIN* (what a read-only
   `status` poll means when it passes `pin=None`).
   Both rungs have now been measured on the handset (2026-11-07): typing the PIN as keyevents
   unlocks this MIUI lock screen in one attempt, and `wm dismiss-keyguard` still does nothing,
   which is why the swipe is tried first. The swipe-only branch has not been exercised,
   because this phone has a PIN.
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

  > **Not implemented (measured 2026-11-07).** Nothing in the shipped tree issues
  > `force-stop`: the ladder's only recovery is rung 4's `monkey ... LAUNCHER`, which brings
  > a *healthy* app to the front and does nothing about a crashed one, a half-drawn dialog or
  > an app stuck on a sub-screen. Plan 2 must not write a flow whose safety depends on
  > something else cleaning up after it. Cost: a flow that dies mid-dial leaves the phone on
  > the failed screen, and the next `status()` call will report the dialog as "AmberIT is
  > showing" because the package is in the focus string.
- Every dial is appended to `E:\Amber-MCP\logs\calls.jsonl` (timestamp, target, number,
  verdict, raw signal samples). This is a record, not a permission check; nothing is blocked
  by it.

  > Also **not implemented** — there is no `logs/` directory and no writer in the tree.
- Timeouts bound every wait. No tool may hang an assistant's voice turn indefinitely; the
  ceiling is a config value per action, generous enough for the +18 s call-log case.
  This one is real: `config.ACTION_TIMEOUT_SECONDS = 10.0`, per adb action, with the reconnect
  deliberately outside the ceiling.

### What `status()` actually returns, against the table above

The shipped tool is a subset, and the gap is worth naming because the table reads as a
specification of the one tool this branch has:

| The table promises | The branch ships |
|---|---|
| which AmberIT screen is up | yes — the focused activity, shortened |
| registered or not | **no.** Registered-ness appears nowhere in the tree, though `ivAppRegisterStatus` was measured on the dashboard (`artifacts/SELECTORS.md`), so the read is available |
| call up or not, and its timer | **no.** And the timer cannot be read from the in-call screen at all: it exposes no view nodes to accessibility (`artifacts/signals/FINDINGS.md`) — duration has to come from the audio-mode span |
| "AmberIT ready, registered, balance ৳42." | reachability, the rung that failed, and the screen; never the PIN, and — since this review — never the device address either |

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
- AmberIT must not be battery-optimised away. Current Doze whitelist state is **still
  unverified** as of 2026-11-07: the probe returned nothing, which is a bad query, not a
  finding, and nothing since has re-run it. Cost if the app is in fact being optimised away:
  an IP call placed while the phone has dozed fails with no signal at all, and plan 2's
  `call()` cannot tell that from a wrong number — so the first live run of plan 2 should
  settle it with `dumpsys deviceidle whitelist | grep -i amberit` before anything else.

## Open items carried into the plan

1. **Selector discovery** — the real resource-ids and texts for dialpad, Call, End, search
   box and both scopes. Requires one session with the phone unlocked and the app driven.
   Nothing in `screens.py` may be invented before this.
   **Partly retired 2026-11-07:** the dashboard, the dialpad and the call-type chooser are
   captured in `artifacts/SELECTORS.md`, and the in-call screen is proven to have no
   selectors at all. Still open: the contacts-search screen with the AmberIT/All scope
   control visible — `search_button` opens chat search, which is not it, and the real entry
   point has not been located.
2. **Signal capture call** — as above, decides whether `status()` and outcome detection are
   possible at all. **Retired by one real IP call**, recorded in
   `artifacts/signals/FINDINGS.md`: Paid Call is the IP route (audio goes
   `MODE_IN_COMMUNICATION` while `mCallState` stays `0`), and answered versus ringing is
   **not** distinguishable on these signals, so plan 2 must return UNKNOWN for it.
3. **GitHub repo does not exist yet**, and `gh` is not installed, so nothing can be pushed
   to `github.com/gitakader/Amber-MCP` until either the repo is created on the web or
   `winget install GitHub.cli` is approved.
   **Superseded by fact: the repository exists, is public, and this branch has been pushed to
   it.** That is not a status note, it is the reason the redaction work in this plan is
   urgent rather than tidy: every claim below about what must never be committed is a claim
   about a tree that the world can already read.
4. **`balance()` and `history()` may not exist in the app at all.** Assumed from typical
   softphone layouts. If discovery finds no such screen, the tool is cut rather than faked.
   **Settled for `balance()`:** `tvBalance` is on the dashboard. `history()` is on the
   dashboard's list too (`name_tv` rows), but no such tool has been written.

## Residuals carried into plan 2

Found during execution, decided, and left unfixed on purpose. Plan 2 starts from this list
rather than rediscovering it. Each is a ruling, not a rumour: what was decided, why, and what
it costs if the decision was wrong.

1. **`type_text()` has no error path.** It calls `send_keys(text)` bare, so nothing scrubs or
   tests what an exception from the IME contains — and a uiautomator2 exception can quote the
   string it failed to type. Deferred because nothing in this branch types anything. It stops
   being harmless the moment `contacts_search` types a Bangla contact name. **Cost if wrong:**
   the first non-ASCII search can put a contact name into an assistant transcript.
2. **Two copies of one predicate.** `config.is_inside_project` and
   `discovery._refuse_the_repository` both decide "is this path inside the repo", and both
   docstrings claim they apply "the same test", with no pairing test. Behaviour is identical
   today. **Cost if wrong:** one drifts and an in-repo capture root is refused on one path and
   silently accepted on the other — which is the exact condition the raw exemption assumes
   cannot happen.
3. **The names gate reads only double-quoted `text=`/`content-desc=` in `.xml` under
   `artifacts/`.** A contact name pasted into a markdown file is caught by nothing. Same
   residual the phone-number rule already carries, and it is stated in the gate file rather
   than hidden. **Cost if wrong:** a hand-written note becomes the leak.
4. **`test_config.py`'s in-repo scratch test proves absence without cleaning a directory it
   creates.** Cosmetic; the tree is clean. **Cost if wrong:** a stray empty directory in a
   developer's tree.

## What plan 2 still needs from the handset

Neither of these can be substituted by reasoning, and plan 2 must not write code that pretends
otherwise:

- **The contacts-search AmberIT / All scope control was never located.** `search_button` opens
  `search_src_text` inside the dashboard and searches chat history, which is not it. Until the
  real control is captured, `contacts_search(query, scope)` has no selector to act on, and the
  scope-must-always-be-set-explicitly rule (the app remembers the last scope, so inheriting it
  silently searches the wrong list) cannot be implemented.
- **Answered versus ringing has one remaining candidate surface:** AmberIT's ongoing-call
  notification, which existed during the measured call (3 amberit notification lines, and a
  content-desc reading `End Call`). Settling it needs a second real call that the user answers.
  Until then the classifier returns UNKNOWN, which is the correct behaviour, not a gap to fill.
- **Whether `tvBalance` refreshes after a paid call**, and how fast. This decides whether
  `balance()` is trustworthy immediately after `call()`.

