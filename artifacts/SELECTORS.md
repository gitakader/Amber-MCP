# AmberIT selectors — captured from the device, 2026-11-07

Every row here was read off `com.amberit.dialer` v1.3.2 on the Redmi (Android 13, MIUI)
by `amber_mcp.discovery.capture()`. Nothing in this file is inferred. Where a value is a
contact name, a phone number or a balance it is **not written here at all**: `redact.text()`
scrubs numbers and money-shaped runs but has no model of names, so the captured hierarchy XML
stays in the scratch directory outside the repo and only ids, classes and bounds are published.

Activity names are the stable identity of a screen. Text labels are not — they translate, and
this build shows English labels today.

## Entry point: the dialpad accepts a deep link

The app does **not** handle `android.intent.action.CALL` (measured: `query-activities` for
ACTION_CALL with `tel:` data returns zero AmberIT matches, while two other handlers appear —
so the absence is a result, not a bad query). Direct-dial therefore always means the cellular
line.

It **does** handle `android.intent.action.DIAL` with the `tel:` scheme, via
`com.revesoft.itelmobiledialer.ui2.dashboard.TelIntentRouterActivity`. Measured behaviour of
firing that intent explicitly:

- focus became `...dialer.dialpad.DialPadActivity`
- `mCallState` stayed `0`, `dumpsys audio` stayed `MODE_NORMAL` — **no call was placed**
- the number arrived prefilled in `nvNumber`, **with the leading `+` stripped**

That last point is why the number read-back must compare digits, not the typed string.

```
am start -n com.amberit.dialer/com.revesoft.itelmobiledialer.ui2.dashboard.TelIntentRouterActivity \
     -a android.intent.action.DIAL -d 'tel:<number>'
```

This replaces typing digits into the keypad: the twelve keypad cells are anonymous `ViewGroup`s
with no resource-id and no label, so they are only reachable by geometry, which is exactly the
fragile thing the deep link avoids.

## Dashboard — `ui2.dashboard.AgniDashboardActivity`

| Element | id | class | bounds | note |
|---|---|---|---|---|
| balance | `tvBalance` | TextView | `[244,143][865,196]` | not clickable; the only place credit is shown |
| search button | `search_button` | — | `[948,104][1080,236]` | clickable; opens in-dashboard search |
| registration | `registration_icon` | ImageView | — | presence/state to be classified |
| chat filter | `tvIM` | TextView | `[796,428][841,482]` | container `imContainer` is clickable |
| missed filter | `tvSMS` | TextView | `[918,428][1047,482]` | container `smsContainer` is clickable |
| history row name | `name_tv` | TextView | `[187,539][937,600]` then every 176px | the call-history list lives on the dashboard |
| history row avatar | `ivProfileImage` | ImageView | `[33,537][165,669]` | clickable |
| pager | `chatListViewPager`, `viewPager` | ViewPager | — | |

`tvBalance` answers the spec's Open Item 4: **the balance is on the dashboard**, so `balance()`
is one read of a single view, not a screen walk.

## Dialpad — `dialer.dialpad.DialPadActivity`

| Element | id | class | bounds | centre |
|---|---|---|---|---|
| number field | `nvNumber` | EditText | `[187,921][893,1055]` | read-back before dialling |
| call | `ivCall` | FloatingActionButton | `[463,2059][617,2213]` | **540, 2136** |
| backspace | `ivBack` | ImageView | `[893,933][1047,1043]` | |
| add to contact | `ivAddToContact` | ImageView | `[33,933][187,1043]` | |
| keypad holder | `keyPadHolder` | LinearLayout | `[0,921][1080,2270]` | |
| keypad cells | *(none)* | ViewGroup x12 | 272px columns, 245px rows | anonymous — geometry only |

`ivCall` is the only dialling action on the screen, and it is a distinct id. That gives plan 2
a safe shape: deep-link, assert `nvNumber` holds the intended digits, then tap `ivCall` — with
the read-back between the two, so a mistargeted tap cannot dial something else.

## Search — still open

Tapping `search_button` opens `search_src_text` (`AutoCompleteTextView`, `[22,120][948,219]`,
with `search_close_btn` beside it) **inside the dashboard activity**, and its results are chat
conversations. That is not the contact search described by the user, which offers two scopes:

- **AmberIT** — only contacts who run an Amber IT service
- **All** — AmberIT plus the phone's normal contacts (a superset, so it is the default)

**The scope toggle has not been found yet.** The `tvIM`/`tvSMS` pair above is a chat/missed
filter, not this. Reaching the real contact search needs a hand on the phone.

## Signals measured, and what they can prove

From `dumpsys audio` on this build — the committed capture keeps only the two current-state
lines; the 20-line history stays in the raw copy, outside the repo:

```
- Requested mode = MODE_NORMAL
- Actual mode = MODE_NORMAL
```

The raw history is the more interesting discovery, because it is **attributed**:

```
11-06 19:57:07:446 setMode(MODE_IN_COMMUNICATION) from package=com.whatsapp ...
```

So an AmberIT IP call should appear as `setMode(MODE_IN_COMMUNICATION) from package=com.amberit.dialer`,
which is a better classifier input than "the mode changed" — but it is also why the history must
never be published: it records the user's WhatsApp and cellular call times.

`dumpsys telephony.registry | grep mCallState` answers `mCallState=0` twice and is the expected
**negative** for IP calls. It is cellular-only, so it cannot distinguish anything here.

## What is now proven on the handset, that was assumption before

1. uiautomator2's instrumentation server attaches on this MIUI build and returns a parsable tree.
2. `send_keys` via uiautomator2's IME types on this build, so non-ASCII is reachable (the
   `input text` route cannot do that, and the driver no longer uses it for text).
3. Typing the PIN as keyevents unlocks this MIUI lock screen — one attempt, `ready: True`, focus
   on `AgniDashboardActivity`. `wm dismiss-keyguard` still does nothing, which is why the ladder
   tries the swipe first.
4. `ACTION_DIAL` with `tel:` reaches AmberIT; `ACTION_CALL` does not.

## Still to capture before plan 2 can be written

- the contact-search screen **with the AmberIT / All scope control visible**, and both states
- the in-call screen: `ivCall`'s counterpart — End, plus Accept for an incoming call
- the three outcome signal sets (answered / ringing-no-answer / rejected-or-failed) from one
  real call, which needs the user's explicit go-ahead
