# Nimbus — Application Shell & Chat HUD

> **Design and implementation plan for two features:**
> **A.** A real windowed application, licence-gated, with everything Nimbus does in one place.
> **B.** A floating chat panel pinned to the top of the screen showing the live conversation.
>
> **Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[-]` rejected/deferred
> **Verification legend:** `⚠ VERIFY` = confirm against live sources *immediately before*
> implementing. Do not code from the assumption written here.
>
> **Baseline:** 1,252 tests passing · `--selftest` OK · Tiers 0–3 complete
> (see `IMPROVEMENTS.md`). This document is a sibling to that one and follows its conventions.
>
> **Scale warning.** Together these are larger than any single tier in `IMPROVEMENTS.md`.
> `S-1` alone touches licensing, packaging, a new UI framework layer, and the app's entry
> point. §9 phases the work so each phase ships something usable and testable on its own.
> Attempting it in one pass is how this becomes a six-week branch that never merges.

---

## Table of contents

- [0. Strategic decisions — READ FIRST](#0-strategic-decisions--read-first)
- [1. What verification already established](#1-what-verification-already-established)
- [2. Design system](#2-design-system)
  - [2.5 Elevation and shading — how dark UI stops looking flat](#25-elevation-and-shading--how-dark-ui-stops-looking-flat)
  - [2.6 Motion](#26-motion)
  - [2.7 Component recipes](#27-component-recipes)
- [3. Part A — the application shell](#3-part-a--the-application-shell)
- [4. Part B — the chat HUD](#4-part-b--the-chat-hud)
  - [S-6b Interactions worth having](#-s-6b--interactions-worth-having)
- [5. Licensing and activation](#5-licensing-and-activation)
- [6. Threading model](#6-threading-model)
- [7. Invariants that must not break](#7-invariants-that-must-not-break)
- [8. Test plan](#8-test-plan)
- [9. Phased rollout](#9-phased-rollout)
  - [9.1 Running two agents in parallel](#91-running-two-agents-in-parallel)
- [10. Decisions needed from the maintainer](#10-decisions-needed-from-the-maintainer)
- [11. Non-goals](#11-non-goals)

---

## 0. Strategic decisions — READ FIRST

### 0.1 The honest problem with "only people who buy have access"

This needs saying before any code is written, because the rest of the licensing design
depends on accepting it:

> **A local desktop application cannot enforce a licence. It can only deter casual sharing.**

Nimbus is a Python application distributed as a PyInstaller bundle. That bundle can be
unpacked, the bytecode decompiled, and a licence check patched out. This is not a flaw in the
implementation plan — it is true of every locally-executing application, including ones from
companies with large security teams.

The reason it bites harder here is architectural. Payment gating is genuinely enforceable when
the gated value lives on **your** server. Nimbus's value lives entirely on the **user's**
machine: their API key, their model calls, their screen. There is nothing on a server to
withhold.

So there are three real options, and only one of them is consistent with what Nimbus already
is:

| Option | Genuinely enforceable? | Cost |
|---|---|---|
| **A. Signed offline licence file** | No — deterrence only | Low. No server. |
| **B. Online activation + device binding** ← **recommended** | No — deterrence, but abuse is *visible* and revocable | Low-medium. Needs a licence service. |
| **C. Server-proxied inference** | **Yes** | Destroys BYOK, adds per-user cost, latency, and a privacy liability |

**Option C contradicts an explicit non-goal.** `IMPROVEMENTS.md` §8 lists *"Server-side key
proxy"* as a recorded non-goal, on the grounds that BYOK plus DPAPI keyring storage is a
deliberate differentiator and the thing that makes the fully-local path possible. Choosing C
means reversing that decision knowingly, and it also means Nimbus can no longer claim
"nothing leaves your machine" on the local path.

**Recommendation: Option B**, designed so that a crack is *possible but pointless* rather than
prevented. See §5.

### 0.2 What gets reused, and what must not be rewritten

The single biggest risk in `S-1` is treating it as a fresh start. It is not. There are 1,252
tests protecting behaviour that has been debugged the hard way — three coordinate spaces, a
DPI-aware overlay, cancel semantics across eleven checkpoints, a privacy gate at the capture
choke point.

| Existing | Fate under this plan |
|---|---|
| `app.py` `NimbusApp` orchestrator | **Unchanged.** The shell is a *view*; the pipeline stays exactly as it is |
| `settings_dialog.py` | **Re-hosted, not rewritten.** Its widgets become a page inside the shell (§3.5) |
| `overlay.py` | **Kept.** The pointer/annotation overlay is a different thing from the chat HUD (§4.0) |
| `tray.py` | **Kept, demoted.** The tray stays as the always-running surface; the window becomes the primary one (§3.6) |
| `hotkey.py`, `capture.py`, `ai.py`, `kb.py`, `memory.py`, `review.py`, `privacy.py` | **Untouched** |

> **Rule for this document, same as `IMPROVEMENTS.md` §1.3:** no change here may alter the
> behaviour of the push-to-talk pipeline. If a shell change requires touching
> `_pipeline_worker`, stop and reconsider the design.

### 0.3 Two recommendations that differ from the brief

Stated up front rather than buried, because they are choices to accept or reject.

**1. Navigation on the left, not the right.** The brief asks for a right sidebar. I would put
it on the left, because every desktop app the user already uses puts primary navigation there,
and Western reading order makes the left edge the cheapest place to scan. A right rail is
conventionally for *contextual* content — properties, inspectors, activity feeds. The
implementation makes this a single constant (`NAV_SIDE`), so it costs nothing to disagree.

**2. The reference screenshot uses a top nav bar, not a sidebar.** The Lunar screenshot puts
Dashboard / Positions / Tasks / Wallets / Settings across the top. If the goal is to match
that layout, say so — the design below uses a sidebar as asked, but the visual language
(dark surface, orange accent, rounded cards, table density) is what actually makes the
screenshot look the way it does, and that transfers to either arrangement.

---

## 1. What verification already established

Run before writing this document, because two of these would have changed the design.

| # | Question | Verified answer | Consequence |
|---|---|---|---|
| 1 | Can a window be hidden from `mss` screen capture? | **Yes.** `WDA_EXCLUDEFROMCAPTURE` (0x11) → 0 of 120,000 marker pixels captured, while the window stays visible on screen | The chat HUD needs **no** hide/show cycle. See §4.2 — this is the single most useful finding here |
| 2 | Is `SetWindowDisplayAffinity` available? | Yes, and functional on Windows 10 build 19045 | Needs 19041+; older builds fall back to hiding (§4.2) |
| 3 | Does `WDA_MONITOR` work instead? | Hides it, but renders the region **black** in the capture | Wrong flag — a black rectangle in the screenshot is worse than the window itself |
| 4 | How hard is retheming the overlay to orange? | `overlay._STATE_ACCENT_RGB` is a single central dict, five entries | One-dict change, not a hunt through paint code (§2.4) |
| 5 | Does a power on/off control already exist? | Yes — `hotkey.enabled` plus a tray "Pause push-to-talk" action | The shell's power button reuses it; no new state (§3.4) |
| 6 | Is there somewhere to store chat sessions? | Yes — `~/.nimbus/index.db`, WAL enabled, `CREATE TABLE IF NOT EXISTS` convention, already holding `apps` and `review_queue` | Sessions are two new tables, no migration (§4.6) |

### 1.1 The finding that also unblocks a deferred item

`IMPROVEMENTS.md` deferred **`T2-6` (overlay flicker)** with sound reasoning: the payoff was
cosmetic, and the cost was touching the invariant that overlays hide before `mss.grab()` —
one of the highest-risk areas in the project. Its own `⚠ VERIFY` block even noted that the
obvious approach might not work.

Verification #1 changes that calculus. If the overlay can be marked
`WDA_EXCLUDEFROMCAPTURE`, then the hide → wait 50 ms → grab → show cycle can be **deleted
outright**, which:

* removes 50–100 ms from *every* interaction (there are up to two cycles per turn),
* removes the flicker entirely, and
* **removes the invariant** rather than working around it — there is nothing to hide, so
  nothing can fail to be hidden.

That reframes `T2-6` from "risky cosmetic change" to "cheap latency win with a smaller
attack surface than the status quo". It is listed here as `S-9`, deliberately **last**, so it
is attempted only once the exclusion path has been proven in production by the HUD.

> **Outcome: `S-9` was attempted and abandoned.** Capture exclusion fails on a layered window, and
> the overlay must be layered to be translucent. The reasoning above holds only for the chat panel,
> which can be opaque. See §9 for the measurements and for the 55 ms that was reclaimed instead.

---

## 2. Design system

New module `theme.py`. Values, not vibes — every colour and spacing number lives in one place
so the shell, the HUD, and the overlay cannot drift apart.

### 2.1 Palette

Derived from the reference screenshot: near-black surfaces, a single warm accent, colour used
only where it carries meaning.

```python
# theme.py

# --- Surfaces: a 5-step elevation ramp, not "some dark greys" ---------------
BG_BASE       = "#0B0B0D"   # 0 · window background
BG_SUNKEN     = "#08080A"   # -1 · input wells, code blocks, scroll troughs
BG_ELEVATED   = "#141417"   # 1 · cards, panels, the HUD body
BG_RAISED     = "#191920"   # 2 · popovers, menus, dropdowns
BG_HOVER      = "#1F1F26"   # row / control hover
BG_ACTIVE     = "#26262E"   # row / control pressed

# --- Lines --------------------------------------------------------------------
BORDER        = "#26262B"   # default hairline
BORDER_STRONG = "#33333A"   # focused / active
HIGHLIGHT_TOP = "rgba(255,255,255,0.055)"  # 1px inner top edge — see §2.5

# --- Text ---------------------------------------------------------------------
TEXT_PRIMARY   = "#F5F5F7"  # 16.9:1 on elevated
TEXT_SECONDARY = "#A1A1AA"  #  7.2:1 on elevated
TEXT_MUTED     = "#8A8A94"  #  5.4:1 on elevated — see the note below
TEXT_DISABLED  = "#5A5A63"  # decorative only, never information

# --- Accent -------------------------------------------------------------------
ACCENT        = "#FF7A1A"   # Nimbus orange · 7.1:1 on elevated
ACCENT_HOVER  = "#FF8F3D"
ACCENT_PRESS  = "#E56A0F"
ACCENT_WASH   = "rgba(255,122,26,0.10)"  # selected nav item, subtle fills
ACCENT_GLOW   = "rgba(255,122,26,0.28)"  # blurred bloom behind active elements
ACCENT_HAIR   = "rgba(255,122,26,0.45)"  # 1px accent edge on active surfaces

# --- State --------------------------------------------------------------------
SUCCESS       = "#22C55E"
WARNING       = "#F59E0B"
DANGER        = "#EF4444"
```

**One accent, and it means "Nimbus".** The temptation with a dark theme is to accent
everything; the reference screenshot is legible precisely because orange covers maybe 5% of
the surface. Success/danger are reserved for state, never decoration.

> #### ⚠ `TEXT_MUTED` was wrong, and it was measured
>
> The first draft used `#6B6B75`. Audited against WCAG: **3.49:1 on `BG_ELEVATED`**, which
> fails AA for body text (needs 4.5:1). It only passes as large text, and it was going to be
> used for secondary labels — exactly the small text it fails for.
>
> `#8A8A94` measures **5.38:1**. `#7C7C87` was also tried and misses at 4.46:1, which is the
> kind of near-miss that gets waved through.
>
> Full audit of the final palette, every value computed rather than eyeballed:
>
> | Colour | on `BG_BASE` | on `BG_ELEVATED` | Verdict |
> |---|---|---|---|
> | `TEXT_PRIMARY` | 18.1 | 16.9 | AA |
> | `TEXT_SECONDARY` | 7.7 | 7.2 | AA |
> | `TEXT_MUTED` (fixed) | 5.8 | 5.4 | AA |
> | `ACCENT` | 7.5 | 7.1 | AA |
> | `SUCCESS` / `WARNING` / `DANGER` | 8.6 / 9.2 / 5.2 | 8.1 / 8.6 / 4.9 | AA |
>
> `TEXT_DISABLED` deliberately fails, and that is correct — disabled text must not carry
> information. `test_theme.py` pins these so a future palette tweak cannot quietly regress
> readability.

### 2.2 Geometry and type

```python
RADIUS_CARD    = 12
RADIUS_CONTROL = 8
RADIUS_PILL    = 999

SPACE = (4, 8, 12, 16, 20, 24, 32, 48)   # use only these

FONT_FAMILY  = "Segoe UI"     # already used throughout overlay.py
FONT_DISPLAY = 20   # page titles
FONT_BODY    = 11
FONT_SMALL   = 10   # secondary labels
FONT_MONO    = "Cascadia Mono"   # paths, exe names, licence keys
```

Monospace for paths and executable names is not decoration: `orionflow.exe.md` and
`kpm_viewer.exe` are strings the user must copy accurately, and a proportional font makes
character-level mistakes easy.

### 2.3 Delivery: a single stylesheet, not per-widget styling

`theme.qss` built from the constants above and applied once via
`QApplication.setStyleSheet()`. Per-widget `setStyleSheet` calls are how a Qt app ends up with
four slightly different greys, and they are invisible to review.

Exception, and it is a real one: **`overlay.py` keeps painting with `QPainter`.** It is a
click-through translucent window doing per-frame animation at 60 Hz; a stylesheet has nothing
to offer it. It consumes `theme.py` constants directly instead.

### 2.4 Retheming the overlay to orange

`overlay._STATE_ACCENT_RGB` maps five interaction states to colours. Today they are blue /
green / amber. Verified: it is one dict, so this is a contained change.

| State | Now | Proposed |
|---|---|---|
| `IDLE` | blue `(96,165,250)` | `TEXT_SECONDARY` grey — idle should recede |
| `POINTING` | blue `(59,130,246)` | **`ACCENT` orange** — the brand moment |
| `LISTENING` | green `(34,197,94)` | keep green — "recording" is near-universally green |
| `THINKING` | amber `(245,158,11)` | keep amber, nudged toward `ACCENT` |

> **⚠ VERIFY before implementing.** `tests/test_overlay.py` (37 tests) asserts painted
> colours in places. Run it first and expect to update expectations *with a comment saying
> why*, per `IMPROVEMENTS.md` §1.2. Do not change a test to match new behaviour without that
> note — that is how a real regression gets laundered into a green suite.

**Keep `LISTENING` green even though it is off-brand.** Recording indicators are green
everywhere; overriding that to match a palette trades a learned signal for visual tidiness.

### 2.5 Elevation and shading — how dark UI stops looking flat

This is the section that decides whether the result looks professional or looks like a dark
stylesheet. Dark interfaces have a specific failure mode: without cues, every panel is a grey
rectangle on a slightly different grey rectangle, and the eye cannot tell what is on top of
what. Six techniques fix that, and **all six are cheap**.

#### 1. Top-edge highlight — the single highest-impact trick

A 1 px lighter line along the **top inside edge** of a raised surface. It simulates light
falling from above, which is how every physical object reads, and it is why a card with it
looks like an object while a card without it looks like a hole.

```css
/* theme.qss */
QFrame#Card {
    background: #141417;
    border: 1px solid #26262B;
    border-top: 1px solid rgba(255,255,255,0.055);   /* the highlight */
    border-radius: 12px;
}
```

If only one thing from this section gets implemented, it is this one.

#### 2. Tint the surface, do not just lighten it

Neutral grey stacks look muddy. Each elevation step gains a **very slight** warm shift toward
the accent — 2–3 points of red over blue. Present in the ramp above (`#191920` is warmer than
a neutral `#1A1A1A`) and it is why the stack reads as one material rather than five unrelated
greys.

#### 3. Ambient accent bloom behind active elements

The reference screenshot has a soft glow along its bottom edge. Generalised: a heavily blurred,
low-alpha accent wash **behind** the focused element — active nav item, live HUD header, the
power toggle when on.

```python
glow = QGraphicsDropShadowEffect()
glow.setBlurRadius(28)
glow.setColor(QColor(255, 122, 26, 72))   # ACCENT_GLOW
glow.setOffset(0, 0)                       # ambient, not directional
widget.setGraphicsEffect(glow)
```

> **⚠ VERIFY.** `QGraphicsDropShadowEffect` forces the widget into a software-rendered
> offscreen buffer. On a widget that repaints every frame it is a real cost. Use it on
> **static** elements only — nav items, card borders, the power toggle. For the HUD's animated
> state strip, paint the bloom with a `QRadialGradient` in `paintEvent` instead, which is what
> `overlay.py` already does for the spinner.

#### 4. Two-tone borders

An outer border **darker** than the surface plus an inner highlight **lighter** than it reads
as a bevel at a fraction of the cost of an actual bevel. Combined with #1 this is what makes
the reference cards look inset into the page.

#### 5. Grain — the thing that separates good dark themes from amateur ones

Large, low-contrast gradients on dark backgrounds **band** — visible stepped stripes, worst on
cheap panels and worse again after Windows' colour management. A 1–2% opacity tiled noise
texture over the window destroys banding completely and adds a faint premium texture.

- 128×128 tiled PNG, monochrome noise, ~2% alpha
- Drawn once as a window-level overlay, not per widget
- ~8 KB, generated at build time by a script so it is reproducible

#### 6. Scrim gradients at scroll edges

A hard cut at the top of a scroll area looks broken; content should **fade** into the edge. A
24 px gradient from the surface colour to transparent at both ends of every scrollable region —
the HUD message list, the session picker, the knowledge file list. It also signals "there is
more above" without a scrollbar.

#### Shadows

| Level | Use | Value |
|---|---|---|
| 1 | Cards | none — #1 and #4 do the work; shadows on flat cards look dated |
| 2 | Popovers, dropdowns, session picker | `0 8px 24px rgba(0,0,0,0.45)` |
| 3 | The HUD itself, modals | `0 16px 48px rgba(0,0,0,0.55)` |

**Cards get no shadow.** Shadowing everything is the most common way a dark theme starts
looking like 2014 Material.

### 2.6 Motion

Animation here is for **communicating state**, not decoration. Every duration below is short
enough not to be waited on; anything over ~300 ms in a utility app reads as lag.

```python
# theme.py
DUR_MICRO    = 120   # hover, press, focus ring
DUR_STANDARD = 200   # expand/collapse, page change, nav slide
DUR_ENTRANCE = 260   # HUD appearing, popover opening
DUR_EXIT     = 160   # anything leaving — always faster than its entrance

EASE_STANDARD = (0.4, 0.0, 0.2, 1.0)    # QEasingCurve.Type.BezierSpline
EASE_OUT      = (0.0, 0.0, 0.2, 1.0)    # entrances
EASE_IN       = (0.4, 0.0, 1.0, 1.0)    # exits
EASE_OVERSHOOT= "OutBack"               # the HUD arriving, sparingly
```

**Exits are faster than entrances.** An element arriving deserves to be noticed; the same
element leaving is in the user's way. Equal durations make dismissal feel sluggish, and it is
the most common mistake in hand-rolled UI motion.

| What | Animation | Duration |
|---|---|---|
| HUD appears | slide down 12 px + fade in | 260 ms `OutBack` |
| HUD dismisses | fade + rise 8 px | 160 ms `EASE_IN` |
| HUD minimise → pill | width/height morph + content crossfade | 200 ms `EASE_STANDARD` |
| New message | fade + rise 8 px | 200 ms `EASE_OUT` |
| Nav selection | accent wash **slides** to the new item | 200 ms `EASE_STANDARD` |
| Page change | crossfade only, no slide | 160 ms |
| Hover / press | background + border colour | 120 ms |
| Power toggle | knob slides, bloom fades in | 200 ms |
| Screenshot expand | height + opacity | 200 ms `EASE_STANDARD` |
| State change (listening→thinking) | header strip colour crossfade | 200 ms |
| Waveform / spinner | existing 60 Hz `QPainter` loops — **unchanged** | — |

#### Three rules that keep it smooth

1. **Animate `opacity` and `pos`, never `width`/`height`, wherever there is a choice.** Layout
   animation forces a relayout of every child per frame. The minimise morph is the one
   justified exception, and it is 200 ms on a small widget.
2. **Never animate on a background thread.** All animation is `QPropertyAnimation` on the Qt
   main thread. Obvious, and still the way this gets broken.
3. **Nav selection slides; pages crossfade.** A sliding page transition in a utility app looks
   like a phone and costs the user time on every navigation.

#### Reduced motion — not optional

Windows exposes a system preference, and vestibular sensitivity is real.

```python
def animations_enabled() -> bool:
    """Honour the Windows 'show animations' preference (SPI_GETCLIENTAREAANIMATION).

    When off, every duration collapses to 0 and transitions become instant state changes.
    Deliberately checked once at startup rather than per animation -- the setting changes
    rarely and a per-animation syscall on the hover path is wasteful.
    """
    import ctypes
    SPI_GETCLIENTAREAANIMATION = 0x1042
    enabled = ctypes.c_int()
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
    return bool(enabled.value) if ok else True
```

> **⚠ VERIFY.** Confirm the flag reads correctly and that a `0` duration `QPropertyAnimation`
> still fires `finished` — several code paths will depend on that signal to run cleanup, and
> if it does not fire, disabling animation silently breaks them.

### 2.7 Component recipes

Concrete enough to implement without inventing, so the shell and the HUD end up visually
identical rather than merely similar.

**Card** — surface `BG_ELEVATED`, radius 12, `BORDER` outline, `HIGHLIGHT_TOP` inner top edge,
padding 20, no shadow. Optional 13 px `TEXT_SECONDARY` uppercase header with 0.4 px letter
spacing.

**Primary button** — `ACCENT` fill, `#0B0B0D` text (dark on orange reads better than white and
measures higher), radius 8, height 34, 600 weight. Hover `ACCENT_HOVER`; press `ACCENT_PRESS`
plus 1 px downward translate.

**Secondary button** — transparent fill, `BORDER` outline, `TEXT_PRIMARY`. Hover fills
`BG_HOVER` and borders `BORDER_STRONG`.

**Nav item** — height 38, radius 8, 16 px icon + label. Idle `TEXT_SECONDARY`; hover
`BG_HOVER`; **selected** = `ACCENT_WASH` fill, `TEXT_PRIMARY`, a 3 px `ACCENT` bar on the
leading edge, and the wash *slides* between items.

**Toggle** — 40×22 track, 18 px knob. Off `BG_ACTIVE`; on `ACCENT` with an `ACCENT_GLOW` bloom.
Knob slides over `DUR_STANDARD`.

**Input** — `BG_SUNKEN` fill, `BORDER` outline, radius 8, height 34. Focus swaps to
`BORDER_STRONG` plus a 2 px `ACCENT_WASH` ring **outside** the border, so focus never shifts
layout.

**Focus ring** — 2 px `ACCENT` at 55% alpha, 2 px offset, on every focusable control. Keyboard
navigation is not optional, and Qt's default focus rect is invisible on a dark theme.

**Chat bubble** — no bubble. User turns are `TEXT_SECONDARY` with a 2 px `BORDER` leading rule;
Nimbus turns are `TEXT_PRIMARY` with a 2 px `ACCENT_HAIR` leading rule. Speaker bubbles at HUD
width waste horizontal space and read as a phone messenger, which is the wrong reference.

---

## 3. Part A — the application shell

### `[ ] S-1` — Frameless main window with sidebar navigation

New package `shell/` rather than one large module, because a single `window.py` covering six
pages plus a custom title bar becomes unreviewable fast:

```
shell/
    __init__.py
    window.py        # MainWindow: title bar + nav + page stack
    nav.py           # Sidebar, NavItem
    titlebar.py      # custom frameless title bar (drag, min, max, close)
    pages/
        __init__.py
        home.py      # power, status, recent activity
        knowledge.py # knowledge base browser
        journal.py   # T3-3 review queue
        settings.py  # hosts existing SettingsDialog widgets
        account.py   # licence status, device, sign out
theme.py
theme.qss
```

#### Window behaviour

- **Frameless** (`Qt.FramelessWindowHint`) with a custom title bar, which is what makes the
  reference screenshot look like a product rather than a Qt app.
- **Minimum 1040 × 680.** Below that the sidebar plus content stops working. Chosen to fit a
  1366 × 768 laptop after the taskbar — the same screen size that caught out the Settings
  dialog (`IMPROVEMENTS.md` `T3-2` follow-up).
- **Opens at 1240 × 780**, clamped to 88% of the available screen. Reuse the
  `_size_to_screen` approach already added to `SettingsDialog`; do not reinvent it.
- **Closing hides to tray, it does not quit.** `closeEvent` → `hide()`, and a tray balloon
  the first time so the user is not left wondering. Quitting is the tray's "Quit Nimbus" and
  the Account page. Nimbus is a background tool; closing the window must not stop
  push-to-talk.
- **Frameless windows lose snap and edge-resize by default.** Either implement
  `WM_NCHITTEST` via `nativeEvent`, or accept a resize grip. Budget real time for this — it is
  the single most underestimated part of a custom title bar.

> **⚠ VERIFY before implementing**
> 1. Frameless + `WindowStaysOnTopHint` on the *overlay* already coexists with a normal
>    window; confirm the shell does not steal focus from the overlay's click-through styles.
> 2. Confirm per-monitor DPI behaviour when the window is dragged between monitors of
>    different scaling. `capture.py` documents Per-Monitor-V2; the shell must not assume one
>    ratio.
> 3. Confirm `QApplication` already exists before the shell is constructed — `app.py` builds
>    it early, and constructing widgets before it is a hard crash.

### `[ ] S-2` — Pages

| Page | Contents | Reuses |
|---|---|---|
| **Home** | Big power toggle, current provider/model, hotkey reminder, last-5 interactions, "nothing leaves your machine" framing | `hotkey.enabled`, `_history` |
| **Knowledge** | Per-app list from `KB_DIR`, file counts, size, "Open folder", inline view of the seeded guide, drag-and-drop to add | `kb.py`, `kb.ensure_guide` |
| **Journal** | `T3-3` review queue: due count, topics, accuracy, "quiz me" trigger, per-item history | `review.ReviewQueue`, `review.write_insights` |
| **Settings** | Existing settings, re-hosted (§3.5) | `settings_dialog.py` |
| **Account** | Licence status, plan, device name, seats used, "Deactivate this device", sign out, quit | `licensing.py` (§5) |

**Home earns its place by answering "is it on, and what is it using?"** — the two questions a
tray-only app cannot answer without opening a menu. Everything else on that page is secondary.

#### Home page layout — where the reference screenshot's density comes from

The Lunar screenshot reads as professional because of **information hierarchy**, not
decoration: one dominant number, a row of supporting cards, then a dense table. Home mirrors
that structure with Nimbus's own content.

```
┌──────────┬──────────────────────────────────────────────────────────┐
│          │  Home                                                    │
│ ● Home   │  ┌────────────────────┐ ┌──────────┐ ┌──────────┐        │
│ ○ Know…  │  │  ⏻  LISTENING      │ │ Provider │ │ This week│        │
│ ○ Journal│  │  ctrl+alt+space    │ │ Gemini   │ │   47     │        │
│ ○ Settings│ │  [ ON  ●        ]  │ │ 3.1 Pro  │ │ questions│        │
│ ○ Account│  └────────────────────┘ └──────────┘ └──────────┘        │
│          │  ┌──────────────────────────────────────────────────────┐│
│          │  │ Recent            app        when      pointed at    ││
│          │  │ where is export…  Kiro       2m ago    export button ││
│          │  │ what is a pivot…  EXCEL      1h ago    —             ││
│          │  └──────────────────────────────────────────────────────┘│
│  ◐ Local │  ┌──────────────────────────────────────────────────────┐│
│  ✓ Guard │  │ Privacy    Screenshots skipped this week: 3          ││
└──────────┴──└──────────────────────────────────────────────────────┘┘
```

- **The power card is deliberately dominant.** "Is it on?" is the question a tray-only app
  cannot answer, and it is the first thing the eye should land on.
- **A footer status block in the sidebar** — a dot for the provider mode (local vs cloud) and a
  tick for the Privacy Guard. Always-visible reassurance, no click needed.
- **"Screenshots skipped this week: 3"** turns the Privacy Guard from a claim into an
  observation. That number is the single most trust-building thing on the page, and it is free
  — the guard already logs every suppression.

#### Deliberately *not* pages

- **Diagnostics/logs.** Already served by the debug log and Explorer. A log viewer is a
  surprisingly large amount of UI for something used a handful of times.
- **Memory browser.** `memory.py`'s contract is that memory is plain Markdown the user can
  read and edit in any editor; a bespoke viewer would weaken that, not strengthen it. Link to
  the folder.
- **A dashboard of charts.** The reference is a trading app, where the numbers *are* the
  product. Nimbus's numbers are incidental; a usage sparkline would be decoration pretending to
  be information.

### `[ ] S-3` — Power control

Nimbus is either listening for the hotkey or it is not. That state already exists as
`hotkey.enabled`, with a tray action toggling it.

```python
# shell/pages/home.py
def _on_power_toggled(self, on: bool) -> None:
    self.sig_set_listening.emit(on)     # -> NimbusApp on the Qt main thread
```

**One source of truth, three views.** The window's toggle, the tray's menu item, and the tray
icon must never disagree. They all read and write `hotkey.enabled`; none holds its own copy of
the state. A `sig_listening_changed` signal from `NimbusApp` drives all three, so flipping it
anywhere updates everywhere.

> **⚠ VERIFY.** Confirm `hotkey.enabled` genuinely gates callbacks without uninstalling the
> listener — `hotkey.py`'s docstring says the listener stays installed. Toggling should be
> instant and must not need a restart, unlike the settings marked `↻`.

### `[ ] S-4` — Hosting the existing Settings

**The point of this item is to not rewrite `settings_dialog.py`.** It carries 41 tests, the
provider/model/key matrix, the OpenRouter key-reuse logic, keyring persistence, the hotkey
capture widget, the Privacy group, the experimental group, and the restart labels. Rewriting it
as a "nicer" page would silently drop several of those.

Two options, and the second is the recommendation:

| | Approach | Verdict |
|---|---|---|
| A | Keep opening `SettingsDialog` as a modal from the shell | Fastest, zero risk, but a modal dialog inside a modern shell looks exactly as bolted-on as it is |
| B | **Extract `_build_ui`'s content into a reusable `QWidget`**, hosted either by the dialog (unchanged) or by the shell page | Slightly more work, keeps one implementation and all 41 tests |

B is a **pure refactor**: move the body of `_build_ui` into `SettingsForm(QWidget)`, have
`SettingsDialog` embed it plus its button box, and have the shell page embed it with its own
Save. Every existing test keeps passing untouched, which is the acceptance criterion.

> **⚠ VERIFY.** `SettingsDialog._on_save` currently also handles the Ollama compatibility
> prompt and the "local data cleared → restart" path. Both must keep working from the shell.
> Check `_local_data_cleared` in particular — the shell needs to react to it, not just the
> dialog.

### `[ ] S-5` — Tray relationship

The tray does **not** go away. It is what makes Nimbus feel like a background utility rather
than an app you have to keep open, and it is the only surface available when the window is
closed.

| Surface | Role after this change |
|---|---|
| **Tray** | Always present. Left-click → show/focus window. Menu: Show Nimbus, Pause, Quit |
| **Window** | Primary surface for everything configurable |

**Trim the tray menu.** Items that now have a better home in the window — Settings, Open
Knowledge Folder, Open Memory Folder, Export Session History — should leave the tray. A menu
that duplicates the window is two places to keep in sync and two places to fix a bug.

Keep **Pause** in the tray: it is the one action whose whole value is being reachable in one
click without opening anything.

---

## 4. Part B — the chat HUD

### 4.0 Why this is a new window and not a change to `overlay.py`

They look similar — both floating, both dark, both always-on-top — and merging them would be
a mistake:

| | `overlay.py` (existing) | Chat HUD (new) |
|---|---|---|
| Purpose | Point at and draw on the screen | Show what was said |
| Interaction | **Click-through**, never focusable | Clickable, draggable, scrollable |
| Count | One **per monitor** | **One**, total |
| Painting | `QPainter`, 60 Hz animation | Widgets, event-driven |
| Coordinates | Spaces A/B/C, DPI-critical | Its own window space only |

`overlay.py` is the most coordinate-sensitive file in the project. Adding a scrollable
message list with focus and drag handling to it would put ordinary UI code inside the one
module where a mistake produces a mispointed cursor.

### `[ ] S-6` — HUD window and positioning

New module `chat_hud.py`.

```
┌─────────────────────────────────────────────────┐
│ ◉ Nimbus    Excel · session 3        ⌄  —  ✕    │  ← drag anywhere here
├─────────────────────────────────────────────────┤
│  you   where is the export button?              │
│                                                 │
│  nimbus  it's top-right, next to Share.         │
│          ▸ screenshot                           │  ← click to expand
│                                                 │
│  you   what about presets?                      │
│  nimbus  ...                                    │
├─────────────────────────────────────────────────┤
│  ⏻ listening · ctrl+alt+space        + New chat │
└─────────────────────────────────────────────────┘
```

- **720 × 420**, top-centre, 24 px below the top edge of the cursor's monitor.
- **Draggable** by the header. Position persisted per monitor, so it returns where the user
  left it. `Home` on the header, or a right-click item, resets it to top-centre — a dragged
  window on a monitor that has since been unplugged is otherwise unreachable.
- **Minimises to a pill** (~200 × 34) that stays top-centre and shows the latest state
  ("listening…", "thinking…", the first few words of the reply). Minimising should not mean
  losing the ability to tell whether Nimbus heard you.
- **Frameless, translucent** (`WA_TranslucentBackground`), `BG_ELEVATED` at ~92% alpha with a
  1 px `BORDER` hairline and an orange top edge when active.
- **Never takes focus on appearing.** `Qt.WindowDoesNotAcceptFocus` unless the user clicks it.
  A panel that steals focus mid-typing while the user is working in another app is a bug, and
  Nimbus appears *while* they are working in another app by definition.

- **Resizable** by the bottom edge and bottom corners, 560–1100 wide, 280–720 tall. Users with
  long answers will want it bigger, and a fixed panel feels like a toy.
- **A 2 px state strip** across the top, colour-bound to the interaction state: green
  listening, amber thinking, orange speaking, invisible idle. It is the same information the
  overlay conveys at the cursor, available without looking away from the panel.

> **⚠ VERIFY**
> 1. `WA_TranslucentBackground` plus `WindowStaysOnTopHint` plus per-monitor DPI — confirm the
>    HUD renders correctly when dragged between monitors with different scaling. Reuse
>    `overlay.physical_to_local_logical` rather than writing new maths.
> 2. Confirm the HUD does not appear in the Alt-Tab list (`Qt.Tool` window type).
> 3. Confirm it coexists with the overlay's per-monitor windows without z-order fighting.
> 4. Confirm `WA_TranslucentBackground` + a `QScrollArea` renders without artefacts. Translucent
>    windows and scrolling viewports interact badly in some Qt/Windows combinations, and the
>    fallback is an opaque `BG_ELEVATED` body with only the border translucent.

### `[ ] S-6b` — Interactions worth having

Not a wish list. Each of these earns its place by removing a specific friction that exists
today, and the ones I considered and rejected are recorded at the end so they are not
re-proposed.

#### Streams the reply as it is spoken

The pipeline already streams sentence-by-sentence into TTS. The HUD renders those same deltas,
so text appears in step with the voice.

Why it matters more than it sounds: a panel that stays empty for four seconds and then dumps a
finished paragraph feels slower than one that fills as Nimbus talks — **even at identical
latency**. This is the cheapest perceived-performance win available, and the plumbing exists.

#### Replay and re-point

Two buttons on hover over a Nimbus turn:

| Control | Action | Why |
|---|---|---|
| ⟲ **Replay** | Speak that reply again via `tts.speak()` | "What did it say?" currently has no answer but asking again |
| ◎ **Show me** | Re-fly the cursor to that turn's stored coordinate | The pointer fades after a few seconds. Re-pointing is currently a whole new request — a round trip and a token spend to re-show something already known |

**Re-point is the one I would fight for.** The coordinate is already stored (§4.6) and the
overlay already knows how to fly to a point, so it is signal plumbing with no model call. It
turns the HUD from a log into a control surface.

#### Copy, and a quiet correction signal

- **Copy** on hover — obvious, and its absence is immediately annoying.
- **⚑ "that was wrong"** — one click, no dialog. Writes a flag against the turn. Two uses:
  it is honest telemetry the user controls, and it can suppress the turn from `T3-3`'s review
  queue. Reviewing a wrong answer for thirty days would actively teach the wrong thing.

#### Auto-hide, pin, and an honest empty state

- **Auto-hide** after `HUD_IDLE_HIDE_SECONDS` (default 45) of no activity, fading out. Returns
  on the next interaction. An always-visible panel on a screen the user is working on becomes
  furniture they resent.
- **Pin** in the header defeats auto-hide for users who want it permanent.
- **Empty state** on first run: *"Hold Ctrl+Alt+Space and ask about anything on your screen."*
  A blank panel teaches nothing, and this is the only surface where the core interaction can
  be explained at the moment it is relevant. Reads the real configured hotkey, not a hardcoded
  string.

#### Errors belong here

When a request fails, the HUD is the right place — inline against the turn that failed, with a
**Retry** that re-runs the same transcript without re-recording. Today a failure is a toast
that vanishes, and retrying means asking again from scratch.

#### Session picker

The header session label opens a popover: search field, recent sessions with app badge and
relative time, **+ New chat**, and per-row delete.

The search field is not premature. Sessions accumulate silently — after a few weeks of normal
use there are hundreds, and a flat list stops being navigable well before that.

#### Considered and rejected

| Idea | Why not |
|---|---|
| **Text input in the HUD** | Nimbus is voice-first. A text box invites a different product with a different design; §11 keeps it a non-goal |
| **Per-turn token/cost readout** | Genuinely useful to maybe 5% of users, and clutter for the rest. Belongs in the debug log, which already has it |
| **Markdown rendering in replies** | The system prompt forbids markdown because every character is spoken. Rendering it would encourage the model to emit it |
| **Export session to file** | `_export_session_history` already exists and the shell is a better home for it |
| **Reactions / threading** | Chat-app furniture. There is one participant and no branching |

#### Keyboard shortcuts

Routed through `hotkey.py`'s existing listener, the same way `T2-2` added Esc — **not** a second
`WH_KEYBOARD_LL` hook, which would double the per-keystroke cost system-wide to watch two keys.

| Chord | Action |
|---|---|
| `Ctrl+Alt+H` | Show / hide the HUD |
| `Ctrl+Alt+N` | New chat |
| `Esc` | Cancel — **existing `T2-2` behaviour, unchanged** |

> **⚠ VERIFY.** `parse_hotkey` deliberately rejects modifier-free chords and is *not* the right
> path for these. Follow `_handle_cancel_key`'s pattern: gate on a predicate, swallow every
> exception, and never touch the PTT state machine. An escaping exception kills the listener
> thread and the hotkey dies for the whole session.

### `[ ] S-7` — Capture exclusion (**the load-bearing detail**)

> **This is the most important technical decision in Part B.**

A chat panel showing the last answer, pinned to the top of the screen, would be **captured in
the screenshot** and fed to the model on the next question. The consequences are not cosmetic:

1. The model sees its own previous answer rendered as UI and may describe it.
2. It may **point at the HUD** instead of the application underneath.
3. Every screenshot carries a panel of text, wasting tokens on every request.

`overlay.py` solves this by hiding before `mss.grab()` and showing after — the 50 ms cycle
that `T2-6` exists to complain about.

**Verified working (§1): `WDA_EXCLUDEFROMCAPTURE` removes the window from capture entirely
while leaving it visible on screen.** 0 of 120,000 marker pixels appeared in an `mss` grab.

```python
# chat_hud.py
_WDA_NONE = 0x00
_WDA_EXCLUDEFROMCAPTURE = 0x11

def exclude_from_capture(hwnd: int) -> bool:
    """Hide this window from screen capture while leaving it on screen.

    Verified: 0 of 120,000 marker pixels reach an mss grab. Requires Windows 10 build
    19041+; SetWindowDisplayAffinity returns 0 on older builds, which is the fallback signal.

    NOT WDA_MONITOR (0x01) -- that also hides the window but paints the region BLACK in the
    capture, which is worse than the window itself: the model then sees a black rectangle
    covering the top of the screen.
    """
    import ctypes
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            ctypes.c_void_p(hwnd), ctypes.c_uint(_WDA_EXCLUDEFROMCAPTURE)))
    except Exception:
        return False
```

**Fallback, and it must exist.** On builds older than 19041 the call fails. Then the HUD joins
the existing hide/show cycle via `sig_hide_overlay`. Detected once at startup and logged, so
a user on an old build gets a working app with slightly more flicker rather than a screenshot
containing the panel.

> **⚠ VERIFY at runtime, not just at build time.** Check the return value on the *real* HUD
> window after `show()`, and log which path is active. A silent failure here is invisible
> until someone notices Nimbus pointing at its own chat panel.

### `[ ] S-8` — Messages, screenshots, and sessions

#### Message model

```python
@dataclass(frozen=True)
class ChatMessage:
    role: str            # "user" | "nimbus" | "system"
    text: str
    created_at: str
    screenshot: str = ""              # relative path, "" when none
    coordinate: tuple[int, int] | None = None   # Space C, for the marker
```

The **`system`** role is not padding. It is how the HUD explains an absence:

- *"Screenshot skipped — a password manager was open"* (`T2-1` Privacy Guard)
- *"Cancelled"* (`T2-2` Esc)
- *"New chat started"*

Without it, a privacy-suppressed turn looks like Nimbus malfunctioning.

#### Screenshots — click to view

Stored per session, thumbnail generated on write:

```
~/.nimbus/chats/<session_id>/<message_id>.jpg        # full, JPEG q80
~/.nimbus/chats/<session_id>/<message_id>_thumb.jpg  # 240px wide
```

Collapsed by default as a `▸ screenshot` row. Expanding shows the thumbnail with the pointer
coordinate drawn on it — `debug_log.save_screenshot` already draws exactly that marker, so
reuse it rather than writing a second marker renderer.

**Three constraints that are easy to get wrong:**

1. **Never store a screenshot the Privacy Guard refused.** The guard's entire purpose is that
   those pixels are not retained. Writing them to `~/.nimbus/chats/` would quietly undo `T2-1`
   — worse than not having the guard, because the user believes they are protected.
2. **Retention, or this grows without bound.** ~150 KB per turn is ~50 MB after a few hundred
   interactions. Reuse the existing `DIAGNOSTIC_RETENTION_DAYS` pattern:
   `CHAT_RETENTION_DAYS`, default 14, pruned at startup.
3. **`CHAT_STORE_SCREENSHOTS` default `off`.** Screen contents on disk is a bigger privacy
   commitment than a transcript, and it deserves an explicit yes rather than being inherited
   from a feature the user enabled for a different reason.

#### Sessions

- **New chat** → new session, `_history` cleared. This is the honest version of "zero
  context": it must clear the in-memory `_history` that `_pipeline_worker` passes to the model,
  not merely start a new visual thread. A "new chat" that still sends the last ten exchanges
  is a lie.
- **Switch session** → load its messages, rebuild `_history` from the last
  `_MAX_HISTORY_EXCHANGES` (10) pairs, honouring `HISTORY_IMAGE_COUNT` (`T2-4`) for images.
- **Auto-title** from the first user message, truncated. No LLM call for a title — that would
  spend a request on cosmetics.
- **Auto-new-session** when the foreground app changes *and* the previous session is older
  than ~30 minutes. Per-app memory already exists (`memory.py`), so a session that spans
  Excel and Photoshop is muddled context; but switching windows for ten seconds should not
  fragment one conversation.

### `[ ] S-8b` — Persistence schema

Two tables in the existing `~/.nimbus/index.db`, alongside `apps` and `review_queue`. Same
`CREATE TABLE IF NOT EXISTS` contract, so no migration and existing databases are untouched.

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL DEFAULT '',
    app_name     TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL,
    role         TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    screenshot   TEXT NOT NULL DEFAULT '',
    coord_x      INTEGER,
    coord_y      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id);
```

`FOREIGN KEY` is deliberately omitted: SQLite does not enforce it without
`PRAGMA foreign_keys=ON` per connection, and `memory.py` does not set it. A constraint that
looks enforced but is not is worse than none — deletion cascades happen in `sessions.py`.

> **⚠ VERIFY.** `memory.MemoryStore` and `review.ReviewQueue` each open their own connection
> with `isolation_level=None` and WAL. A third writer is fine under WAL's single-writer model
> **provided all writes stay on the Qt main thread**, which is the documented assumption.
> Confirm the HUD writes from the main thread and not from `_pipeline_worker`.

### 4.1 The relationship to `_history` — the subtle part

`app.py` keeps `_history` in memory, reset on restart. Sessions make conversations durable,
which means there are now **two** representations of the same conversation, and they can
disagree.

**Rule: `_history` stays the single source of truth for what is sent to the model.** The
session store is a *record* of it, plus what the model does not need (screenshots, system
notes, timestamps). Switching sessions rebuilds `_history` from the store; nothing else reads
back from it.

Inverting that — making the store authoritative and having the pipeline query it per turn —
would put a database read on the hot path and couple `_pipeline_worker` to the UI. Not worth
it.

---

## 5. Licensing and activation

### `[ ] S-10` — Licence gate

Read §0.1 first. This section assumes the conclusion: **deterrence, not enforcement.**

#### Recommended shape

```
first launch
   ↓
sign-in / licence key dialog  ──── offline? ──→  cached licence still valid? ──→ run
   ↓                                                      ↓ no
POST /activate {key, device_id, device_name}          blocking prompt
   ↓
{ signed_licence, expires_at, seats_used, seats_max }
   ↓
cache to keyring (DPAPI) + verify Ed25519 signature locally
   ↓
run · revalidate every 7 days · 14-day offline grace
```

#### Decisions, with reasons

| Decision | Choice | Why |
|---|---|---|
| Credential | **Licence key**, not email+password | No password to store, reset, or breach. Nimbus has no account system and does not need one |
| Device identity | Salted hash of machine GUID + volume serial | Stable across reboots, changes on reinstall-to-new-machine. **Never send a raw hardware ID** — that is a fingerprint you then have to be a good custodian of |
| Storage | Windows Credential Manager via existing `keyring` | Already a dependency, already DPAPI per-user. A licence file on disk is trivially copied between machines |
| Verification | **Ed25519 signature, public key embedded** | The app can verify without contacting the server, so it works offline. **The private key never ships** |
| Offline grace | 14 days | A tool that stops working on a flight is worse than one that gets pirated |
| Revalidation | Every 7 days, silent | Detects seat abuse without nagging |
| Failure mode | **Blocking dialog with "Retry" and "Use offline"** | Never silently degrade. A user who paid must never be left guessing |

#### What not to build

- **Do not roll your own licence server.** Keygen, Cryptolens, LemonSqueezy and Paddle all
  provide activation, seat limits and revocation as an API. Building this yourself means owning
  key rotation, revocation lists, and an availability requirement where downtime locks out
  legitimate users.
- **Do not obfuscate the Python.** PyArmor and friends add build fragility and break
  PyInstaller in ways that cost days to debug, in exchange for delaying a determined attacker
  by an afternoon.
- **Do not phone home on every launch.** It makes startup depend on your uptime, which turns
  your outage into their outage.

#### Where the gate belongs

```python
# app.py __main__, BEFORE QApplication work and before the pipeline starts
if not licensing.is_activated():
    if not run_activation_flow():       # modal, blocking
        sys.exit(1)
```

**Before the hotkey listener installs and before the mic opens.** An unlicensed instance
should consume no devices and register no global hooks.

> **⚠ VERIFY before implementing**
> 1. Confirm `keyring` round-trips a ~1 KB signed blob. Credential Manager has a per-entry
>    size limit (~2.5 KB for generic credentials) — a licence with a long signature and
>    metadata can approach it. If it does not fit, store the licence in
>    `%LOCALAPPDATA%\Nimbus\licence.dat` and keep only a verification secret in the keyring.
> 2. Confirm the chosen provider's SDK (or plain `httpx`, already a dependency) does not add a
>    heavy transitive dependency to the frozen build.
> 3. Decide and record the **trial** behaviour before writing the dialog. Retrofitting a trial
>    into an activation flow is far more disruptive than designing it in.

#### The honest note to keep in the code

```python
"""Licence activation (S-10).

This DETERS casual sharing. It does not prevent a determined user from patching the check
out of a PyInstaller bundle, and no client-side check can. That is accepted deliberately
(SHELL_AND_CHAT.md §0.1): the alternative is proxying inference through a server, which
would end BYOK and contradict the non-goal recorded in IMPROVEMENTS.md §8.

So the design optimises for: honest use is never inconvenienced, seat abuse is
visible and revocable, and offline use keeps working.
"""
```

---

## 6. Threading model

Nothing here changes the existing model, and that is the requirement. Restating it because
both features add UI that will be tempted to touch state from the wrong thread.

| Thread | Owns | May touch Qt? |
|---|---|---|
| Qt main | Shell, HUD, overlay, tray, all widgets | **Yes — only here** |
| pynput listener | Hotkey press/release/Esc | No → `pyqtSignal` |
| `_pipeline_worker` | STT, capture, model, TTS | No → `pyqtSignal` |
| portaudio / WS | Audio levels, partial transcripts | No → `pyqtSignal` |
| `_GeometryWorker` | Concurrent geometry call | No |

New signals on `NimbusApp`:

```python
sig_chat_message   = pyqtSignal(object)   # ChatMessage -> HUD append
sig_chat_state     = pyqtSignal(str)      # "listening" | "thinking" | "speaking" | "idle"
sig_session_changed= pyqtSignal(int)      # session id -> HUD + shell
sig_listening_changed = pyqtSignal(bool)  # power state -> window + tray + icon
```

**`sig_chat_message` carries the whole message object, not a formatted string.** The HUD needs
the role, the screenshot path and the coordinate to render; passing pre-rendered text would put
formatting decisions inside `_pipeline_worker`, which is the wrong place and untestable without
Qt.

`T4-5`'s live captions already prove this path: `on_partial_transcript` fires on the
WebSocket thread and reaches the overlay through `sig_caption`. The HUD reuses the same
mechanism.

### 6.1 Does the HUD replace live captions?

Partly, and that needs deciding rather than discovering later. The HUD shows the transcript;
`T4-5`'s caption also shows the transcript.

**Recommendation: keep both, and make the caption defer.** The caption sits at the bottom of
the screen and appears while speaking; the HUD is a scrollable history at the top. When the
HUD is **visible and not minimised**, suppress the caption — two copies of the same words on
one screen is noise. When the HUD is minimised or hidden, the caption does its original job.

---

## 7. Invariants that must not break

Carried from `IMPROVEMENTS.md` §1.6, plus new ones these features introduce. Every one has
cost real debugging time or would.

| # | Invariant | Why it matters here |
|---|---|---|
| 1 | **The HUD is never captured** | Otherwise the model sees its own answer and may point at it (§4.2) |
| 2 | **Overlays hide before `mss.grab()`** — permanently. `S-9` was tried and cannot work: exclusion fails on a layered window, and the overlay must be layered (§9) | The existing guarantee; do not weaken it speculatively |
| 3 | **Positions transform, lengths only scale** | Any new coordinate code reuses `overlay.annotations_to_local` |
| 4 | **Qt only on the Qt main thread** | The HUD is fed from three non-Qt threads |
| 5 | **Closing the window must not stop push-to-talk** | Nimbus is a background tool |
| 6 | **A privacy-suppressed screenshot is never written to disk** | Otherwise the chat store silently undoes `T2-1` |
| 7 | **"New chat" clears `_history`, not just the view** | Otherwise "zero context" is untrue |
| 8 | **The fully-local path still works** | Ollama + faster-whisper + Kokoro, no network. A licence check must not require internet at *every* launch (§5) |
| 9 | **`_pipeline_worker` gains no UI dependency** | It must remain testable without Qt |
| 10 | **The pipeline never blocks on the HUD** | A HUD exception must degrade to "no chat panel", never "no answer" |

---

## 8. Test plan

New files, mirroring existing conventions (`pytest`, `pytest-mock`, imports inside tests, one
file per module).

### `tests/test_theme.py`
```python
def test_every_colour_is_valid_hex_or_rgba(): ...
def test_palette_has_exactly_one_accent():
    """A dark theme stays legible by using accent sparingly; two accents means neither reads
    as 'Nimbus'."""
def test_spacing_scale_is_the_only_source_of_spacing(): ...
def test_qss_references_no_literal_colours():
    """Drift guard: a hardcoded #1a1a1a in the stylesheet is how four slightly different
    greys appear."""

# --- contrast: these caught a real failure in the first draft (§2.1) ---------
@pytest.mark.parametrize("name", ["TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED"])
@pytest.mark.parametrize("bg", ["BG_BASE", "BG_ELEVATED", "BG_RAISED"])
def test_body_text_meets_wcag_aa(name, bg):
    """4.5:1 minimum. TEXT_MUTED was #6B6B75 (3.49:1) and would have shipped failing."""

def test_accent_and_state_colours_meet_aa(): ...
def test_text_disabled_is_deliberately_below_aa():
    """Documents intent: disabled text must not carry information, so it is allowed to fail.
    Without this test, someone 'fixes' it and disabled stops looking disabled."""
def test_dark_text_on_accent_beats_white_text_on_accent():
    """Why primary buttons use #0B0B0D on orange rather than white."""

# --- motion -----------------------------------------------------------------
def test_exit_durations_are_faster_than_entrances():
    """An arriving element deserves notice; a leaving one is in the way."""
def test_no_duration_exceeds_300ms():
    """Anything longer reads as lag in a utility app."""
def test_reduced_motion_collapses_every_duration_to_zero(mocker): ...
def test_zero_duration_animation_still_emits_finished(qt_app):
    """Cleanup logic hangs off `finished`. If it does not fire, disabling animation silently
    breaks those paths -- the failure mode of honouring an accessibility setting."""
```

### `tests/test_shell.py`
```python
def test_window_minimum_fits_1366x768(): ...
def test_opens_within_screen_cap(): ...
def test_close_hides_and_does_not_quit():
    """Invariant 5 — closing must not stop push-to-talk."""
def test_every_nav_item_maps_to_a_page(): ...
def test_nav_side_constant_moves_the_sidebar():
    """§0.3 — disagreeing about left/right must stay a one-constant change."""
def test_settings_form_is_shared_with_the_dialog():
    """S-4: one implementation, not two."""
def test_power_toggle_reflects_hotkey_enabled(): ...
def test_power_state_is_not_duplicated():
    """Window, tray and icon must read one source, or they drift."""
```

### `tests/test_chat_hud.py`
```python
def test_capture_exclusion_is_applied_on_show():
    """Invariant 1. THE test for this feature."""
def test_falls_back_to_hiding_when_exclusion_unavailable(self, mocker):
    """Simulate SetWindowDisplayAffinity returning 0 (pre-19041 Windows)."""
def test_exclusion_uses_excludefromcapture_not_monitor():
    """WDA_MONITOR renders the region BLACK in the capture, which is worse."""
def test_never_takes_focus_on_appearing(): ...
def test_not_in_alt_tab(): ...
def test_drag_persists_position_per_monitor(): ...
def test_reset_position_returns_to_top_centre():
    """A window dragged onto a now-unplugged monitor must be recoverable."""
def test_minimised_pill_still_shows_state():
    """Minimising must not cost the user the ability to tell if Nimbus heard them."""
def test_system_message_rendered_for_privacy_skip(): ...
def test_hud_exception_does_not_break_the_pipeline():
    """Invariant 10."""
def test_messages_arrive_via_signal_not_direct_call():
    """Invariant 4."""

# --- S-6b interactions -------------------------------------------------------
def test_streaming_deltas_append_to_the_open_message():
    """A second delta must extend the current turn, not create a new one."""
def test_replay_calls_tts_with_the_stored_text(mocker): ...
def test_repoint_emits_the_stored_coordinate_without_a_model_call(mocker):
    """The whole value of re-point: no round trip, no tokens."""
def test_repoint_is_absent_when_the_turn_had_no_coordinate(): ...
def test_retry_reuses_the_transcript_without_re_recording(mocker): ...
def test_auto_hide_fires_after_idle_and_cancels_on_activity(): ...
def test_pin_defeats_auto_hide(): ...
def test_empty_state_shows_the_configured_hotkey_not_a_hardcoded_one():
    """A user who remapped the hotkey must not be told the wrong chord."""
def test_wrong_flag_excludes_the_turn_from_the_review_queue():
    """T3-3 interaction: reviewing a known-wrong answer teaches the wrong thing."""
def test_resize_is_clamped_to_min_and_max(): ...
def test_state_strip_colour_matches_the_interaction_state(): ...
```

### `tests/test_sessions.py`
```python
class TestSessionStore:
    def test_schema_created_idempotently(self, tmp_path): ...
    def test_existing_memory_and_review_tables_untouched(self, tmp_path):
        """Backward-compat gate. Users have live databases with apps + review_queue."""
    def test_new_session_clears_history(self):
        """Invariant 7 — 'zero context' must be true."""
    def test_switching_session_rebuilds_history_within_max_exchanges(self): ...
    def test_switching_session_honours_history_image_count(self):
        """T2-4 interaction."""
    def test_auto_title_from_first_user_message(self): ...
    def test_auto_title_makes_no_api_call():
        """A title is cosmetic; spending a request on it is not justified."""
    def test_privacy_suppressed_turn_stores_no_screenshot(self):
        """Invariant 6 — the one that would silently undo T2-1."""
    def test_screenshots_disabled_by_default(self): ...
    def test_retention_prunes_old_sessions(self, tmp_path): ...
    def test_deleting_a_session_removes_its_screenshots(self, tmp_path):
        """No FOREIGN KEY, so the cascade is ours to get right."""
```

### `tests/test_licensing.py`
```python
def test_valid_signature_accepted(): ...
def test_tampered_licence_rejected(): ...
def test_expired_licence_rejected(): ...
def test_offline_grace_allows_use_within_window(): ...
def test_offline_grace_expires(): ...
def test_device_id_is_stable_across_calls(): ...
def test_device_id_is_not_a_raw_hardware_id():
    """A raw fingerprint is a custodianship liability; it must be salted and hashed."""
def test_private_key_is_not_present_in_the_package():
    """The single most damaging possible mistake in this feature."""
def test_activation_failure_shows_a_blocking_prompt_not_silent_degradation(): ...
def test_local_only_path_does_not_require_network_at_every_launch():
    """Invariant 8."""
```

### Manual smoke tests — not automatable, and required

1. Frameless window: drag, snap, resize from every edge, maximise, restore.
2. Drag the window between monitors at different DPI scaling.
3. **Ask a question with the HUD visible and confirm the screenshot in the debug log does not
   contain the HUD.** The single most important manual check in this document.
4. Open a password manager, ask something, confirm the HUD shows the system message and no
   screenshot is written to `~/.nimbus/chats/`.
5. Close the window, confirm push-to-talk still works.
6. Pull the network cable, restart, confirm the app runs on cached licence.

---

## 9. Phased rollout

Each phase ends with a working, shippable app. Ordered so the riskiest unknowns are proven
early and the largest cosmetic work happens last.

| Phase | Items | Ends with | Est. |
|---|---|---|---|
| **1 — Foundation** | `theme.py`, `theme.qss`, overlay retheme (`S-0`) | Nimbus looks like the new brand; no structural change | 1 day |
| **2 — Chat HUD** | `S-6`, `S-7`, `S-8`, `S-8b` | The visible headline feature, and it proves capture exclusion in real use | 3–4 days |
| **3 — Shell** | `S-1`, `S-2`, `S-3`, `S-4`, `S-5` | The windowed app, settings re-hosted, tray trimmed | 4–5 days |
| **4 — Licensing** | `S-10` | Gated distribution | 2–3 days + provider setup |
| **5 — Payoff** | ~~`S-9`~~ (delete the hide/show cycle) | **Abandoned — not achievable.** The 55 ms was reclaimed another way; see below | — |

**Why the HUD before the shell**, despite the shell being the bigger ask: the HUD is what the
user *sees working* on every interaction, and it de-risks `WDA_EXCLUDEFROMCAPTURE` in
production before `S-9` depends on it. The shell is mostly known-quantity Qt work.

**Why licensing fourth, not first.** It gates distribution, not development, and its design
depends on a provider decision (§10) that should not block visible progress.

**Why `S-9` last.** It deletes a safety mechanism. It should only happen after exclusion has
been running in the HUD for a while, on real hardware. Doing it early trades a small latency
win for the risk of the model pointing at Nimbus's own overlay.

#### `S-9` was attempted and is **not achievable**. Do not try again.

Measured on real hardware, not reasoned about. `SetWindowDisplayAffinity` returns **0** on the
overlay: its ex-style is `0x080800A8`, which includes `WS_EX_LAYERED` — set by
`WA_TranslucentBackground` — and exclusion fails outright on a layered window, which `chat_hud.py`
already records. The overlay must be translucent to draw a pointer over arbitrary desktop content,
so unlike the chat panel it cannot trade transparency for exclusion. **The pointer is absent from
diagnostic screenshots *because of* the hide/show cycle, not despite it**; deleting it reopens the
feedback loop Invariant 3 exists to prevent.

The latency was reclaimable anyway, and almost all of it. The cycle's cost was a hard-coded 50ms
sleep — a guess at how long the compositor needs — and `DwmFlush()` answers the question properly by
blocking until the next present completes:

| Wait | Median over 7 capture cycles | Nimbus-orange pixels in the grab |
|---|---|---|
| overlay visible | — | 413 px |
| fixed 50 ms sleep | 174.9 ms | 332 px (5 identical runs) |
| `DwmFlush()` | **119.8 ms** | 332 px (5 identical runs) |

332 px is Nimbus's own window, legitimately on screen; the overlay contributes 81 px and both waits
remove all of them. So **55 ms per interaction was reclaimed with no change in safety**, which is
inside `S-9`'s own 50–100 ms estimate. `app._wait_for_compositor` carries the full reasoning and
falls back to the old sleep if `DwmFlush` is unavailable — a missing compositor call must cost
latency, never Invariant 3.

### 9.1 Running two agents in parallel

Phases 2 and 3 **can** run in parallel, but not naively — they share three files, and two
agents editing `app.py` concurrently produces a conflict that costs more than the time saved.

#### What is genuinely independent

| | Agent A — Chat HUD | Agent B — Shell |
|---|---|---|
| Creates | `chat_hud.py`, `sessions.py`, `tests/test_chat_hud.py`, `tests/test_sessions.py` | `shell/**`, `theme.qss`, `tests/test_shell.py` |
| Reads | `theme.py`, `overlay.py`, `app.py` | `theme.py`, `settings_dialog.py`, `tray.py`, `app.py` |
| Modifies | *nothing existing* | `settings_dialog.py` (the `S-4` extract only) |

#### The three shared files, and who owns them

`app.py`, `config.py` and `nimbus.spec` are touched by **both** features. Rather than
coordinate, **neither agent edits them.** Each agent delivers self-contained modules with a
documented integration surface, and integration happens in a single pass afterwards.

That is not bureaucracy — it is the difference between two clean merges and a manual conflict
resolution in the 2,800-line orchestrator that holds the pipeline together.

#### Sequence

```
  ME  ── theme.py  (the shared contract: palette, motion, elevation)
       │           small, and everything downstream depends on it
       ├──────────────┬──────────────┐
       ▼              ▼              │
   AGENT A        AGENT B            │  in parallel
   chat_hud.py    shell/**           │
   sessions.py    theme.qss          │
       └──────────────┴──────────────┘
                      ▼
  ME  ── integration: app.py signals, config.py settings,
         nimbus.spec hiddenimports, selftest runtime_modules
                      ▼
         full suite + selftest + manual smoke (§8)
```

**`theme.py` must exist before either agent starts.** Both consume it, and if each invents its
own the result is two palettes and a day of reconciliation. It is small — an hour — and it is
the contract.

#### Integration surface each agent must deliver

Written to as a spec, so the integration pass is mechanical:

```python
# Agent A — chat_hud.py + sessions.py
class ChatHud(QWidget):
    def append(self, message: ChatMessage) -> None: ...
    def stream_delta(self, text: str) -> None: ...
    def set_state(self, state: str) -> None: ...      # listening|thinking|speaking|idle
    def set_session(self, session_id: int, title: str) -> None: ...
    sig_new_session   = pyqtSignal()
    sig_open_session  = pyqtSignal(int)
    sig_repoint       = pyqtSignal(int, int)          # Space C coordinate
    sig_replay        = pyqtSignal(str)               # text to speak
    sig_retry         = pyqtSignal(str)               # transcript to re-run

# Agent B — shell/window.py
class MainWindow(QWidget):
    def show_page(self, name: str) -> None: ...
    def set_listening(self, on: bool) -> None: ...
    def set_provider(self, provider: str, model: str) -> None: ...
    sig_set_listening = pyqtSignal(bool)
    sig_quit          = pyqtSignal()
```

**Both must be constructible and testable with no `NimbusApp`.** Dependency-injected callbacks
and signals only, exactly as `stt.py`, `realtime.py` and `gemini_live.py` already do. If either
agent needs to import `app`, the seam is wrong — and it would also make the module untestable
without starting the whole application.

#### Rules for both agents

1. **Do not edit `app.py`, `config.py` or `nimbus.spec`.** Document what you need instead.
2. **Do not edit any existing test file.** If an existing test fails, stop and report it — that
   is a regression, not a test to update.
3. **Consume `theme.py`.** No literal colours, no literal durations.
4. Follow `IMPROVEMENTS.md` §1.4 conventions: imports inside tests, one test file per module,
   CRLF endings.
5. Verify with the canonical command, which neutralises `.env`:
   `python -c "import dotenv,pytest,sys; dotenv.load_dotenv=lambda *a,**k:False; sys.exit(pytest.main(['-q']))"`
6. **`⚠ VERIFY` blocks are not optional.** They have caught eleven wrong assumptions across
   Tiers 0–3, including several in this document's own first draft.

---

## 10. Decisions needed from the maintainer

Ordered by how much they block. Recorded here so they are answered deliberately rather than
implied by whatever gets built first.

Decisions 1, 2, 3 and 7 are **answered and built**. The rest stand as recorded.

| # | Decision | Options | Outcome |
|---|---|---|---|
| 1 | **Licensing model** | Offline key · online activation · server proxy | **Decided: online activation**, device-bound licence, 14-day offline grace, **2 devices** (a desktop and a laptop). Built in `licensing.py`. Server proxy remains a non-goal (BYOK stays), though a "no keys needed, we run the model" edition is on the roadmap and would revisit it |
| 2 | **Licence provider** | LemonSqueezy · Paddle · Keygen · Cryptolens · own server | **Decided: our own backend** — the Next.js app in `web/`, deployed to Vercel, which owns accounts, email and licence signing. A deliberate departure from the recommendation, because one deployment serves the site, the accounts, the download and the licence API rather than four things to run. Stripe and a manual-transfer rail are wired in and covered by tests but **not connected**; nothing is charged |
| 3 | **Trial** | None · 7-day · limited interactions/day | **Decided: 7 days, no card, device-bound.** Keyed on a salted device hash server-side and kept forever, so a new email address earns no second trial |
| 4 | **Nav position** | Left · right | Left (§0.3). One constant either way |
| 5 | **Top nav or sidebar** | The screenshot uses a top nav; the brief says sidebar | Sidebar as asked — but the palette and card treatment are what make the reference look the way it does |
| 6 | **Store screenshots in chat history?** | Off · on · ask | **Off by default.** Screen contents on disk is a bigger commitment than a transcript |
| 7 | **Free tier?** | Paid-only · free BYOK-local + paid cloud extras | **Decided: paid-only.** The 7-day trial is the only free tier. Recommendation not taken, knowingly — a free local tier splits the product in two for a group of testers that is being onboarded in person anyway |
| 8 | **Does the HUD replace live captions?** | Replace · coexist | Coexist, with the caption suppressed while the HUD is visible (§6.1) |
| 9 | **HUD default visibility** | Always on · auto-hide after idle · only while interacting | **Auto-hide after 45 s.** An always-visible panel on a screen someone is working on becomes furniture they resent; pin is there for people who disagree |
| 10 | **Retheme the overlay pointer to orange?** | Yes · keep it blue | Yes for `POINTING` — it is the brand moment. Keep `LISTENING` green; recording indicators are green everywhere and overriding a learned signal for palette tidiness is a bad trade (§2.4) |
| 11 | **Grain overlay?** | Yes · no | Yes. ~8 KB and it eliminates gradient banding, which is the most visible amateur tell in a dark theme (§2.5) |

### 10.1 New settings this introduces

All follow the existing `resolve_setting` pattern, and all are restart-gated, so each needs the
`↻` marker from `T4-7` and an entry in `RESTART_REQUIRED_SETTINGS`.

| Setting | Default | Notes |
|---|---|---|
| `CHAT_HUD` | `on` | Master switch for the HUD |
| `CHAT_HUD_AUTOHIDE_SECONDS` | `45` | `0` = never auto-hide |
| `CHAT_STORE_SCREENSHOTS` | **`off`** | Screen contents on disk is a bigger commitment than a transcript |
| `CHAT_RETENTION_DAYS` | `14` | Mirrors `DIAGNOSTIC_RETENTION_DAYS` |
| `SHELL_ON_STARTUP` | `off` | Whether the window opens at launch or Nimbus starts to tray |
| `NAV_SIDE` | `left` | §0.3 — `left` \| `right` |
| `REDUCE_MOTION` | `auto` | `auto` follows Windows, `on`/`off` override |

**`CHAT_STORE_SCREENSHOTS` is the one to get right.** Everything else here is preference; that
one is a privacy commitment, and it must be an explicit opt-in rather than inherited from
having enabled the HUD.

---

## 11. Non-goals

Recorded so they are not relitigated mid-implementation.

| Non-goal | Why |
|---|---|
| **Typing into the HUD** | Nimbus is voice-first, and a text box invites a completely different product. If it is wanted, it is its own item with its own design |
| **Markdown rendering in HUD replies** | The system prompt forbids markdown because every character is spoken aloud. Rendering it would encourage the model to emit it, degrading the speech |
| **Charts / usage dashboard on Home** | The reference is a trading app, where the numbers *are* the product. Nimbus's numbers are incidental; a sparkline would be decoration pretending to be information |
| **Per-turn token or cost readout in the HUD** | Useful to a small minority, clutter for everyone else. The debug log already records it |
| **Custom user themes** | One palette done well beats a theming engine. Revisit only if asked for repeatedly |
| **A web/Electron rewrite** | PyQt6 is already a dependency, the overlay is deeply tied to Win32, and 1,252 tests assume the current stack |
| **Cloud sync of chats** | Contradicts the local-first framing and needs an account system the licence model deliberately avoids |
| **Preventing piracy** | See §0.1. Deterrence is the honest goal; anything stronger requires ending BYOK |
| **Obfuscating the Python** | Build fragility for an afternoon's delay to an attacker |
| **A log/diagnostics viewer page** | Explorer and the debug log already serve this; it is a lot of UI for rare use |
| **A memory browser page** | `memory.py`'s contract is plain Markdown the user can edit anywhere; a bespoke viewer weakens that |
| **Replacing the tray** | It is the only surface available when the window is closed |
| **Replacing `overlay.py` with the HUD** | Different jobs, and the overlay is the most coordinate-sensitive code in the project (§4.0) |

---

*Cross-references use file and symbol names rather than line numbers, since line numbers
drift. Colour values are derived from the supplied reference screenshot.*
