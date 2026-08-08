# Design Document

## Overview

Two modules. `chat_hud.py` is one floating window — frameless, always on top, never focusable, hidden
from screen capture, fed exclusively through signals. `sessions.py` is the durable record behind it:
three tables in the database that already holds per-application memory and the review queue, plus three
pure functions carrying all the subtle behaviour. Two of those tables are this feature's own; the third,
`privacy_skips`, belongs to the privacy guard and lives here only because this module owns the schema.

The split matters for the same reason the privacy split does: everything hard about sessions — the
ten-exchange window, the image budget, when a new session is justified — is a pure function over
values, so it is exhaustively testable with no database, no clock and no toolkit.

Three findings shaped this design more than any preference:

**Capture exclusion and translucency are mutually exclusive.** Measured, not assumed. Exclusion wins,
because a cosmetic alpha is not worth the model pointing at Nimbus's own panel.

**One graphics effect left attached turned the panel black.** The dismiss path detached it; the reveal
path did not. So from the first time the panel appeared, every repaint of the body went through a stale
offscreen buffer for the rest of the session.

**A styled button cannot host two lines of text.** Three separate attempts failed for three separate
reasons. A plain frame has none of that machinery.

> Consolidated from `SHELL_AND_CHAT.md` §4 `S-6`, `S-6b`, `S-7`, `S-8`, `S-8b`, §4.1 and §6.1.

## Architecture

```
  pipeline thread          listener thread         socket thread
        │                        │                      │
        └────────── pyqtSignal ──┴──────────────────────┘
                          │  sig_message(ChatMessage) · sig_delta(str) · sig_state(str)
                          ↓                            listening|thinking|speaking|idle
      ┌──────────────── ChatHud ── ONE window, Qt main thread ────────────────┐
      │ WS_EX_LAYERED?  NO.  Opaque body.  ← the whole design hangs on this   │
      │                                                                       │
      │ showEvent → SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)    │
      │             re-applied EVERY show, because a window can lose it       │
      │ resizeEvent → SetWindowRgn(round-rect)   ← corners without layering   │
      │                                                                       │
      │  ┌───────────────────────────────────────────────────────────────┐    │
      │  │ 3px state strip   green listening · amber thinking · orange    │   │
      │  ├───────────────────────────────────────────────────────────────┤    │
      │  │ header 38px   mark · session title · sessions · new · collapse │    │
      │  │ ─── accent hairline ─────────────────────────────────────────  │    │
      │  │ QScrollArea    _SessionRow / message rows (QFrame, NOT button) │    │
      │  │                thumbnail + red marker at the Space C coord     │    │
      │  │                replay · repoint · retry · "that was wrong"     │    │
      │  │ ─── accent hairline ─────────────────────────────────────────  │    │
      │  │ footer 36px   state · hotkey pill · pin                        │    │
      │  └───────────────────────────────────────────────────────────────┘    │
      │  RESIZE_MARGIN 5  = the grabbable border AND the visible bezel        │
      │  CORNER_MARGIN 16 = diagonal hit region only, not the bezel           │
      │  every public method wrapped in @_never_raises → logged, swallowed    │
      └────────────────────────────┬──────────────────────────────────────────┘
                                   │ ChatHud.append  ← main thread BY DEFINITION
                                   ↓                   so the single-writer rule is structural
      ┌──────────────────── sessions.SessionStore ───────────────────────────┐
      │  ~/.nimbus/index.db     chat_sessions · chat_messages · privacy_skips│
      │                         beside apps (memory) and review_queue        │
      │                         CREATE TABLE IF NOT EXISTS · WAL · no ALTER  │
      │  ~/.nimbus/chats/<session>/<message>.jpg  + _thumb.jpg               │
      │      ↑ derived from INDEX_DB_PATH, so a test that moves the DB        │
      │        moves the images too                                          │
      │                                                                      │
      │  save_screenshot: THREE refusals, in order                           │
      │      1. privacy_skipped   ← the one that matters                      │
      │      2. store_screenshots off  ← the DEFAULT                          │
      │      3. no image / write failed → "" (no dangling path)               │
      └──────────────────────────────┬───────────────────────────────────────┘
                                     │ pure, no DB / clock / Qt
              auto_title · build_history · should_auto_new_session · clamp_size
                                     │
        start_new_session / switch_session  ──→ MUTATE app._history IN PLACE
                                                (list.clear / history[:] = …)
```

## Components and Interfaces

### Capture exclusion — the load-bearing detail

```python
_WDA_NONE               = 0x00
_WDA_MONITOR            = 0x01   # named ONLY to record that it was rejected
_WDA_EXCLUDEFROMCAPTURE = 0x11

def exclude_from_capture(hwnd: int) -> bool: ...        # never raises
def apply_rounded_region(hwnd, w, h, radius) -> bool: ...
def needs_hide_for_capture() -> bool: ...
```

Measured on Windows 10 19045:

| Window | Return | Affinity |
|---|---|---|
| opaque frameless tool window | 1 | `0x11` |
| same window + `WA_TranslucentBackground` | 0 | `0x00` |

`WA_TranslucentBackground` adds `WS_EX_LAYERED`, and `SetWindowDisplayAffinity` fails on a layered
window. So the design brief's 92%-alpha body and capture exclusion are **mutually exclusive**, and
exclusion wins. The design document's own verification step anticipated this and named the opaque body
as the fallback, so this is the sanctioned path rather than an improvisation.

Effectiveness is a pixel count, with a control:

| | Marker pixels in an `mss` grab |
|---|---|
| exclusion **on** | **0** of 4,147,200 |
| exclusion **off**, same run | 299,789 |

The control is not ceremony. Without it, a test that renders no marker at all passes silently.

`WDA_MONITOR` is named in the source purely so the next reader knows it was considered. It hides the
window from capture but renders the region **black** — worse than the window itself, because the model
then sees a black rectangle across the top of the screen and has no way to know it is not part of the
application.

Rounded corners come from `SetWindowRgn` with a round-rect region, which needs no layering and was
verified not to disturb the affinity. It must be re-applied on every resize: the region is defined at a
fixed size in window coordinates, so a stale one clips the new geometry.

The same finding bans window-level fades. `setWindowOpacity(<1.0)` also forces Qt's layered path — the
overlay module already documents that — so a window-level fade would trade correct answers for polish.

### Why there is no opacity animation at all

`reveal` and `dismiss` used to animate a `QGraphicsOpacityEffect` on the body. **Removed after it
produced the black panel.**

The old helper's own docstring contained the reason for the bug: *the effect is attached only for the
duration of the fade, because a permanent effect forces every repaint through an offscreen buffer.*
`dismiss` detached it when its animation finished. **`reveal` never did.** Measured: after one
`reveal()`, `self._body.graphicsEffect()` is still a live effect at opacity 1.0.

That produced two reported symptoms — "the chat has a black bg" after reopening, and black bars down
the sides when switching session. A resize re-creates the buffer, and whatever has not repainted into it
yet is transparent black.

So `reveal` now **clears** any effect rather than trusting that none is attached — that is precisely the
state that caused the bug — and the entrance slides instead, animating `pos`, which needs no buffer.
Dismissal is immediate. `_replace` stops and disposes of the previous animation before starting the
next, because two live animations on the same property fight and the loser wins intermittently.

The shell's page crossfade was retired for exactly the same class of artefact.

### `_never_raises`

```python
@_never_raises
def append(self, message): ...
```

Wraps every public entry point. The pipeline emits into the panel and moves on; if a render path
throws — a malformed message, a deleted screenshot, a destroyed Qt object — the user loses the chat
panel for that turn, not the answer they asked for. Logged rather than silent, because an invisible
swallowed exception is how this feature would rot unnoticed.

### Geometry

```python
HUD_WIDTH, HUD_HEIGHT   = 660, 430      # up from 600x340
MIN_WIDTH, MAX_WIDTH    = 460, 1200
MIN_HEIGHT, MAX_HEIGHT  = 260, 900
TOP_MARGIN              = 24
RESIZE_MARGIN           = 5             # border AND bezel
CORNER_MARGIN           = 16            # hit region only
HEADER_HEIGHT           = 38            # up from 34
FOOTER_HEIGHT           = 36            # up from 30
STATE_STRIP_HEIGHT      = 3             # up from 2
DEFAULT_AUTOHIDE_SECONDS = 45
```

The size grew because the first pass optimised only for covering as little of the user's work as
possible, and produced a panel where the header, transcript and footer were pressed against each other
with no air anywhere. The interior padding grew with it, so the extra 60×90 buys **margins**, not more
rows. Legible beats small.

`MIN_WIDTH` is a real floor: below about 460 the footer's status text and its two pills stop fitting on
one line, which is what produced the elided `idle · ctrl+alt+sp...`. `MAX_WIDTH` exists because an
unbounded drag produces a 3000px panel covering the application the user is asking about, which defeats
the point of the product.

`RESIZE_MARGIN` is deliberately both the grabbable border and the visible bezel. The inset is what
leaves bare window under the pointer for the hit test, so a narrower gutter would create a ring that
changes the cursor but is not grabbable. 5px rather than 7: at 7 it read as a second frame around the
panel, a box inside a box, and 5 matches the shell window so the two look related rather than
coincidental.

`CORNER_MARGIN` is three times the border, and that gap is the fix for "the corner cursor never shows
up". Two 5px strips crossing leave a 5×5 corner — 25 pixels — and one pixel outside it the user silently
gets a single-axis resize instead of the diagonal they were aiming for. 16px is close to what the
platform's own frames use and costs nothing visually, because it changes only the hit test.

`STATE_STRIP_HEIGHT` went from 2 to 3 because 2px read as a rendering artefact rather than the
deliberate state indicator it is.

```python
def clamp_size(width, height) -> tuple[int, int]: ...        # pure
def top_centre_position(geometry, width, margin) -> tuple[int, int]: ...   # pure
def state_colour(state) -> str: ...                          # pure, unknown → idle
```

State colours: green listening, amber thinking, orange speaking, invisible idle. The same information
the overlay conveys at the cursor, available without looking away from the panel. Listening stays green
rather than matching the palette, for the reason the overlay's own state map gives — recording
indicators are green everywhere, and the user needs certainty that the microphone is live more than they
need palette tidiness.

### Collapse

The third state, and the one people actually asked for: know Nimbus is there and which session you are
in, without a transcript over your work. Minimise shrinks to a 200px pill and loses the session name;
collapse keeps the bar exactly where it was and drops the body.

```python
def _bar_height(self) -> int:
    shell = self.layout().contentsMargins()
    body  = self._body.layout().contentsMargins()
    return (shell.top() + shell.bottom() + body.top() + body.bottom()
            + STATE_STRIP_HEIGHT + self._header.height())
```

**Derived from the live layouts, not written as a literal.** It used to be
`STATE_STRIP_HEIGHT + header + 2`, where the `2` stood for the body's 1px margins. Adding the resize
gutter put another 10px between the window edge and the header, so the collapsed window came out 53px
short of the 43 it claimed — the body could not fit the header, and the header spilled past the body's
bottom edge and clipped the four buttons in it. Deriving it means the next margin change cannot
reintroduce that.

Four ordering rules, each fixing a measured bug:

1. **Height is set explicitly**, because a frameless window keeps its old height if nothing tells it
   otherwise — the body would vanish and leave an empty rectangle.
2. **The direction is decided before anything moves, and remembered.** Deciding again on expand would
   let a panel dragged near a screen edge mid-collapse expand the other way and jump.
3. **The previous height is recorded before the children are hidden.** Hiding them makes the layout
   recalculate immediately and shrink the window to its minimum, so reading the height afterwards
   returns `MIN_HEIGHT` rather than the size the user chose — expanding then "restored" the panel to
   220px. Caught by `test_expanding_restores_the_previous_height`.
4. **Signals are blocked while syncing the control.** `setChecked` re-emits `toggled`, which re-entered
   the method: the inner call collapsed the panel and *then* the outer call recorded the height, by
   which point it was 38px. Same reason the power toggle and the tray's pause action block signals — a
   view syncing itself must not look like input.

**Collapsing never moves the bar.** It stays exactly where the panel's top edge was, which is what makes
it behave like a dropdown handle: the thing you clicked is still under your pointer afterwards. An
earlier version moved it to where the panel's bottom edge had been, and a bar that walks away from the
click that collapsed it is disorienting even when the arithmetic is right.

Direction follows position: below the screen's halfway line it expands upwards, above it downwards,
which is how every menu on the platform behaves. With no screen it defaults to downwards — a panel that
opens down and is clipped is still usable, one that opens off the top is not. The glyph is an arrow
pointing **where the body will go**, not a state indicator.

### `_SessionRow` — a frame, deliberately

Three attempts at a button failed, each for a different reason, and all clipped the descenders:

| Attempt | Why it failed |
|---|---|
| `QPushButton("title\nsubtitle")` + `setMinimumHeight` | The application stylesheet's `QPushButton { min-height: 20px }` **overrides** the widget property — 28px allocated for 42px of text |
| Same, with `min-height` raised in the button's own stylesheet | `min-height` governs the *content box*, and the arithmetic never agreed with the layout — 42px against 44px needed |
| `QPushButton` containing a layout of two `QLabel`s | A styled button computes its size hint from the style's contents size and **ignores a child layout** — the labels were squeezed to 5px each |

A `QFrame` has none of that machinery: its layout's size hint is its size hint, the labels report their
own heights, and the row is exactly as tall as its content. Clicking is one `mousePressEvent`, which is
less code than any of the three attempts. `WA_Hover` is set explicitly, because the stylesheet's
`:hover` rule does not fire on a plain frame without it.

### `sessions.ChatMessage`

```python
@dataclass(frozen=True)
class ChatMessage:
    role: str                                  # user | nimbus | system
    text: str
    created_at: str = ""
    screenshot: str = ""
    coordinate: tuple[int, int] | None = None  # Space C
    message_id: int = 0
    error: str = ""
    image: object = field(default=None, compare=False, repr=False)
    privacy_skipped: bool = field(default=False, compare=False, repr=False)
```

`coordinate` is **Space C** — the declared-resolution coordinate the model returned, which is also the
space the stored screenshot is in, so the marker draws on the image with no transform. Re-pointing later
emits it unchanged and the application runs the same Space C to physical conversion it already runs for
a live answer.

`image` and `privacy_skipped` are **not persisted and not compared**. They carry the pixels only as far
as the main-thread write call, and a true suppression flag is a hard stop there.

The `system` role is not padding. It is how the panel explains an **absence**: "Screenshot skipped — a
password manager was open", "Cancelled", "New chat started". Without a role for those, a
privacy-suppressed turn looks indistinguishable from Nimbus malfunctioning, and the user's conclusion is
that the application is broken rather than that it protected them.

### The three pure functions

```python
def auto_title(text, limit=48) -> str: ...
def should_auto_new_session(previous_app, current_app, last_used_at,
                            now=None, idle_minutes=30) -> bool: ...
def build_history(messages, max_exchanges=10, image_count=None,
                  chats_dir=None, read_image=_read_jpeg) -> list[dict]: ...
```

**Titles need no model call.** A title is cosmetic; spending a request and a round trip on one is not
justified, and the first thing the user said is a better label than a generated summary because it is
what they will search for later. Truncation lands on a word boundary where one is available.

**A session boundary needs both conditions.** Per-application memory already exists, so a session
spanning Excel and Photoshop is muddled context — but alt-tabbing to a browser for ten seconds must not
fragment one conversation into three. Time alone is not enough either: an hour of continuous work in one
application is still one conversation.

**History rebuild produces exactly the shape the pipeline appends** — `{"role": "user"|"assistant",
"content": [block, ...]}` with Anthropic-form blocks. Anything else works until the first provider that
actually reads history, which is the worst time to find out. `system` messages are dropped, because they
were never sent to the model and replaying them would put interface copy into the conversation as if
someone had said it.

The image budget is applied **newest-first, before any blocks are built**, so it is honoured regardless
of where the screenshots sit. Newest, because an old screenshot is actively misleading: the user has
moved on and the model would answer about a window that is no longer there. `image_count` defaults to
the live setting read at call time rather than import time, so a settings change applies without a
restart.

`MAX_HISTORY_EXCHANGES` duplicates the pipeline's constant rather than importing it, because importing
the orchestrator here would drag a whole running application into a module that must be testable on its
own. `test_history_window_matches_the_app_constant` pins the two so they cannot drift.

### The two operations that also touch `_history`

```python
def start_new_session(store, app_name="", history=None, now=None) -> int:
    if history is not None:
        history.clear()                    # IN PLACE, same call
    return store.new_session(...)

def switch_session(store, session_id, history=None, ...) -> list[dict]:
    rebuilt = store.history_for_session(...)
    if history is not None:
        history[:] = rebuilt               # IN PLACE
    store.touch(session_id)
    return rebuilt
```

Clearing is part of the same call on purpose. "New chat" that starts a fresh visual thread while still
sending the model the last ten exchanges is a **lie**, and the way that lie happens is a caller creating
the session and forgetting the clear. Making it one operation removes the opportunity.

In place rather than returning a new list, because the pipeline holds the same object; rebinding here
would leave the worker with the old one.

The persistent record is a *record*, not the source of truth. Nothing reads back from it per turn — that
would put a database read on the hot path and couple the pipeline worker to the interface.

### `save_screenshot` — three refusals, in order

```python
if privacy_skipped or not self.store_screenshots or image is None:
    return ""
```

1. **`privacy_skipped`.** The guard's entire purpose is that those pixels are not retained. Writing them
   here would quietly undo it, which is worse than never having had the guard, because the user believes
   they are protected. The flag passed in must be the *same* boolean the guard returned, or the
   protection is decorative.
2. **`store_screenshots` off — the default.** Screen contents on disk is a materially bigger privacy
   commitment than a transcript, and deserves an explicit yes rather than being inherited from switching
   the panel on for an unrelated reason.
3. **No image, or the write failed.** Returns `""` so the caller records a turn with no screenshot rather
   than a dangling path.

Never raises: a thumbnail is a nicety, the transcript is the feature. The stored image carries the same
red circle-and-crosshair the diagnostic screenshot draws, at the same radius and stroke width, so a
thumbnail and a diagnostic image show the user the same marker for the same coordinate. It is
reimplemented rather than called, because the existing method is bound to a diagnostic session's folder
and gated on a diagnostics setting — there is no way to reach the drawing without also creating a
diagnostic session the user did not ask for.

## Data Models

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
    session_id   INTEGER NOT NULL,        -- no FOREIGN KEY, deliberately
    role         TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    screenshot   TEXT NOT NULL DEFAULT '',
    coord_x      INTEGER,                 -- both or neither
    coord_y      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS privacy_skips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_privacy_skips_created ON privacy_skips(created_at);
```

**No foreign key, deliberately.** SQLite does not enforce one without a per-connection pragma, and
neither of the existing stores sets it — a constraint that looks enforced but is not is worse than none,
because the next reader trusts it. So deletion cascades are explicit in `delete_session`, which also
removes the screenshot folder that no constraint could reach.

A coordinate exists only when **both** columns are present. A half-null pair is treated as no coordinate
rather than as `(x, 0)`, because a re-point to a fabricated coordinate would fly the cursor somewhere the
model never suggested.

Dates are ISO strings, which sort correctly as text.

On disk:

```
~/.nimbus/chats/<session_id>/<message_id>.jpg
~/.nimbus/chats/<session_id>/<message_id>_thumb.jpg     240px wide, quality 80
```

Derived from the database path rather than declared independently, so pointing the database elsewhere —
which the tests and the environment override both do — moves the screenshots with it instead of
scattering test images into a real profile.

Settings, all restart-gated: `CHAT_HUD` (on), `CHAT_HUD_AUTOHIDE_SECONDS` (45, zero meaning never),
`CHAT_STORE_SCREENSHOTS` (**off**), `CHAT_RETENTION_DAYS` (14). The retention default is not
hypothetical: roughly 150 KB per turn is about 50 MB after a few hundred interactions.

`store_screenshots` is resolved **once** in the constructor rather than per turn. The setting is
restart-gated anyway, and resolving writes back to the credential store whenever it finds an environment
value — not something to put on a per-interaction path.

## Correctness Properties

### Property 1: The panel is absent from every capture

For any capture taken while the panel is visible and exclusion is active, zero pixels of the panel's
distinctive colour appear. The control case — the same capture with exclusion off — yields a large
non-zero count, so the assertion is known to be capable of failing.

**Validates: Requirements 1.1, 1.3, 1.4**

### Property 2: Exclusion is re-applied on every show

For any sequence of shows and hides, the exclusion call is made once per show. The panel is never
visible without having had exclusion applied in that visibility episode.

**Validates: Requirements 1.2**

### Property 3: The panel is never layered

Static analysis finds no translucent-background attribute, no sub-unity window opacity, and no graphics
effect assignment anywhere in the module. Any of the three would silently disable exclusion.

**Validates: Requirements 2.1, 2.8, 3.1**

### Property 4: The rounded region tracks the geometry

For any resize, the region applied matches the new size. No stale region survives a resize. Applying the
region does not change the capture affinity.

**Validates: Requirements 2.5, 2.6, 2.7**

### Property 5: Reveal leaves no effect attached

For any number of reveal and dismiss cycles, the body's graphics effect is `None` afterwards. Asserted
directly, because a live effect at full opacity is invisible until the next resize and then produces a
black panel.

**Validates: Requirements 3.1, 3.4, 3.6**

### Property 6: Every public entry point is total

For any input to any public method — malformed messages, absent screenshot files, destroyed child
objects, `None` in every argument position — the call returns without raising and logs. Generator:
fuzzed messages across every method.

**Validates: Requirements 4.4, 4.5**

### Property 7: The panel never enters the pipeline's failure path

Static analysis finds no import of the panel or the session store inside the pipeline worker, and no
call path from the worker that can raise into it.

**Validates: Requirements 4.6**

### Property 8: Size clamping is idempotent and in range

For any requested width and height, including negative and enormous values, the result lies within the
configured range in both axes, and clamping an already-clamped value returns it unchanged.

**Validates: Requirements 5.2, 5.4**

### Property 9: The corner region strictly contains the edge region

The corner hit size is strictly greater than the resize margin, and the four corner regions are each at
least that size square. The bezel width is unchanged by the corner size, so the hit test and the visual
are independent.

**Validates: Requirements 5.8, 5.9**

### Property 10: Position is always on the target screen

For any screen geometry, the computed opening position lies within that geometry, horizontally centred
to within a pixel, at the configured top margin.

**Validates: Requirements 5.10**

### Property 11: The collapsed height is derived, never assumed

For any margin configuration, the collapsed height equals the sum of the live margins plus the strip and
header heights, and the header fits entirely within the collapsed body. Generator: several margin
combinations including the one that produced the clipped buttons.

**Validates: Requirements 6.3, 6.4**

### Property 12: Collapsing preserves the bar's position

For any panel position, the window's top-left is identical before and after collapsing. For an upward
expansion, the bar's **bottom** edge is identical before and after expanding.

**Validates: Requirements 6.6, 6.10**

### Property 13: Expanding restores the pre-collapse height exactly

For any height the user had chosen, collapsing then expanding returns that height. Generator: heights
across the whole resizable range, and repeated collapse-expand cycles, which is what exposed both the
re-entrancy and the read-after-hide ordering bugs.

**Validates: Requirements 6.12, 6.13**

### Property 14: The expansion direction cannot change mid-cycle

For any sequence of moves performed while collapsed, expanding uses the direction decided at collapse
time. The panel never jumps.

**Validates: Requirements 6.8, 6.9, 6.11**

### Property 15: A session row is as tall as its content

For any title and subtitle, including strings with descenders and strings long enough to elide, the
row's height is at least the sum of both labels' heights plus its padding, and no label is clipped.

**Validates: Requirements 7.1, 7.3**

### Property 16: Existing tables are untouched

For a database pre-populated with the memory and review tables and their rows, constructing the session
store leaves every existing table definition and every existing row identical.

**Validates: Requirements 9.1, 9.2, 9.3**

### Property 17: Schema creation is idempotent across all three stores

For any order and any number of constructions of all three stores against one database, the schema is
unchanged and no row is modified.

**Validates: Requirements 9.2**

### Property 18: Every write happens on the main thread

Static analysis finds no session-store write reachable from the pipeline worker. The panel's append is
the only caller of the message write.

**Validates: Requirements 9.5, 9.6**

### Property 19: Deletion removes the images too

For any session with screenshots, deleting it leaves no row in either table and no file under its
screenshot folder. Pruning is equivalent to deleting each expired session.

**Validates: Requirements 9.8, 10.11, 10.12**

### Property 20: The screenshot root follows the database

For any database path, the screenshot root is a sibling directory of that path. No write lands outside
it.

**Validates: Requirements 9.9**

### Property 21: Streaming leaves a partial answer, never nothing

For any sequence of deltas followed by an abrupt stop, the stored text equals the concatenation of the
deltas received. No turn is lost.

**Validates: Requirements 9.10**

### Property 22: A suppressed screenshot never lands on disk

For any message with the suppression flag set, no file is written, whatever the storage setting says and
whatever the image is. This is asserted first among the refusals because it is the one whose failure
silently undoes a privacy feature.

**Validates: Requirements 10.1, 10.2**

### Property 23: Storage off means nothing on disk

For any message with the storage setting off, no file is written and the stored screenshot path is
empty. The default configuration writes nothing.

**Validates: Requirements 10.4, 10.5**

### Property 24: A failed write yields no dangling path

For any write failure — unwritable folder, invalid image, locked file — the returned path is empty and
the message row's screenshot column stays empty. No row ever references a file that does not exist.

**Validates: Requirements 10.6, 10.7**

### Property 25: The image and the flag are never persisted or compared

For any two messages differing only in their image or suppression flag, they compare equal, and neither
field appears in any stored row.

**Validates: Requirements 10.8**

### Property 26: A new session leaves the history empty, in place

For any history list, starting a new session leaves that same object empty. The object identity is
unchanged, so a holder of the old reference sees the clear.

**Validates: Requirements 11.1, 11.2, 11.3**

### Property 27: Switching session rebuilds in place

For any stored session and any history list, switching replaces the contents of that same object with
the rebuilt history and returns an equal value.

**Validates: Requirements 11.4**

### Property 28: Rebuilt history matches the pipeline's shape

For any stored messages, every entry has a role of exactly `user` or `assistant` and a content list of
well-formed blocks. No system message appears. Generator: sessions containing every role in every order.

**Validates: Requirements 12.1, 8.4**

### Property 29: The exchange window and the image budget are both respected

For any message count, the rebuilt history contains at most twice the exchange window's entries, and at
most the budget's image blocks. The images belong to the newest eligible turns. Generator: budgets from
zero to more than the available screenshots.

**Validates: Requirements 12.2, 12.5, 12.6, 12.7**

### Property 30: The two window constants agree

The session module's exchange window equals the pipeline's, asserted directly so the deliberate
duplication cannot drift.

**Validates: Requirements 12.3, 12.4**

### Property 31: A coordinate needs both components

For any stored row, a coordinate is produced only when both columns are non-null. A half-null pair
yields no coordinate.

**Validates: Requirements 12.8**

### Property 32: A session boundary needs both conditions

For any pair of application names and any elapsed time, a new session is indicated only when the names
differ **and** the idle threshold is met. Equal names, empty names, and an unparseable timestamp all
yield no boundary. Generator: the cross product of name pairs and elapsed times either side of the
threshold.

**Validates: Requirements 13.1, 13.3, 13.4**

### Property 33: A title never ends mid-word and never exceeds the limit

For any text, the title is no longer than the limit, contains no line break or repeated space, and where
truncated ends at a word boundary followed by an ellipsis.

**Validates: Requirements 13.5, 13.6**

### Property 34: Counters agree with the transcript

For any sequence of turns, the question count equals the number of stored non-blank user messages in the
window, and the recent list is a prefix of those messages in reverse order. No counter is maintained
independently.

**Validates: Requirements 14.3, 14.4, 14.5**

### Property 35: A counter failure is never fatal

For any database error during a count or a recent-turns read, the result is zero or an empty list and
nothing raises.

**Validates: Requirements 14.2, 14.8**

### Property 36: Flagging removes exactly the matching review row

For any flagged reply, the review row matching that question and answer is gone, no other review row is
affected, and a system note appears in the transcript. Against a database with no review table, flagging
still succeeds.

**Validates: Requirements 15.1, 15.3, 15.4**

## Error Handling

| Failure | Response | Why |
|---|---|---|
| Exclusion call raises or returns false | Log, fall back to the hide cycle | Must not crash on every show |
| Operating system too old for exclusion | Report it, let the overlay hide cycle cover the panel | The hide cycle already exists |
| Rounded-region call fails | Square corners | Cosmetic |
| Any public method raises | Log, swallow, return `None` | The user loses the panel, not the answer |
| A message renders badly | That row only | Wrapped per entry point |
| Screenshot file deleted under the panel | Render without a thumbnail | User-controlled folder |
| Suppression flag set | Refuse the write, first check | Otherwise the privacy guard is decorative |
| Storage setting off | Refuse the write | The default |
| Image write fails | Return empty, no dangling path | A row must never reference a missing file |
| Thumbnail write fails | Return empty | The transcript is the feature |
| Locked image during pruning | Skip that session, continue | Runs at startup; must not block launch |
| Counter query fails | Zero, or an empty list | A status card is not worth an interaction |
| Timestamp does not parse | Pass the string through | Better than a guessed date |
| Review table absent | Skip the delete, still add the note | A database predating the journal is valid |
| No screen available when collapsing | Expand downwards | Clipped downwards is still usable |
| Autohide setting unreadable | Use the default | Bounded resolution with a fallback |

## Testing Strategy

Two files, `tests/test_chat_hud.py` and `tests/test_sessions.py`, plus the pure functions which need
neither a window nor a database.

- **The capture-exclusion test carries its own control.** Assert zero marker pixels with exclusion on
  **and** a large count with it off, in the same run. Without the control, a test that renders no marker
  at all passes silently — which is the failure mode that would let this feature break unnoticed until
  someone sees Nimbus pointing at its own panel.
- **The black-panel bug has a direct regression test.** Assert `graphicsEffect() is None` after a reveal.
  The bug was invisible until a resize, so an appearance test would not have caught it.
- **`test_expanding_restores_the_previous_height`** is the test that found two separate ordering bugs —
  the read-after-hide and the signal re-entrancy. Both are the same shape: a value read at the wrong
  moment in a sequence.
- **Both directions of the collapse geometry.** The bar does not move on collapse; the bar's bottom edge
  does not move on an upward expand.
- **`test_existing_memory_and_review_tables_untouched`** against a fixture built to look like a real
  user's database. This is the backward-compatibility gate, and the reason it exists is that users have
  live databases with a year of memory in them.
- **`test_history_window_matches_the_app_constant`** pins two deliberately duplicated constants. The
  duplication is justified; the drift would not be.
- **Every screenshot refusal asserted separately**, and in priority order, because the ordering is the
  correctness property: the suppression check must come before the setting check.
- **The pure functions exhaustively.** `should_auto_new_session` across the cross product of name pairs
  and elapsed times either side of the threshold; `auto_title` across lengths either side of the limit
  with and without word boundaries; `build_history` across every role ordering and every budget from
  zero to over-supply.
- **No test touches** a real window, the real database, the real screenshot folder or a real capture.
  `tmp_path` for storage; the screenshot root is derived from the database path precisely so a test
  cannot write into a developer's profile.
- **Manual verification, required.** Ask a question with the panel visible and confirm the next answer is
  about the application rather than about the panel. Reopen the panel after dismissing it and confirm the
  background is not black. Switch session and confirm no black bars appear down the sides. Collapse near
  the bottom of the screen and confirm the body opens upwards without the bar moving. Resize from each
  corner and confirm the diagonal cursor appears. Turn the screenshot setting on, ask a question in front
  of a password manager, and confirm no image lands on disk.
