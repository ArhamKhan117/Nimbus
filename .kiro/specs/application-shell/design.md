# Design Document

## Overview

A frameless window, a navigation rail, five pages, and a hard rule: **the shell is a view**. There is
no `import app` anywhere in the package, every data source is an injected callable and every action is
an outbound signal. That is what makes the window constructible under pytest with no application, and
it is the same seam that keeps the pipeline from acquiring a user-interface dependency.

The interesting parts of this design are all places where the plan was measured and found wrong. Three
in particular:

- **Snap is not something an application implements.** It is something Windows does *to* a window
  during its own move loop, and only for a window that declares itself sizable. A frameless window is
  not, so the design hands both gestures back to the operating system rather than reimplementing them.
- **The page crossfade had to be deleted.** A graphics effect renders through an offscreen buffer, and
  the pages are full of the widgets that go wrong there. It left stale pixels from the previous page
  visible inside the new one.
- **The push-to-talk chord was pressing buttons.** Two individually correct decisions — a
  non-suppressing global hook, and a toolkit that activates buttons on Space regardless of modifiers —
  met badly, and the button holding focus on a freshly opened window was the one that turns Nimbus off.

The settings work is deliberately the least creative part of the feature. It is an extraction, not a
rewrite, and its acceptance criterion is that 41 existing tests keep passing untouched.

> Consolidated from `SHELL_AND_CHAT.md` §2 and §3 `S-1`–`S-5`, plus `IMPROVEMENTS.md` `T2-7` and
> `T4-7`.

## Architecture

```
shell/
    __init__.py      lazy __getattr__ → MainWindow      ← invisible to the static bundler
    window.py        MainWindow: title bar + rail + page stack + 8 grips + 2 Win32 calls
    nav.py           Sidebar, NavItem, the sliding marker, the status footer
    titlebar.py      TitleBar, GlyphButton (glyphs PAINTED, not typed)
    widgets.py       GrainOverlay, SidebarSwitch, StatusChip, StatusDot, PowerSwitch
    pages/
        home.py      power card · provider card · this-week card · recent table · privacy card
        knowledge.py per-app list, open folder, the seeded guide
        journal.py   due count, accuracy, quiz trigger
        settings.py  hosts SettingsForm + its own Save (ONE scroll area, from the form)
        account.py   licence, device, seats, deactivate, sign out, quit
theme.py             every colour, spacing step, duration and easing curve
```

```
                       ┌──────────────── MainWindow (a VIEW) ────────────────┐
 injected callables →  │  listening_provider   usage_provider                │
 (no import app)       │  hotkey_provider      privacy_provider              │  → em dash
                       │  recent_provider      chat_visible_provider         │    when absent
                       │  review_queue_provider  licence_provider            │
                       │  settings_form_factory  open_folder                 │
                       ├─────────────────────────────────────────────────────┤
                       │  TitleBar ── startSystemMove() ─────→ OS move loop  │
                       │  AccentRule (1px gradient widget, not a border)     │
                       │  ┌─────────┬───────────────────────────────────┐    │
                       │  │ Sidebar │  QStackedWidget                   │    │
                       │  │ 216px   │   home ─ QScrollArea ─ container  │    │
                       │  │ marker  │   knowledge ─ QScrollArea ─ …     │    │
                       │  │ slides  │   journal   ─ QScrollArea ─ …     │    │
                       │  │         │   settings  ─ container  ← NO extra    │
                       │  │ chat sw │   account   ─ QScrollArea ─ …     │    │
                       │  │ guard   │                                   │    │
                       │  └─────────┴───────────────────────────────────┘    │
                       │  GrainOverlay over the STACK only, mouse-transparent│
                       │  8 × _ResizeGrip ── startSystemResize(edge) → OS    │
                       │  QSizeGrip (visible affordance + no-native fallback)│
                       │  QShortcut(configured chord) → lambda: None         │
                       └──────────────────────┬──────────────────────────────┘
                                              │ signals, main thread only
        sig_set_listening ─────→ hotkey.set_enabled(on) + tts.stop() on pause
        sig_set_chat_visible ─→ NimbusApp.set_chat_visible
        sig_quit ─────────────→ ONE shutdown path, shared with the tray
        sig_hidden_to_tray ──→ tray balloon, once
        sig_local_data_cleared, sig_quiz_me, sig_export_history,
        sig_open_memory_folder, sig_deactivate_device, sig_sign_out

  showEvent → _enable_native_snap()  ── SetWindowLongW: |= WS_THICKFRAME
                                     │                     | WS_MAXIMIZEBOX
                                     │                     | WS_MINIMIZEBOX
                                     └── SetWindowPos(SWP_FRAMECHANGED)
              apply_minimum_size()   ← also on moveEvent (screen may differ)
              setFocus(window)       ← so PowerSwitch is NOT armed on open
  closeEvent → event.ignore(); hide(); sig_hidden_to_tray   ← NEVER quits
```

## Components and Interfaces

### `MainWindow` — the integration surface

Inbound, all callable from the main thread at any time:

```python
set_listening(on)                  set_provider(provider, model)
set_chat_visible(on)               set_local_mode(local, detail="")
set_privacy_guard(on)              show_page(name)
refresh()                          set_hotkey_capture_active(capturing)
```

Read-only accessors — `is_listening` and `is_chat_visible` — **read through to their providers and keep
no copy**. That is the mechanism behind "one source, three views": with a provider wired up, an inbound
set call is honoured only as a refresh, so a caller cannot make a view show something the source
disagrees with.

`show_page` ignores unknown names and swallows a page's refresh exception. Both are reachable from
signals, and a typo in a tray action must not be able to take the window down.

### Frameless behaviour: hand it to the OS

| Gesture | Mechanism |
|---|---|
| Move | `QWindow.startSystemMove()` from the title bar |
| Resize | `QWindow.startSystemResize(edge)` from one of eight grips |
| Maximise | Title-bar button and title-bar double-click |
| Snap | The OS, once the style bits are restored |

Measured styles:

```
ordinary window   GWL_STYLE = 0x96CF0000   THICKFRAME=True   CAPTION=True
frameless window  GWL_STYLE = 0x96000000   THICKFRAME=False  CAPTION=False
```

`0x96000000` is `WS_POPUP | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN` and nothing else. No
`WS_THICKFRAME` means not sizable, so the OS had nothing to snap; no `WS_MAXIMIZEBOX` means the top
edge cannot maximise. Dragging always worked, because `startSystemMove` posts into the OS move loop —
the loop ran, it just had no reason to offer a snap.

```python
GWL_STYLE      = -16
SNAP_STYLES    = WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX   # NOT WS_CAPTION
SWP_FLAGS      = SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED

def enable_snap_styles(hwnd: int) -> bool: ...   # module level, testable without a window
```

Three details in that function each fix a specific silent failure. **Explicit `argtypes`**, because an
undeclared `HWND` is marshalled as a C `int`, truncating a 64-bit handle so the call fails against a
handle that does not exist. **An unsigned `restype`**, because the real value is `0x96000000` and a
signed read makes it negative. **A read-back rather than trusting the return**, because `SetWindowLongW`
returns the *previous* style, so a legitimate call can return zero and a failed one cannot be
distinguished without `GetLastError`.

`SWP_FRAMECHANGED` is the point of the second call: without it Windows does not re-ask `WM_NCCALCSIZE`
and the new style has no visible effect until the next resize.

**Suppressing the returning frame turned out to be unnecessary.** The textbook answer is to intercept
`WM_NCCALCSIZE` and leave the client rectangle equal to the window rectangle. Measured with the styles
restored and no message handling at all: `GetClientRect` and `GetWindowRect` both report 400×300 on a
test window, and maximising lands exactly on `availableGeometry` rather than over the taskbar. Qt
already answers that message. `TestAeroSnap` pins the measurement, so a future Qt that stops doing it
is a failing test rather than a returning frame.

**Recorded dead end.** A `nativeEvent` override was written first. Calling `super().nativeEvent(...)`
from PyQt6 crashes the process with an access violation on the first message the window receives. If a
handler is ever genuinely needed, return `(False, 0)` for the unhandled case rather than delegating.

### `_ResizeGrip` — why eight widgets

```python
CORNER_SIZE   = 16              # deliberately larger than the visible bezel
RESIZE_MARGIN = theme.SPACE[0]  # 4px of window showing, which doubles as a bezel
```

Eight children: four 16×16 corners, four thin edges spanning between them. Each owns its edge and its
cursor, is transparent to painting but not to the mouse, and is hidden while maximised.

The previous implementation was `mouseMoveEvent` calling `self.setCursor(...)` on the window, and it
produced "my cursor turns into a resize arrow and stays like that". `setCursor` on a parent applies to
every child that has not set its own, so the resize cursor was inherited by all the cards and labels.
Clearing it needed another move event over the *window* — and a move from a 4px gutter into the content
lands on a child, so the window never saw the pointer leave. One brush past an edge left every page with
a resize cursor until the pointer happened to cross the gutter again.

Per-widget cursors are deterministic: Qt sets on enter and restores on leave, with no state of ours
involved. The 16px corners also fix the same problem the chat panel had, where two 5px strips crossing
left a 5×5 corner nobody could hit.

### Sizing

```python
MIN_WIDTH, MIN_HEIGHT      = 760, 480     # lowered from 1040×680
MIN_SCREEN_FRACTION        = 0.9
OPEN_WIDTH, OPEN_HEIGHT    = 1240, 780
SCREEN_FRACTION            = 0.88
```

Both the floor and the opening size clamp against `availableGeometry`, which already excludes the
taskbar. The clamp is not defensive noise: at 250% scaling a 1920×1080 panel reports 768×432 logical
pixels, *below* the old floor, so the window would have opened unable to fit on its own screen and
unable to shrink. `apply_minimum_size` re-runs on `moveEvent`, so a floor measured on a 4K panel does
not follow the window onto a 1366×768 laptop.

The floor came down because each page moved into its own scroll area. Measured before that change:
`layout().minimumSize()` was 810×646 while `setMinimumSize` said 1040×680 — the explicit floor was 230
px wider and 34 px taller than anything the layout needed, so the user was being stopped by a constant.
Of the 646, the Home page alone accounted for 549 px with no way to give less. Scrolling makes "too
small" recoverable rather than a hard stop.

**Settings is the exception**, and the exception is load-bearing. `SettingsForm` deliberately contains
no scroll area and no button box; its host supplies both, with Save *outside* the scrolling region.
Wrapping it again in the shell would nest one scroll region inside another and put Save back below the
fold, which is what `test_settings_page_has_exactly_one_scroll_area` exists to prevent.

The scroll areas carry `NoFocus`. Measured after adding them: Tab from the nav rail landed on the
`QScrollArea` — a page-sized container with nothing to do — and with Windows' keyboard cues on, it drew
a focus frame around the whole page.

### The hotkey guard

```python
guard = QShortcut(QKeySequence(parse_hotkey(configured_chord).display), self)
guard.setContext(Qt.ShortcutContext.WindowShortcut)
guard.setAutoRepeat(False)
guard.activated.connect(lambda: None)          # deliberately does nothing
```

Reported as *"when I press Ctrl+Alt+Space the push-to-talk listens and then pauses"*. Two correct
decisions meeting badly: the global hook is deliberately `suppress=False`, because pynput's flag is
all-or-nothing and `True` blocks every key on the system, so the chord reaches the focused widget as
well as Nimbus; and `QAbstractButton::keyPressEvent` activates on `Key_Space` **without looking at
modifiers**, so a focused button treats the chord as a click.

All three measured consequences were real. With the power switch focused, the chord emitted
`sig_set_listening(False)` — pausing Nimbus at the moment the user asked it to listen. With "Open memory
folder" focused it opened Explorer. With a nav item focused it changed page. And `focusWidget()` on
activation *was* the `PowerSwitch`, so this fired on the very first question after opening the window.

A `QShortcut` rather than an application-wide event filter, because Qt's shortcut map runs *before* a
key event is delivered to the focus widget — the only place this can be stopped cleanly. The slot does
nothing on purpose: the global hook already handles the chord and this window must not become a second
push-to-talk path. Built from the **configured** chord, so a user who remapped push-to-talk gets the
same protection; on a parse failure it falls back to guarding the default rather than giving up.

Two consequences handled. `set_hotkey_capture_active` lifts the guard while Settings is recording, so
the user can re-record the chord they are already bound to. And the window claims focus for itself in
both the constructor (`StrongFocus`) and `showEvent` (`setFocus`) — the policy alone was measured
insufficient, because Qt still handed focus to the first tab-chain widget on activation.

### `Sidebar`

```python
NAV_ITEMS = (("home", "Home"), ("knowledge", "Knowledge"), ("journal", "Journal"),
             ("settings", "Settings"), ("account", "Account"))
SIDEBAR_WIDTH = 216
```

One list. The rail builds its buttons from it and `MainWindow` builds its page stack from it, so a nav
item without a page is impossible by construction rather than by vigilance —
`test_every_nav_item_maps_to_a_page`. Each `NavItem` carries its own `page_name`, so nothing maps an
index back to a page; index lookups are what break when someone reorders the list.

`sig_page_requested` fires on a user click only; `select()` is the silent programmatic path, so a page
change cannot echo back into another page change.

The footer carries one chip, not a list of dots. The provider is already named on Home where there is
room to say the model too. The privacy chip's **label never changes** — only the dot's colour does —
because a control whose text changes also changes width, and a rail that reflows on every settings
change is its own small twitch. Red rather than amber when off: with the guard off, every question sends
a screenshot of whatever is in front, including a password manager, and that is the one thing in this
interface worth being blunt about. The tooltip says what it means and where to change it, so it informs
rather than nags.

`NAV_SIDE` defaults to left. The brief asked for the right; the disagreement is settled by one setting
value, and anything other than `"right"` resolves to left so an unrecognised value cannot produce a
third layout. The divider hairline goes on the edge facing the content, so moving the rail does not
leave a border floating at the window edge.

### `TitleBar` and `GlyphButton`

The title bar emits `sig_minimise`, `sig_maximise_toggled` and `sig_close`, and deliberately does not
call `parent().close()` itself — `MainWindow.closeEvent` hides to tray, and a title bar reaching past
the window to kill it would bypass that.

Glyphs are **painted**. The maximise glyph was `\u2b1c` WHITE LARGE SQUARE, which Segoe UI renders as a
*filled* white block — a solid white chip in the title bar. Substitutes have the same class of problem
on some fallback: `\u25a1` is too small against a 15pt wordmark, `\u2610` is a ballot box with its own
metrics and baseline. Two strokes and a rectangle outline cost less than picking a font-safe character
and look identical on every machine. It stays a `QPushButton`, so the whole `#WindowButton` stylesheet
still applies to the background; only the glyph is ours.

Window buttons are 32×24 inset chips with a border and a resting background rather than full-height
transparent hit zones. The transparent version was the original design, was close to invisible against
a near-black title bar, and was the first thing anyone remarked on.

Two optical corrections, both recorded because they look like arbitrary magic numbers otherwise. The
wordmark is nudged down by the font's **descent**, because a `QLabel` centres its line box while
"NIMBUS" is all caps with no descenders, so the cap heights — which is what the eye compares — did not
line up. And the gap between the mark and the wordmark is one step tighter than the layout default,
because the trimmed artwork carries no side bearing and anything wider read as two unrelated elements.

`titlebar_qss()` is deliberately empty and kept only so the window's stylesheet composition is
unchanged. The styling moved into `theme.build_qss` so that two stylesheets cannot both claim a say over
`#WindowButton` — which is how the close button ends up a different red from `DANGER`.

### Why there is no page crossfade

The design system asks for a 160 ms crossfade, and this implemented it with a `QGraphicsOpacityEffect`
on the stack. **Removed after seeing it on real hardware.** A `QGraphicsEffect` renders its target into
an offscreen buffer, and the pages contain exactly the widgets that go wrong there — `QScrollArea`s and
`QTableWidget`s with transparent viewports. Stale pixels from the *previous* page were visible inside
the new one for the fade's duration, worst on the Knowledge page where the table occupies most of the
card. A transition whose whole job is to feel smooth cannot leave visible tearing.

Alternatives rejected: painting every viewport opaque defeats the card gradient showing through, and
animating a real overlay widget is a lot of machinery for 160 ms. The `animate` parameter stays in
`show_page`'s signature so a caller can still ask for a silent switch if a transition is reintroduced.
The stack gets an explicit opaque fill rather than `transparent`, which is what stops the grain overlay
and any half-painted child leaving remnants — the same class of artefact that retired the fade.

### `SettingsForm` and its two hosts

```python
class SettingsForm(QWidget):        # content ONLY: no QScrollArea, no button box
    sig_validity_changed = pyqtSignal(bool)
    sig_local_data_cleared = pyqtSignal()
    sig_saved = pyqtSignal()
    def save(self) -> bool: ...     # False → nothing written, host must NOT close
    local_data_cleared: bool
```

Extracted from the dialog's builder as a **pure refactor**. `sig_validity_changed` replaces a direct
poke at the dialog's button box, which only worked because the dialog owned both. `save()` returning
`False` covers an invalid hotkey and a declined compatibility warning; the host must not close on
either.

The dialog measured 742–744 px of content, 783 with the frame, against 728 usable on a 1366×768 laptop
— and it is **modal at first launch**, so setup could not have been completed at all. Scrolling alone
was insufficient, because a scrollable dialog opens at its *minimum*, measured at about 111 px. So
`_size_to_screen` asks the **page** for its natural height and clamps to 88% of the screen; asking the
dialog's own layout returned 426 px, because a `QScrollArea` reports its own small hint rather than its
child's.

### Restart labelling

```python
RESTART_REQUIRED_SETTINGS: frozenset[str]     # ~30 entries, API keys deliberately absent
RESTART_MARKER = " \u27f3"                    # CLOCKWISE GAPPED CIRCLE ARROW
RESTART_NOTE   = f"Changes to settings marked{RESTART_MARKER} take effect the next time…"
def restart_marker_for(setting: str) -> str: ...
```

The note is **built from** the marker constant, so the legend cannot end up explaining a symbol the
labels no longer use. The lookup is a pure function so labelling is testable without constructing the
dialog and a setting cannot be marked inconsistently in two places.

The caching is deliberate and the list exists because of it: `resolve_setting` writes to the credential
store whenever a value came from the environment, so re-resolving per interaction would put a Credential
Manager write on the hottest path in the application. Removing the cache would be the wrong fix, so the
minimum viable version is honesty. API keys are absent because they are read per request.

**The glyph was chosen by measurement**, comparing ink height against the surrounding text's cap height
at 10 / 11 / 15 pt:

| Codepoint | Description | Ratio at 10/11/15pt | Ink at 11pt |
|---|---|---|---|
| `U+21BB` | circular arrow, thin | 0.89 / 0.82 / 0.93 | 28 px |
| `U+27F3` | circular arrow, gapped | 0.89 / 0.82 / 0.86 | **36 px** |
| `U+E72C` | Refresh, icon font | 1.44 / 1.36 / 1.43 | 65 px |
| `U+2192` | rightwards arrow | 0.44 / 0.45 / 0.36 | 15 px |

`U+21BB` shipped first and was reported as pixelated. `U+2192` is crisp but a straight arrow does not
say "reloads on next start". `U+E72C` is the right *shape* and was reported as too big — correctly: an
icon font is drawn to fill the em box while a text character's capitals occupy roughly 70% of it, so any
icon glyph inline is about 40% larger than the letters beside it. There is no way to shrink one run of a
plain-text label, and `QCheckBox` — which carries nine of these markers — does not support rich text.

`U+27F3` resolves it: the circular shape, at text scale, with 29% more ink than `U+21BB` at the same
size because the gapped form uses fewer, heavier strokes. That weight is what the "pixelated" complaint
was really about — a thin open circle at 8 px has almost nothing to render. Verified rather than
assumed: 36 px of ink against 52 px for a guaranteed-notdef codepoint, so it is a real glyph and not a
box; and its tight-rect bottom sits at the baseline against a 3 px descent, so it does not clip — which
is what the original "cut off at the bottom" was.

### `clear_local_nimbus_data`

Clears the *contents* of the data root and the knowledge folder while preserving the folders, so a
running process can recreate a database or diagnostics folder cleanly. Returns a list of failures rather
than raising, so a partial result is still useful and reportable. Never follows symbolic links. Deletes
the enumerated local credential-store entries, treating a missing entry or a locked store as non-fatal.
User-created exports are excluded — they are explicit documents, not application state. The privacy
guard's entries are included precisely so that a wipe restores the **on** default rather than leaving
the guard off from a previous session.

## Data Models

The shell holds no persistent state of its own. Everything it displays comes from a provider, and
everything it changes goes through a signal. Its own configuration:

| Setting | Default | Read | Restart-gated |
|---|---|---|---|
| `SHELL_ON_STARTUP` | `on` | `should_open_on_startup()` | yes |
| `NAV_SIDE` | `left` | at construction | yes, in practice |
| `REDUCE_MOTION` | `auto` | by `theme` at construction | yes, in practice |
| `HOTKEY` | `ctrl+alt+space` | for the guard | yes |

`SHELL_ON_STARTUP` defaults on, and an unreadable configuration also opens the window. Nothing starts
Nimbus at login — the installer writes no run key and no startup shortcut — so every launch is a person
double-clicking a shortcut, and the only useful answer to that is to appear. Failing towards *invisible*
would turn a keyring hiccup into "I clicked Nimbus and nothing happened", which is the complaint this
default exists to remove.

Geometry constants live in the module rather than in configuration, because they are measurements
against the screen rather than preferences.

## Correctness Properties

### Property 1: The window is constructible with nothing

For any subset of providers omitted — including all of them — the window constructs, shows every page,
and navigates between them without raising. Generator: the power set of the provider arguments.

**Validates: Requirements 1.1, 1.3**

### Property 2: The shell never imports the application

Static analysis of every module under the package finds no import of the application module, and no
import of the shell or chat modules inside the pipeline worker. Asserted as a test rather than a
convention.

**Validates: Requirements 1.2, 1.5**

### Property 3: Every lazily imported module is registered twice

For every module in the package, the module appears in both the frozen-build hidden-import list and the
selftest's runtime module list. The package's lazy attribute hook is invisible to the static bundler,
which is exactly the gap that has caught modules before.

**Validates: Requirements 1.8**

### Property 4: The snap style call is total

For any handle — valid, zero, truncated, or on a non-Windows platform — the call returns a boolean and
never raises. Where it returns true, the style word read back contains all three bits and not the
caption bit.

**Validates: Requirements 2.6, 2.7, 2.11, 2.12**

### Property 5: Client and window rectangles agree after the styles are restored

With the sizing style restored and no message handling, the client rectangle equals the window
rectangle, and maximising lands exactly on the available geometry rather than over the taskbar. This
pins a measured toolkit behaviour so that its loss is a failing test rather than a returning frame.

**Validates: Requirements 2.9**

### Property 6: The eight grips tile the border exactly

For any window size at or above the minimum, the grips' rectangles lie entirely within the window,
overlap nowhere, cover every edge and corner of the border region, and each corner target is at least
the corner size square. For a maximised window, all eight are hidden.

**Validates: Requirements 3.1, 3.5, 3.7**

### Property 7: No shell code converts a coordinate or caches a scale factor

Static analysis finds no device-pixel-ratio read stored in an attribute anywhere in the package, and no
physical-to-logical conversion. The operating system owns both gestures, so there is nothing here to get
wrong when the window is dragged between monitors at different scaling.

**Validates: Requirements 2.4**

### Property 8: The minimum size always fits the screen

For any screen geometry, the computed minimum is no larger than the configured fraction of the available
width and height, and never larger than the constants. Generator: geometries down to the smallest
logical size a heavily scaled panel reports.

**Validates: Requirements 4.2, 4.3**

### Property 9: The opening size always leaves visible desktop

For any screen geometry, the opening size is no larger than the configured fraction of the available
geometry in both axes.

**Validates: Requirements 4.1**

### Property 10: Exactly one scroll area per page host

For every page, the host contains exactly one scroll area, except the settings page whose host contains
none because the form brings its own. No host contains a scroll area inside a scroll area.

**Validates: Requirements 4.5, 4.8, 12.9**

### Property 11: Closing never quits

For any close attempt from any source — the title-bar button, the system menu, a programmatic close —
the window is hidden, the event is ignored, the hidden signal is emitted exactly once per close, and no
quit signal is emitted.

**Validates: Requirements 5.1, 5.2, 5.4**

### Property 12: The chord activates nothing

For any focused widget in the window — every button, every switch, every navigation item — delivering
the configured chord changes no state, emits no signal and opens nothing. Generator: the chord against
each focusable widget in turn, plus the same test with a remapped chord.

**Validates: Requirements 6.1, 6.2, 6.3, 6.7**

### Property 13: No control is armed on open

After showing the window, the focus widget is the window itself rather than any control. Asserted
directly, because the failure mode is that the first chord press pauses Nimbus.

**Validates: Requirements 6.11, 6.12**

### Property 14: The guard survives an unparseable chord

For any chord string, including malformed and empty ones, construction completes and a guard is
installed on either the parsed chord or the default. The guard is never absent.

**Validates: Requirements 6.8**

### Property 15: Every navigation item maps to a page, and vice versa

The set of navigation page names equals the set of page-stack keys exactly. Neither direction may have
an extra.

**Validates: Requirements 7.1, 7.2, 7.3**

### Property 16: Programmatic selection is silent

For any page name, the programmatic selection path emits no page request. Only a user click does, so a
page change cannot echo into another page change.

**Validates: Requirements 7.5**

### Property 17: Navigation is total

For any string, including unknown page names, navigation returns without raising. For a page whose
refresh raises, the page still becomes current and the rail still updates.

**Validates: Requirements 7.6, 7.7**

### Property 18: The navigation side has exactly two outcomes

For any configured value, the resolved side is one of two values, and anything unrecognised resolves to
the default. The divider is always on the edge facing the content.

**Validates: Requirements 7.8, 7.9, 7.10**

### Property 19: An unmeasured number is never zero

For any provider that is absent or raises, the rendered value is the em dash placeholder. No code path
renders a literal zero for an unmeasured quantity.

**Validates: Requirements 9.1, 9.2, 9.4**

### Property 20: The window keeps no copy of the listening state

For any provider value, the window's accessor returns what the provider returns, including after an
inbound set call with the opposite value. With a provider wired up, the source always wins.

**Validates: Requirements 10.4, 10.5, 10.6**

### Property 21: Only one path writes the listening state

Static analysis finds exactly one assignment to the hotkey listener's enabled flag, reached only from
the application's setter. Neither the window nor the tray writes it.

**Validates: Requirements 10.1, 10.2**

### Property 22: Every pre-existing settings test still passes

The full pre-refactor settings test file passes unmodified against the extracted widget. This is the
extraction's acceptance criterion, not a nice-to-have.

**Validates: Requirements 11.1, 11.2**

### Property 23: A failed save never closes the host

For any save returning false — invalid hotkey, declined compatibility warning — the host remains open
and nothing is written to the credential store.

**Validates: Requirements 11.7**

### Property 24: The form fits every tested screen height

For each of several common screen heights, the hosted dialog's total height including its frame is no
greater than the usable height, and the save action's rectangle lies within the visible area.

**Validates: Requirements 12.1, 12.2, 12.3, 12.7**

### Property 25: The control inventory is complete

The set of controls present in the extracted widget equals the recorded inventory exactly, so the
refactor cannot silently drop a widget.

**Validates: Requirements 12.8**

### Property 26: Restart marking is consistent and explained

For every setting in the restart set, its label carries the marker; for every setting not in it, the
label does not. The explanatory note contains the marker character, and that character is taken from
the same constant the labels use.

**Validates: Requirements 13.1, 13.2, 13.4, 13.5, 13.6**

### Property 27: The marker glyph is real and does not clip

The marker's rendered ink is materially greater than a codepoint guaranteed to be absent, and its tight
bounding rectangle's bottom does not extend past the baseline by more than the font's descent.

**Validates: Requirements 13.11**

### Property 28: Clearing is scoped, total and reporting

For any data tree, clearing empties both roots' contents, leaves both roots present, follows no symbolic
link, removes no file outside those roots, returns a list rather than raising on any permission error,
and leaves the privacy guard reading its on default afterwards.

**Validates: Requirements 14.1, 14.4, 14.5, 14.6**

### Property 29: Reduced motion still completes

For a zero-duration animation, the completion signal still fires, so the cleanup hanging off it runs.
Asserted because a silently non-firing signal would break the reduced-motion path rather than merely
skipping the animation.

**Validates: Requirements 16.2, 16.3**

### Property 30: No effect is applied to a page host

Static analysis finds no graphics effect set on the page stack or any page host, and the stack's fill is
opaque rather than transparent. This is the crossfade staying deleted.

**Validates: Requirements 16.4, 16.6, 16.7**

### Property 31: The texture overlay swallows nothing

For any control in the content area, hit-testing its centre resolves to the control rather than to the
overlay. The overlay's rectangle equals the page stack's rectangle and never covers the chrome.

**Validates: Requirements 16.12, 16.13**

### Property 32: No literal colour in the generated stylesheet

The generated stylesheet contains no hex or functional colour literal that is not traceable to a theme
constant.

**Validates: Requirements 16.14**

### Property 33: Resolution order is exactly environment, store, default

For any combination of an environment value, a stored value and a declared default — including any of
them absent — the resolved value is the first present in that order. Generator: the eight presence
combinations across string, boolean and integer settings.

**Validates: Requirements 17.1**

### Property 34: A value from the environment is written through exactly once

For any setting resolved from the environment, the credential store afterwards holds that value, and a
second resolution performs no further write. For a value that came from the store or the default, no
write occurs at all.

**Validates: Requirements 17.2**

### Property 35: Resolution never raises and never invents

For any failure of the credential store — absent, locked, raising on read, returning a wrong type — the
resolver returns the declared default and does not raise. The value returned is never one that was
neither configured nor declared.

**Validates: Requirements 17.6, 17.7**

### Property 36: Only one function resolves a setting

Static analysis finds no module reading a setting other than through the resolver, and no cached
device- or store-derived value assigned outside it. Asserted structurally, because a second read path
would make the order in Property 33 true of one caller and false of another.

**Validates: Requirements 17.1**

### Property 37: The API-key path is not cached

For any key, two resolutions after an intervening change return the new value without a reload.
Asserted directly, because the whole point of excluding keys from the restart set is that a newly
entered key works immediately.

**Validates: Requirements 17.5**

### Property 38: The restart set and the live-reloadable set are disjoint

No setting appears in both. Asserted as a test rather than reviewed, because a setting marked as
needing a restart while also being reloaded live would show the user a marker that lies.

**Validates: Requirements 18.6**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| No provider supplied | Render the em dash placeholder | An unmeasured zero is a false claim |
| A provider raises | Placeholder, swallow | A data source must not break navigation |
| A page's refresh raises | Complete the navigation anyway | Navigation is reachable from a signal |
| Unknown page name | Ignore | A typo in a tray action must not take the window down |
| `winId()` unavailable | Skip snap styles | Under pytest the window is never shown |
| Snap style call fails | No snap | Better than a window that will not open |
| Unparseable hotkey | Guard the default chord | The default is still worth guarding |
| `startSystemResize` unavailable | Fall through to the size grip | No native handle under pytest |
| `startSystemMove` unavailable | Fall through to the base handler | Same |
| Window icon missing | Continue without it | A dev install without assets must still run |
| Unreadable configuration at startup | Open the window | Failing towards invisible is the worse failure |
| Save returns false | Host stays open | Nothing was written, so closing would lose the edit |
| Local data clearing partially fails | Return the failure list | A partial result is still useful |
| Credential entry missing or store locked | Non-fatal, continue | The filesystem result still matters |
| Marker glyph absent from the font | Caught by the ink-height test | A notdef box is worse than no marker |
| Layout not yet run when selecting | Jump the marker rather than animate | Otherwise it slides in from the corner |

## Testing Strategy

The shell is testable without a shown window, which is the whole point of the injection seam.
`tests/test_shell.py`, `tests/test_theme.py`, and the pre-existing `tests/test_settings_dialog.py`
passing **unmodified**.

- **The extraction's acceptance criterion is a test suite, not a review.** 41 pre-existing settings
  tests pass untouched against the extracted widget. If any needed editing, the extraction was a
  rewrite.
- **Measured behaviour is pinned, not trusted.** `TestAeroSnap` asserts the style word and the
  client-versus-window rectangle equality, so a future Qt that stops answering the frame message fails
  a test instead of shipping a visible frame. `TestResizeGrips` asserts grip geometry against the code
  that actually runs — the previous tests referenced a hit-test helper that had already been deleted,
  which is how dead code survives a refactor.
- **Constant drift guards.** The style bit set, the spacing scale, the durations. Same technique as the
  overlay's click-through bit pattern: assert both the value and the expression that produces it.
- **Intent guards — the ones worth having here.** `test_every_nav_item_maps_to_a_page` makes a dead
  link impossible. `test_qss_references_no_literal_colours` keeps every colour in one module.
  `test_settings_page_has_exactly_one_scroll_area` stops Save falling below the fold on a laptop.
  `TestNoEmojiInTheUi` walks the AST over every string literal. And the restart-label coverage test
  asserts both directions of the marker.
- **`TestSettingsFitsSmallScreens`** parametrises across 768 / 900 / 1080 / 1440 and asserts the save
  action is on screen at each, plus an inventory test so the refactor cannot silently drop a widget.
- **The hotkey guard is tested against every focusable widget**, not just the power switch. The bug was
  found on one control and would have recurred on the next one added.
- **Accessibility is measured, not assumed.** `contrast_ratio` is a pure function and every text-on-
  surface pair is asserted against the 4.5:1 requirement. That check caught the muted text colour at
  3.49:1.
- **Manual smoke tests, required and not automatable.** Drag the frameless window, snap it to each
  edge, resize from all eight regions, maximise, restore. Drag it between two monitors at different
  scaling and confirm nothing jumps. Open the window and immediately press the push-to-talk chord —
  Nimbus must listen, not pause. Close the window and confirm push-to-talk still works and the tray
  balloon appeared once. Open Settings on a 1366×768 display and confirm Save is reachable.
