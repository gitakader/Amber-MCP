# Call signals measured on the handset — 2026-11-07

One real IP call to the user's own nominated number, plus one invalid-number probe that
placed nothing. Every value here is a line read off the device; nothing is inferred.
Contact names, numbers, balances and notification prose are deliberately absent — see
"Publication rules" at the bottom, which is the reason this file is three tables long
rather than a dump.

## The three states, and what distinguishes them

| State | `mCurrentFocus` activity | `dumpsys audio` actual mode | `telephony.registry mCallState` | call-type dialog |
|---|---|---|---|---|
| dialpad open, number typed | `dialer.dialpad.DialPadActivity` | `MODE_NORMAL` | `0` | no |
| invalid number, `ivCall` pressed | `dialer.dialpad.DialPadActivity` | `MODE_NORMAL` | `0` | **no — it never opens** |
| IP call up | `com.reve.base.callrtc.ui.CallActivityWebRTC` | **`MODE_IN_COMMUNICATION`** | **`0`** | dismissed |
| IP call ended | left `CallActivityWebRTC` | back to `MODE_NORMAL` | `0` throughout | — |

Measured span of the real call: `IN_COMMUNICATION` at `05:00:06`, `MODE_NORMAL` at
`05:00:25` — **19 seconds, and the call was not answered.**

## The four questions the spec left open, answered

**1. Is "Paid Call" the IP line or the SIM?** — **IP.** During the call, audio went to
`MODE_IN_COMMUNICATION` while `mCallState` stayed `0` the entire time. A cellular call would
move `mCallState` to `1`/`2`. The activity name `CallActivityWebRTC` says the same thing.
This is why the app's three-way choice is safe to automate at all: none of the three rows
dials the SIM, so `call()` cannot silently start spending mobile minutes.

**2. Does `dumpsys audio` distinguish answered from ringing?** — **No.** The mode went up at
dial time, within ~2 seconds, while nobody had answered. `MODE_IN_COMMUNICATION` means "the IP
audio path is up", which is true for ringing *and* for answered. Reporting "they answered" from
this signal would be a fabrication. **Any plan-2 classifier must return UNKNOWN for
answered-versus-ringing on these inputs**, which is exactly the bucket the spec reserved.

**3. Does the system call log see an IP call?** — Not measurable as a *distinguishing* signal:
`content://call_log` holds tens of thousands of rows for this handset and the call I placed
cannot be located in it without matching on the number, which is what the log is for. Plan 2
must therefore **not** reuse Mark-LV's `latest_call_epoch()` proof mechanism: that works for
cellular because cellular writes there. Proof for an IP call is the activity name plus the audio
mode, and the app's own history list on the dashboard.

**4. Can the in-call screen be read through accessibility?** — **No.** A hierarchy dump taken
while `CallActivityWebRTC` had focus returned only `com.android.systemui` and status-bar nodes.
The call UI exposes no app view elements at all, so there is no `End` button, no timer and no
mute control to find. Consequences for plan 2:
- hang up with `KEYCODE_ENDCALL` (6), which is what ended this call, not a tap;
- call duration comes from the audio-mode span, not from reading a timer;
- "is a call up" is focus + audio mode, and that is the whole surface available.

## Where the cost decision actually lives

`ivCall` does not dial. It opens a **"Select call type"** dialog with three rows, all reported
`enabled=true`:

| Row | clickable ancestor centre |
|---|---|
| Free Audio Call | `(540,1048)` |
| Free Video Call | `(540,1175)` |
| Paid Call | `(540,1302)` |
| CANCEL | `(843,1438)` — genuinely clickable |

The user states that for an ordinary mobile number the choice is **Paid Call**; the "Free" rows
are the app-to-app paths. Tapping *Free Audio Call* on a mobile number did nothing at all: no
dialog advance, no audio request, no call, no charge — a silent no-op. That is a trap for any
automation that assumes a tap either works or errors.

## Publication rules this directory follows

- No hierarchy XML from a capture is committed. `redact.text()` scrubs numbers and money-shaped
  runs, but it has no model of **names**, and an in-call dump on this handset also carried
  notification prose from Facebook, WhatsApp Business, Telegram, imo and a Bangla payment app —
  none of which redaction can touch. A published screenshot or XML of this device is a publish
  of the user's private life, not just their contacts.
- Only ids, classes, bounds and these state lines travel. Scratch captures live outside the repo
  (`config.scratch_dir()`), which `capture()` now enforces by refusing an in-repo `out_dir`.
- `dumpsys notification --noredact` is issued because raw must show what the notification state
  really is, and is allowlisted out of the committed copy entirely.

## Still unknown, and what would settle it

- **Answered vs ringing.** The one surface that might separate them is AmberIT's own ongoing-call
  notification (3 amberit lines existed during the call, and a content-desc reading `End Call`
  appeared alongside it), whose text may change with state. Settling this needs a second call
  that the user answers, and reading only the notification's *shape*, never its prose, into raw.
  Cost: a few seconds of credit from a BDT 12.49 balance. **User's decision, not an assumption.**
- Whether `tvBalance` changes after a paid call, and how fast the app refreshes it — that decides
  whether `balance()` is trustworthy right after `call()`.
