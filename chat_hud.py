"""Nimbus chat HUD: a floating panel showing the live conversation (SHELL_AND_CHAT.md §4).

One window, total. Frameless, always-on-top, never focusable, hidden from screen capture, and
fed exclusively through signals from the three non-Qt threads that produce content.

## The load-bearing detail, and the verification that changed the design

A chat panel pinned to the top of the screen would otherwise be **captured in the screenshot**
and fed to the model on the next question: it would see its own previous answer rendered as
UI, might describe it, and might point at the HUD instead of the application underneath.
``exclude_from_capture`` is what prevents that (Invariant 1).

**Verified on this machine, and it contradicted the plan.** ``SetWindowDisplayAffinity``
fails -- returns 0, sets nothing -- on a window carrying ``WS_EX_LAYERED``, which is exactly
what ``WA_TranslucentBackground`` adds. Measured on Windows 10 19045:

    opaque frameless Tool window          -> 1, affinity 0x11
    same window + WA_TranslucentBackground -> 0, affinity 0x00

So the design brief's translucent 92%-alpha body and capture exclusion are **mutually
exclusive**, and exclusion wins: a cosmetic alpha is not worth the model pointing at Nimbus's
own panel. §4's own ⚠ VERIFY #4 anticipated this and named the fallback -- an opaque
``BG_ELEVATED`` body -- so this is the sanctioned path rather than an improvisation.

Rounded corners survive anyway, via ``SetWindowRgn`` with a round-rect region, which needs no
layering and was verified not to disturb the affinity. With exclusion on, an ``mss`` grab
contained **0 of 4,147,200** marker pixels; with it off, 299,789. The control matters: without
it a broken test passes silently.

The same finding bans window-level fades. ``setWindowOpacity(<1.0)`` also forces Qt's layered
path -- ``overlay.py`` already documents that -- so a window-level fade would trade Invariant 1
for polish.

The obvious workaround, a ``QGraphicsOpacityEffect`` on the *body* widget, was tried and is gone:
it forces every repaint through an offscreen buffer, and the entrance path left it attached for the
whole session. Full account above ``reveal``. **This panel has no opacity animation.** The entrance
slides instead, which animates ``pos`` and needs no buffer.

## Threading

Everything visual happens on the Qt main thread. ``sig_message`` / ``sig_delta`` /
``sig_state`` exist as inbound signals so a producer on the pipeline, listener or WebSocket
thread can reach the HUD without touching a widget, the same way ``T4-5``'s captions reach the
overlay through ``sig_caption``. ``append`` and friends stay public because Qt marshals a
cross-thread ``connect`` to a slot on the receiver's thread either way.

## Failure is cosmetic, by construction

Every public entry point is wrapped so an exception degrades to "no chat panel", never "no
answer" (Invariant 10). The pipeline is upstream of the HUD and must never learn that it
exists in any way it can trip over.

## INTEGRATION REQUIRED

Neither ``app.py``, ``config.py`` nor ``nimbus.spec`` is touched by this workstream (§9.1).
The integration pass needs:

**1. ``config.py`` settings** (all restart-gated, so each needs the ``↻`` marker from T4-7 and
an entry in ``RESTART_REQUIRED_SETTINGS``). This module reads them with
``resolve_setting(name, default)`` today, so it works before they are declared -- declaring
them only adds the Settings UI:

    CHAT_HUD                  "on"    master switch
    CHAT_HUD_AUTOHIDE_SECONDS "45"    0 = never auto-hide
    CHAT_STORE_SCREENSHOTS    "off"   PRIVACY: must stay an explicit opt-in
    CHAT_RETENTION_DAYS       "14"    mirrors DIAGNOSTIC_RETENTION_DAYS

**2. ``NimbusApp`` signals** (§6), and the HUD's inbound signals are the connection points:

    sig_chat_message = pyqtSignal(object)  -> hud.sig_message  (a ChatMessage)
    sig_chat_delta   = pyqtSignal(str)     -> hud.sig_delta
    sig_chat_state   = pyqtSignal(str)     -> hud.sig_state    listening|thinking|speaking|idle

**3. Construction** -- after ``QApplication`` exists, and never in ``_pipeline_worker``:

    store = sessions.SessionStore()
    store.prune()                                   # startup retention, best effort
    hud = ChatHud(store=store)
    hud.set_session(sessions.start_new_session(store, app_name, self._history), "")

**4. Outbound wiring.** ``sig_replay`` -> ``self._tts.speak(text)``; ``sig_repoint(x, y)`` ->
the existing Space C -> physical conversion then ``sig_point_at``; ``sig_retry(transcript)``
-> re-run the pipeline with that transcript and no recording; ``sig_new_session`` ->
``sessions.start_new_session(store, app, self._history)``; ``sig_open_session(id)`` ->
``sessions.switch_session(store, id, self._history)``. The two session helpers clear and
rebuild ``_history`` **in place**, which is what makes Invariant 7 true.

**5. Screenshots.** Pass the capture through ``ChatMessage(image=..., privacy_skipped=...)``
and let ``add_message`` decide. ``privacy_skipped`` must be the *same* boolean the Privacy
Guard returned, or Invariant 6 is only decorative.

**6. Capture-exclusion fallback.** If ``hud.needs_hide_for_capture()`` is true (pre-19041
Windows), add ``hud.hide_for_capture()`` / ``hud.show_after_capture()`` to the existing
``sig_hide_overlay`` / ``sig_show_overlay`` slots. Both are no-ops when exclusion is active,
so the calls can be unconditional.

**7. Captions (§6.1).** Suppress ``T4-5``'s caption while ``hud.is_showing_transcript()`` --
two copies of the same words on one screen is noise.

**8. Keyboard.** ``Ctrl+Alt+H`` toggle, ``Ctrl+Alt+N`` new chat, routed through ``hotkey.py``'s
existing listener the way ``T2-2`` added Esc. Not a second ``WH_KEYBOARD_LL`` hook, and not
``parse_hotkey``, which deliberately rejects chords it cannot own.

**9. ``nimbus.spec``.** ``chat_hud`` and ``sessions`` in ``hiddenimports``, and in
``--selftest``'s ``runtime_modules``.
"""
from __future__ import annotations

import ctypes
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import brand
import theme
from sessions import ROLE_NIMBUS, ROLE_SYSTEM, ROLE_USER, ChatMessage


def _log(message: str) -> None:
    """Console line prefixed like the rest of Nimbus, flushed for frozen builds."""
    print(f"CHAT HUD: {message}", flush=True)


# --- Capture exclusion (S-7) -------------------------------------------------

_WDA_NONE = 0x00
_WDA_MONITOR = 0x01
"""Named only so the next reader knows it was considered and rejected.

It hides the window from capture but renders the region **black**, which is worse than the
window itself: the model then sees a black rectangle across the top of the screen and has no
way to know it is not part of the application."""

_WDA_EXCLUDEFROMCAPTURE = 0x11
"""The flag that leaves the window on screen while removing it from capture entirely."""


def exclude_from_capture(hwnd: int) -> bool:
    """Hide a window from screen capture while leaving it visible on screen.

    Verified: 0 of 4,147,200 marker pixels reach an ``mss`` grab, against 299,789 in the same
    run with exclusion off. Requires Windows 10 build 19041+; ``SetWindowDisplayAffinity``
    returns 0 on older builds, which is the fallback signal (see ``needs_hide_for_capture``).

    **It also returns 0 on a ``WS_EX_LAYERED`` window**, which is the reason this HUD is
    opaque -- measured on 19045 and the single most consequential finding in this module.

    Never raises. A ctypes failure here must degrade to the hide/show cycle, not to a crash on
    every ``show()``.
    """
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            ctypes.c_void_p(int(hwnd)), ctypes.c_uint(_WDA_EXCLUDEFROMCAPTURE)))
    except Exception as exc:
        _log(f"capture exclusion call failed - {type(exc).__name__}: {exc}")
        return False


def apply_rounded_region(hwnd: int, width: int, height: int,
                         radius: int = theme.RADIUS_CARD) -> bool:
    """Clip a window to a round-rect via ``SetWindowRgn``.

    How the HUD keeps rounded corners without ``WA_TranslucentBackground``, which would set
    ``WS_EX_LAYERED`` and break capture exclusion. Verified not to disturb the display
    affinity. Must be re-applied on every resize: the region is in window coordinates at a
    fixed size, so a stale one would clip the new geometry.
    """
    try:
        gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
        region = gdi32.CreateRoundRectRgn(
            0, 0, int(width) + 1, int(height) + 1, int(radius) * 2, int(radius) * 2)
        if not region:
            return False
        return bool(user32.SetWindowRgn(int(hwnd), region, True))
    except Exception as exc:
        _log(f"rounded region failed - {type(exc).__name__}: {exc}")
        return False


# --- Geometry and state (pure, so they are testable without a window) --------

HUD_WIDTH, HUD_HEIGHT = 660, 430
"""Sized for breathing room, after 600x340 came back as too congested.

The first pass optimised only for covering as little of the user's work as possible, and got a
panel where the header, the transcript and the footer were pressed against each other with no
air anywhere. Legible beats small: the interior padding grew with this, so the extra 60x90 buys
margins rather than more rows.

Still resizable from any edge, and the chosen size is remembered per monitor."""

MIN_WIDTH, MAX_WIDTH = 460, 1200
MIN_HEIGHT, MAX_HEIGHT = 260, 900
"""The minimum is a real floor, not a guess: below ~460 the footer's status text and its two
pills stop fitting on one line, which is what produced the elided ``idle · ctrl+alt+sp...``."""

TOP_MARGIN = 24

RESIZE_MARGIN = 5
"""The grabbable border, and the width of the visible bezel around the body.

Both at once on purpose: the inset is what leaves bare ``ChatHud`` under the pointer for the
resize hit-test, so a gutter narrower than the hit zone would create a ring that changes the
cursor but is not actually grabbable.

5px rather than 7. At 7 the bezel read as a second frame around the panel -- a box inside a box
-- and it is the same width the shell window uses for the same job, so the two now look related
rather than coincidental."""

CORNER_MARGIN = 16
"""How far from a corner the diagonal resize starts. Deliberately much larger than
``RESIZE_MARGIN``.

Two 5px strips crossing leave a 5x5 corner -- 25 pixels, and one pixel outside it the user
silently gets a single-axis resize instead of the diagonal they were aiming for. That is exactly
what "the corner cursor never shows up" was. 16px gives a 16x16 target, which is close to what
Windows' own frames use, and it costs nothing visually because it changes only the hit-test, not
the bezel."""

HEADER_HEIGHT = 38
FOOTER_HEIGHT = 36
STATE_STRIP_HEIGHT = 3
"""Taller than the 34/30/2 they replaced. The strip in particular: 2px read as a rendering
artefact rather than as the deliberate state indicator it is."""

DEFAULT_AUTOHIDE_SECONDS = 45

STATE_LISTENING, STATE_THINKING = "listening", "thinking"
STATE_SPEAKING, STATE_IDLE = "speaking", "idle"
STATES: tuple[str, ...] = (STATE_LISTENING, STATE_THINKING, STATE_SPEAKING, STATE_IDLE)

_STATE_COLOURS: dict[str, str] = {
    STATE_LISTENING: theme.SUCCESS,
    STATE_THINKING: theme.WARNING,
    STATE_SPEAKING: theme.ACCENT,
    STATE_IDLE: "transparent",
}
"""The 2px top strip's colour per interaction state.

Green listening, amber thinking, orange speaking, invisible idle -- the same information the
overlay conveys at the cursor, available without looking away from the panel. ``listening``
stays green for the reason ``theme.OVERLAY_STATE_RGB`` gives: recording indicators are green
everywhere, and the user needs certainty that the microphone is live more than they need
palette tidiness."""

_STATE_LABELS: dict[str, str] = {
    STATE_LISTENING: "listening\u2026",
    STATE_THINKING: "thinking\u2026",
    STATE_SPEAKING: "speaking",
    STATE_IDLE: "idle",
}


def state_colour(state: str) -> str:
    """Strip colour for a state, falling back to idle for anything unrecognised."""
    return _STATE_COLOURS.get((state or "").strip().lower(), _STATE_COLOURS[STATE_IDLE])


def clamp_size(width: int, height: int) -> tuple[int, int]:
    """Clamp a requested size into the resizable range.

    Users with long answers want it bigger and a fixed panel feels like a toy, but an
    unbounded drag produces a 3000px-wide panel covering the application the user is asking
    about -- which defeats the point of the product.
    """
    return (
        max(MIN_WIDTH, min(int(width), MAX_WIDTH)),
        max(MIN_HEIGHT, min(int(height), MAX_HEIGHT)),
    )


def top_centre_position(geometry, width: int = HUD_WIDTH,
                        margin: int = TOP_MARGIN) -> tuple[int, int]:
    """Top-centre of a screen's available geometry, in that screen's logical coords."""
    return (
        geometry.left() + max(0, (geometry.width() - int(width)) // 2),
        geometry.top() + margin,
    )


def configured_hotkey() -> str:
    """The **real** configured push-to-talk chord, never a hardcoded one.

    The empty state is the only surface where the core interaction can be explained at the
    moment it is relevant, so telling a user who remapped the hotkey the wrong chord is worse
    than saying nothing. Read through ``resolve_setting`` rather than ``config.HOTKEY`` so a
    Settings change is picked up without a restart.
    """
    try:
        from config import HOTKEY, resolve_setting
        return resolve_setting("HOTKEY", default=HOTKEY) or HOTKEY
    except Exception:
        return "ctrl+alt+space"


def format_hotkey(chord: str) -> str:
    """``"ctrl+alt+space"`` -> ``"Ctrl+Alt+Space"``. Presentation only, never parsing."""
    return "+".join(part.strip().capitalize() for part in (chord or "").split("+") if part.strip())


def _never_raises(method):
    """Wrap a public entry point so a HUD bug cannot reach the pipeline (Invariant 10).

    The pipeline emits into the HUD and moves on. If a render path throws -- a malformed
    message, a deleted screenshot, a Qt object already destroyed -- the user should lose the
    chat panel for that turn, not the answer they asked for. Logged rather than silent,
    because an invisible swallowed exception is how this feature would rot unnoticed.
    """
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            _log(f"{method.__name__} failed - {type(exc).__name__}: {exc}")
            return None
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


# --- Small styled parts ------------------------------------------------------

def _hairline(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("AccentRule")
    line.setFixedHeight(1)
    # Accent-led, fading to the ordinary border within the first quarter. Styled by object name
    # from ``theme.build_qss`` -- the same rule the shell's title-bar divider uses -- rather than
    # by a local stylesheet, so the two surfaces cannot end up with different dividers.
    line.setStyleSheet(f"QFrame#AccentRule {{ background: {theme.accent_rule()}; }}")
    return line


MARK_SIZE = 16


class _BrandMark(QWidget):
    """The accent dot in the header, painted rather than typed.

    It was a ``\u25c9`` text glyph, which is a hinted bitmap at 10pt -- so it rendered as a
    visibly blocky ring with hard stair-steps on the curve, and looked worse the higher the
    display's DPI. A font glyph is the wrong tool for a logo mark: its weight, its optical size
    and its baseline all belong to the typeface, none of which is under our control.

    Drawn as two antialiased circles with the metallic accent gradient, it is smooth at any scale
    and matches the orange used everywhere else by construction.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(MARK_SIZE, MARK_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QBrush, QLinearGradient, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        outer = QRectF(1.0, 1.0, MARK_SIZE - 2.0, MARK_SIZE - 2.0)
        ring = QLinearGradient(0, 0, MARK_SIZE, MARK_SIZE)
        ring.setColorAt(0.0, theme.qcolor(theme.ACCENT_LIGHT))
        ring.setColorAt(1.0, theme.qcolor(theme.ACCENT_DEEP))
        pen = QPen(QBrush(ring), 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(outer)

        core = QRectF(0, 0, MARK_SIZE * 0.42, MARK_SIZE * 0.42)
        core.moveCenter(outer.center())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.qcolor(theme.ACCENT)))
        painter.drawEllipse(core)
        painter.end()


PILL_HEIGHT = 24
"""Footer pill height. Fixed, so the radius can be exactly half of it -- ``RADIUS_PILL`` is 999
and Qt clamps that to half the *shorter* side, which only gives a true capsule when the height is
known. Left to the layout, the two pills came out different heights and read as rectangles with
rounded corners rather than as capsules."""


def _pill_button(parent: QWidget, text: str, tooltip: str = "",
                 accent: bool = False) -> QPushButton:
    """A capsule text button for the footer's two actions.

    "+ New chat" and the auto-hide line were bare coloured text on a transparent background,
    which read as two stray labels rather than as controls -- and one of them being clickable
    while looking identical to a status line was the worst of both.

    ``accent`` gives the primary action a warm tint at rest, so "New chat" is the one your eye
    lands on and the auto-hide toggle recedes. Two identical pills side by side make the user
    read both to find out which one they want.
    """
    button = QPushButton(text, parent)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedHeight(PILL_HEIGHT)
    if tooltip:
        button.setToolTip(tooltip)
    radius = PILL_HEIGHT // 2
    # Opaque fills throughout, like every other surface on this panel. A translucent background
    # composites over whatever the parent painted, and on a widget inside a scroll area or an
    # unstyled container that is Windows' near-white palette default -- which is how the session
    # list ended up unreadable. Pre-blended colours make the result the same everywhere.
    rest_bg = theme.PANEL_HOVER if accent else theme.PANEL_RAISED
    rest_border = theme.ACCENT_HAIR if accent else theme.BORDER
    rest_text = theme.ACCENT if accent else theme.TEXT_SECONDARY
    button.setStyleSheet(
        f"QPushButton {{ background: {rest_bg};"
        f" border: 1px solid {rest_border};"
        f" border-radius: {radius}px;"
        f" padding: 0px 14px; color: {rest_text};"
        f" font-size: {theme.FONT_SMALL}pt;"
        f" font-weight: {theme.WEIGHT_MEDIUM}; }}"
        f"QPushButton:hover {{ background: {theme.PANEL_HOVER_STRONG};"
        f" border-color: {theme.ACCENT}; color: {theme.TEXT_PRIMARY}; }}"
        f"QPushButton:pressed {{ background: {theme.BG_SUNKEN};"
        f" border-color: {theme.ACCENT_HAIR}; }}"
    )
    return button


WINDOW_BUTTON = 26
"""Window-control size in the HUD header. Larger than the 24px used for per-message controls,
because these three are the ones the user reaches for deliberately."""


def _icon_button(parent: QWidget, glyph: str, tooltip: str,
                 checkable: bool = False, danger: bool = False,
                 size: int = 24) -> QPushButton:
    """A square glyph button with a **visible resting state**.

    Text glyphs rather than icon assets: the HUD needs eight tiny controls, and eight PNGs at
    four DPI scales is a packaging problem in exchange for nothing the user can see.

    These were transparent with ``TEXT_SECONDARY`` glyphs, which on the HUD's dark body meant
    the close and minimise controls were effectively invisible until hovered -- users reported
    not being able to find them at all. Now every one has a chip background and a border at
    rest, so it reads as a button before the pointer arrives, and ``danger`` gives close its own
    red hover instead of the same grey as everything else.
    """
    button = QPushButton(glyph, parent)
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(size, size)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    hover = theme.DANGER if danger else theme.PANEL_HOVER
    button.setStyleSheet(
        f"QPushButton {{ background: {theme.PANEL_RAISED};"
        f" border: 1px solid {theme.BORDER};"
        f" border-radius: {theme.RADIUS_CONTROL - 2}px; color: {theme.TEXT_PRIMARY};"
        f" font-size: {theme.FONT_SMALL}pt; font-weight: {theme.WEIGHT_SEMIBOLD};"
        f" padding: 0px; }}"
        f"QPushButton:hover {{ background: {hover};"
        f" border-color: {theme.BORDER_STRONG}; color: {theme.TEXT_PRIMARY}; }}"
        f"QPushButton:pressed {{ background: {theme.BG_SUNKEN}; }}"
        f"QPushButton:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT};"
        f" color: {theme.ON_ACCENT}; }}"
    )
    return button


class _MessageRow(QWidget):
    """One turn: a leading rule, the text, a collapsible screenshot, hover controls.

    **No bubble.** Speaker bubbles at HUD width waste horizontal space and read as a phone
    messenger, which is the wrong reference for a desktop utility. User turns get a
    ``BORDER`` rule and secondary text; Nimbus turns get an ``ACCENT_HAIR`` rule and primary
    text (§2.7).

    Controls appear on hover rather than permanently because four glyphs against every turn
    turns a transcript into a toolbar.
    """

    def __init__(self, message: ChatMessage, hud: "ChatHud") -> None:
        super().__init__(hud._messages_host)
        self._hud = hud
        self.message = message
        self._expanded = False

        rule_colour = {
            ROLE_USER: theme.BORDER,
            ROLE_NIMBUS: theme.ACCENT_HAIR,
            ROLE_SYSTEM: "transparent",
        }.get(message.role, theme.BORDER)
        text_colour = {
            ROLE_USER: theme.TEXT_SECONDARY,
            ROLE_NIMBUS: theme.TEXT_PRIMARY,
            ROLE_SYSTEM: theme.TEXT_MUTED,
        }.get(message.role, theme.TEXT_PRIMARY)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(theme.SPACE[3], theme.SPACE[0], theme.SPACE[3], theme.SPACE[0])
        outer.setSpacing(theme.SPACE[2])

        rule = QFrame(self)
        rule.setFixedWidth(2)
        rule.setStyleSheet(f"background: {rule_colour};")
        outer.addWidget(rule)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SPACE[0])
        outer.addLayout(column, 1)

        speaker = QLabel(
            {ROLE_USER: "you", ROLE_NIMBUS: "nimbus"}.get(message.role, ""), self)
        speaker.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FONT_MICRO}pt;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD};"
        )
        if speaker.text():
            column.addWidget(speaker)

        self._text_label = QLabel(message.text, self)
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_label.setStyleSheet(
            f"color: {text_colour}; font-size: {theme.FONT_BODY}pt;")
        column.addWidget(self._text_label)

        if message.error:
            column.addLayout(self._error_row(message))
        if message.screenshot:
            self._disclosure = QPushButton("\u25b8 screenshot", self)
            self._disclosure.setCursor(Qt.CursorShape.PointingHandCursor)
            self._disclosure.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._disclosure.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; text-align: left;"
                f" color: {theme.TEXT_MUTED}; font-size: {theme.FONT_SMALL}pt; padding: 0; }}"
                f"QPushButton:hover {{ color: {theme.ACCENT}; }}"
            )
            self._disclosure.clicked.connect(self.toggle_screenshot)
            column.addWidget(self._disclosure, 0, Qt.AlignmentFlag.AlignLeft)
            self._thumbnail = QLabel(self)
            self._thumbnail.setVisible(False)
            self._thumbnail_loaded = False
            column.addWidget(self._thumbnail, 0, Qt.AlignmentFlag.AlignLeft)

        self._controls = self._control_row(message)
        column.addWidget(self._controls, 0, Qt.AlignmentFlag.AlignLeft)
        self._controls.setVisible(False)

    # --- sub-rows ---

    def _error_row(self, message: ChatMessage) -> QHBoxLayout:
        """An inline failure plus a Retry that does not re-record.

        Today a failure is a toast that vanishes and retrying means asking the whole question
        again from scratch. The transcript is already known, so the retry costs the user
        nothing but the model call.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[1])
        label = QLabel(message.error, self)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {theme.DANGER}; font-size: {theme.FONT_SMALL}pt;")
        row.addWidget(label, 1)
        self._retry_button = QPushButton("Retry", self)
        self._retry_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._retry_button.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_CONTROL}px; padding: 2px 10px;"
            f" color: {theme.TEXT_PRIMARY}; font-size: {theme.FONT_SMALL}pt; }}"
            f"QPushButton:hover {{ background: {theme.BG_HOVER};"
            f" border-color: {theme.BORDER_STRONG}; }}"
        )
        self._retry_button.clicked.connect(self.retry)
        row.addWidget(self._retry_button, 0)
        return row

    def _control_row(self, message: ChatMessage) -> QWidget:
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[0])

        self.copy_button = _icon_button(host, "\u29c9", "Copy")
        self.copy_button.clicked.connect(self.copy)
        row.addWidget(self.copy_button)

        self.replay_button = None
        self.repoint_button = None
        self.wrong_button = None
        if message.role == ROLE_NIMBUS:
            self.replay_button = _icon_button(host, "\u27f2", "Replay")
            self.replay_button.clicked.connect(self.replay)
            row.addWidget(self.replay_button)
            # Re-point only exists when there is a coordinate to fly to. A dead button that
            # looks live is worse than an absent one, and the user cannot tell which turns
            # pointed at something without trying.
            if message.coordinate is not None:
                self.repoint_button = _icon_button(host, "\u25ce", "Show me again")
                self.repoint_button.clicked.connect(self.repoint)
                row.addWidget(self.repoint_button)
            self.wrong_button = _icon_button(host, "\u2691", "That was wrong")
            self.wrong_button.clicked.connect(self.flag_wrong)
            row.addWidget(self.wrong_button)
        row.addStretch(1)
        return host

    # --- interaction ---

    def enterEvent(self, event) -> None:
        self._controls.setVisible(True)
        self._hud.note_activity()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._controls.setVisible(False)
        super().leaveEvent(event)

    def append_text(self, text: str) -> None:
        self.message = ChatMessage(
            role=self.message.role,
            text=self.message.text + text,
            created_at=self.message.created_at,
            screenshot=self.message.screenshot,
            coordinate=self.message.coordinate,
            message_id=self.message.message_id,
            error=self.message.error,
        )
        self._text_label.setText(self.message.text)

    def copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.message.text)

    def replay(self) -> None:
        self._hud.sig_replay.emit(self.message.text)

    def repoint(self) -> None:
        """Re-fly the cursor to this turn's stored coordinate. **No model call.**

        The pointer fades after a few seconds, and re-pointing is currently a whole new
        request -- a round trip and a token spend to re-show something already known. The
        coordinate is in the message, so this is signal plumbing.
        """
        if self.message.coordinate is not None:
            self._hud.sig_repoint.emit(*self.message.coordinate)

    def retry(self) -> None:
        self._hud.sig_retry.emit(self._hud.transcript_before(self))

    def flag_wrong(self) -> None:
        self._hud.flag_wrong(self)

    def toggle_screenshot(self) -> None:
        """Expand or collapse the thumbnail. Collapsed by default.

        A transcript of images is unreadable, and most turns' screenshots are never looked at.
        """
        self._expanded = not self._expanded
        self._disclosure.setText(
            ("\u25be " if self._expanded else "\u25b8 ") + "screenshot")
        if self._expanded and not self._thumbnail_loaded:
            pixmap = self._hud.thumbnail_for(self.message)
            if pixmap is not None:
                self._thumbnail.setPixmap(pixmap)
            # Marked loaded even on failure, so a missing file is not re-read on every click.
            self._thumbnail_loaded = True
        self._thumbnail.setVisible(self._expanded)


PICKER_WIDTH = 380
PICKER_MAX_ROWS = 7
"""Session picker geometry.

380px wide, up from 300. A row carries a session title *and* an app name and a timestamp beneath
it, and at 300px the two-line button clipped its own second line.

The list scrolls past seven rows -- seven is where the popover stops fitting comfortably inside a
430px panel.

**Row height is measured, not declared.** It used to be a literal 46, and the rows still clipped:
the row button's height is governed by ``min-height`` in the application stylesheet's
``QPushButton`` rule, which **overrides** ``setMinimumHeight`` -- so the button got 20px of
content box plus 8px of padding, 28px, for two 17px lines. Measured on this machine at 10pt Segoe
UI Variable Text. Deriving it from the live font metrics also means it survives a different font
or DPI, which a literal could not."""

PICKER_CLOSE_SIZE = 22
"""The picker's close button. Smaller than the header's ``WINDOW_BUTTON`` controls: this one closes
a popover, not the panel, and it should not compete with them."""

PICKER_ROW_PADDING = 5
"""Vertical padding inside a row, top and bottom. Part of the row-height calculation, so it is
named rather than repeated in the stylesheet and the arithmetic."""


class _SessionRow(QFrame):
    """One session in the picker: a title, a subtitle beneath it, and a delete button.

    ## Why this is a QFrame and not a QPushButton

    Three attempts at a button failed to fit two lines of text, each for a different reason, and
    all of them clipped the descenders:

    1. ``QPushButton("title\\nsubtitle")`` plus ``setMinimumHeight`` -- the application
       stylesheet's ``QPushButton { min-height: 20px }`` **overrides** the widget property, so
       the button got 28px for 42px of text.
    2. The same with ``min-height`` raised in the button's own stylesheet -- closer, but Qt's
       min-height governs the *content box* and the arithmetic never agreed with the layout:
       42px allocated against 44px needed.
    3. A ``QPushButton`` containing a layout of two ``QLabel``s -- a styled ``QPushButton``
       computes its size hint from the style's contents size and **ignores a child layout**, so
       the labels were squeezed to 5px each.

    A ``QFrame`` has none of that machinery. Its layout's size hint is its size hint, the labels
    report their own heights, and the row is exactly as tall as its content. Clicking is one
    ``mousePressEvent``, which is less code than any of the three attempts above.

    ``WA_Hover`` is what makes the QSS ``:hover`` rule fire on a plain frame; without it the row
    would never highlight.
    """

    clicked = pyqtSignal()

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{title}\n{subtitle}")
        self.setStyleSheet(
            f"QFrame#SessionRow {{ background: {theme.PANEL_TOP};"
            f" border: none; border-radius: {theme.RADIUS_CONTROL}px; }}"
            f"QFrame#SessionRow:hover {{ background: {theme.PANEL_HOVER}; }}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(
            theme.SPACE[1], PICKER_ROW_PADDING, theme.SPACE[0], PICKER_ROW_PADDING)
        row.setSpacing(theme.SPACE[0])

        stacked = QVBoxLayout()
        stacked.setContentsMargins(0, 0, 0, 0)
        stacked.setSpacing(1)
        self.title_label = QLabel(title, self)
        self.subtitle_label = QLabel(subtitle, self)
        for label, colour in ((self.title_label, theme.TEXT_PRIMARY),
                              (self.subtitle_label, theme.TEXT_MUTED)):
            # Mouse-transparent so the whole row is one click target, including the text.
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            label.setStyleSheet(
                f"background: transparent; color: {colour};"
                f" font-size: {theme.FONT_SMALL}pt;")
            # Not word-wrapped: a wrapped title makes rows different heights, and a ragged list
            # is harder to scan than one with a few clipped titles. The tooltip carries the rest.
            label.setWordWrap(False)
            stacked.addWidget(label)
        row.addLayout(stacked, 1)

        self.delete_button = _icon_button(self, "\u2715", "Delete session")
        row.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _SessionPicker(QFrame):
    """Session switcher: search, recent sessions, + New chat, per-row delete.

    Opened from the header's right-click menu ("Switch session..."). It used to hang off a label
    in the header, which is why it was sized for a 300px column beside that label; it is now a
    standalone popover centred on the panel and sized to its content.
    """

    def __init__(self, hud: "ChatHud") -> None:
        super().__init__(hud)
        self._hud = hud
        self.setObjectName("Popover")
        # Warm, like the panel it sits on. It was `BG_RAISED`, part of the cool ramp, which put a
        # blue-black popover on top of a warm-black panel.
        self.setStyleSheet(
            f"QFrame#Popover {{ background: {theme.panel_gradient()};"
            f" border: 1px solid {theme.BORDER_STRONG};"
            f" border-top: 1px solid {theme.SHEEN};"
            f" border-radius: {theme.RADIUS_CARD}px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE[2], theme.SPACE[2], theme.SPACE[2], theme.SPACE[2])
        layout.setSpacing(theme.SPACE[1])

        # Heading row, with a close button. Before this the only ways out were picking a session or
        # starting a new one -- both of which change what you are looking at. Opening the switcher
        # to check what else is there and then staying put was not expressible.
        heading_row = QWidget(self)
        heading_layout = QHBoxLayout(heading_row)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(theme.SPACE[1])

        heading = QLabel("Sessions", heading_row)
        heading.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SMALL}pt;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD}; letter-spacing: 0.6px;"
        )
        heading_layout.addWidget(heading)
        heading_layout.addStretch(1)

        self.close_button = _icon_button(
            heading_row, "\u2715", "Close, and stay in this conversation",
            danger=True, size=PICKER_CLOSE_SIZE)
        self.close_button.clicked.connect(self.hide)
        heading_layout.addWidget(self.close_button)
        layout.addWidget(heading_row)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search sessions")
        self.search.setStyleSheet(
            f"background: {theme.BG_SUNKEN}; border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_CONTROL}px; padding: 4px 8px;"
            f" color: {theme.TEXT_PRIMARY};"
        )
        self.search.textChanged.connect(self.reload)
        layout.addWidget(self.search)

        self._rows_host = QWidget(self)
        self._rows_host.setStyleSheet(f"background: {theme.PANEL_TOP};")
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(theme.SPACE[0] // 2)

        # Scrolled, so a user with fifty sessions gets a list rather than a popover taller than
        # their screen. Height is set from the row count in `reload`, up to `PICKER_MAX_ROWS`.
        self._rows_scroll = QScrollArea(self)
        self._rows_scroll.setWidget(self._rows_host)
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # **Opaque, not transparent.** Measured: with only `QScrollArea { background:
        # transparent; }` the rows rendered in Windows' near-white default palette colour,
        # painted by the *viewport* -- which a stylesheet on the scroll area alone does not
        # reach. The session list came out white text on a white row.
        #
        # Naming the viewport and the host explicitly, with an opaque fill, removes the palette
        # from the question entirely. `PANEL_TOP` is the popover gradient's mid stop, so the flat
        # list region blends into it rather than reading as a patch.
        self._rows_scroll.setStyleSheet(
            f"QScrollArea {{ background: {theme.PANEL_TOP}; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {theme.PANEL_TOP}; }}"
        )
        self._rows_scroll.viewport().setAutoFillBackground(True)
        layout.addWidget(self._rows_scroll, 1)

        self._empty = QLabel(
            "No other sessions yet. Each conversation you have becomes one.", self)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FONT_SMALL}pt;"
            f" padding: {theme.SPACE[1]}px {theme.SPACE[0]}px;")
        layout.addWidget(self._empty)

        # A separator, then New chat on a **solid** fill.
        #
        # It was `background: transparent`, and with the session list scrolling right up behind it
        # the two overlapped: row text showed through the button, which read as a rendering fault.
        # Transparency is only safe over something you know, and the bottom of a scrolling list is
        # the opposite of that. `PANEL_RAISED` is opaque and one step up from the list, so the
        # action row reads as sitting on top of the list rather than in it.
        rule = QFrame(self)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {theme.BORDER};")
        layout.addWidget(rule)

        new_chat = QPushButton("+  New chat", self)
        new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        new_chat.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        new_chat.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL_RAISED};"
            f" border: 1px solid {theme.BORDER}; text-align: left;"
            f" border-radius: {theme.RADIUS_CONTROL}px;"
            f" padding: 7px 10px; color: {theme.ACCENT};"
            f" font-weight: {theme.WEIGHT_SEMIBOLD}; }}"
            f"QPushButton:hover {{ background: {theme.PANEL_HOVER};"
            f" border-color: {theme.ACCENT_HAIR}; }}"
            f"QPushButton:pressed {{ background: {theme.BG_SUNKEN}; }}"
        )
        new_chat.clicked.connect(self._new_chat)
        layout.addWidget(new_chat)
        self.setFixedWidth(PICKER_WIDTH)

    def row_height(self) -> int:
        """Height one session row occupies, measured from a real row rather than calculated.

        Asking a built row for its ``sizeHint`` is the only measurement that cannot disagree with
        what the layout will do -- which two rounds of arithmetic against Qt's stylesheet box
        model did. Falls back to a font-metrics estimate before any row exists, which is only the
        case while the list is empty and nothing is being sized.
        """
        for index in range(self._rows.count()):
            widget = self._rows.itemAt(index).widget()
            if widget is not None:
                return widget.sizeHint().height()
        return self.fontMetrics().lineSpacing() * 2 + PICKER_ROW_PADDING * 2

    def set_search(self, text: str) -> None:
        self.search.setText(text)

    def row_count(self) -> int:
        """How many session rows are listed.

        Counts *widgets*, not layout items: the list ends with a stretch so short lists sit at
        the top of the scroll area instead of spreading out, and a stretch is an item too.
        """
        return sum(
            1 for index in range(self._rows.count())
            if self._rows.itemAt(index).widget() is not None
        )

    def reload(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        records = list(self._hud.session_records(self.search.text()))
        for record in records:
            self._rows.addWidget(self._session_row(record))
        self._rows.addStretch(1)

        # Sized from the content rather than left to `adjustSize`, which measured the collapsed
        # two-line buttons and produced a popover too short to read.
        self._empty.setVisible(not records)
        self._rows_scroll.setVisible(bool(records))
        visible_rows = min(len(records), PICKER_MAX_ROWS)
        # Row height plus the spacing between rows, so the last visible row is not half-shown --
        # a clipped row at the bottom of a list reads as a rendering fault rather than as "scroll
        # for more".
        row_pitch = self.row_height() + self._rows.spacing()
        wanted = max(0, visible_rows * row_pitch)
        # A **maximum**, not a fixed height, and that is the other half of the overlap.
        #
        # `_position_picker` clamps the popover to the space below the header when it will not fit
        # on screen. A fixed-height list cannot give any of that back, so the layout overflowed its
        # own frame and pushed the New chat row out through the bottom edge -- which is what "the
        # new chat is overlapping the text behind it" was. With a maximum it compresses and scrolls
        # instead, and the action row stays where it belongs.
        self._rows_scroll.setMinimumHeight(0)
        self._rows_scroll.setMaximumHeight(wanted)
        self.adjustSize()

    def _session_row(self, record: dict) -> QWidget:
        session_id = int(record["id"])
        title = record.get("title") or "Untitled session"
        badge = (record.get("app_name") or "").strip()
        when = relative_time(record.get("last_used_at", ""))
        subtitle = " \u00b7 ".join(part for part in (badge, when) if part)

        host = _SessionRow(title, subtitle, self._rows_host)
        host.clicked.connect(lambda: self._open(session_id))
        host.delete_button.clicked.connect(lambda: self._delete(session_id))
        host.session_id = session_id
        return host

    def _open(self, session_id: int) -> None:
        self.hide()
        self._hud.sig_open_session.emit(session_id)

    def _delete(self, session_id: int) -> None:
        self._hud.delete_session(session_id)
        self.reload()

    def _new_chat(self) -> None:
        self.hide()
        self._hud.sig_new_session.emit()


def relative_time(stamp: str, now: datetime | None = None) -> str:
    """``"2m ago"`` from an ISO timestamp. Empty string when unparseable.

    Relative time is what the user thinks in when finding a conversation; an absolute
    timestamp forces them to do the subtraction.
    """
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ""
    seconds = int(((now or datetime.now()) - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


# --- The HUD -----------------------------------------------------------------

class ChatHud(QWidget):
    """The floating chat panel (``S-6``, ``S-6b``, ``S-7``).

    Constructible with no arguments and fully testable with **no ``NimbusApp`` import**.
    Everything it needs from the application arrives as an injected dependency or leaves as a
    signal, exactly as ``stt.py``, ``realtime.py`` and ``gemini_live.py`` already do. If this
    module ever needs to import ``app``, the seam is wrong -- and it would also make the HUD
    impossible to test without starting the whole application.

    ``store`` defaults to **None**, which means "render, persist nothing". That is deliberate:
    defaulting to a real ``SessionStore`` would make merely constructing a HUD in a test write
    to the developer's live ``~/.nimbus/index.db``.
    """

    # --- outbound: things the application must act on ---
    sig_new_session = pyqtSignal()
    sig_open_session = pyqtSignal(int)
    sig_repoint = pyqtSignal(int, int)
    """Space C coordinate. ``app.py`` already owns the Space C -> physical conversion."""
    sig_replay = pyqtSignal(str)
    sig_retry = pyqtSignal(str)
    sig_flag_wrong = pyqtSignal(int)
    """Message id. The store already suppresses the review item; this lets the shell react."""

    # --- inbound: the only safe way in from a non-Qt thread ---
    sig_message = pyqtSignal(object)
    sig_delta = pyqtSignal(str)
    sig_state = pyqtSignal(str)
    """Connected internally to ``append`` / ``stream_delta`` / ``set_state``.

    The HUD is fed from the pipeline worker, the pynput listener and the STT WebSocket thread.
    Touching a QWidget from any of them is the invariant that produces intermittent crashes
    rather than clean failures, so these signals are the entry points and Qt does the
    marshalling (``T4-5``'s captions prove the path)."""

    def __init__(
        self,
        store=None,
        hotkey: str | None = None,
        autohide_seconds: int | None = None,
        exclude=exclude_from_capture,
        screen_geometry_fn=None,
        positions_path=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._hotkey = hotkey if hotkey is not None else configured_hotkey()
        self._autohide_seconds = (
            self._resolve_autohide() if autohide_seconds is None else int(autohide_seconds))
        self._exclude = exclude
        self._screen_geometry_fn = screen_geometry_fn or self._default_screen_geometry
        self._positions_path = self._default_positions_path(positions_path)

        self.session_id: int = 0
        self.state: str = STATE_IDLE
        self.capture_exclusion_active: bool = False
        self._rows: list[_MessageRow] = []
        self._open_row: _MessageRow | None = None
        self._collapsed = False
        self._pinned = False
        self._auto_reveal = True
        """Whether an interaction may bring the panel back on screen. See ``set_auto_reveal``."""
        self._picker: _SessionPicker | None = None
        self._drag_origin: QPoint | None = None
        self._resize_origin: tuple[int, int, int] | None = None
        self._slide = None

        # Frameless, always-on-top, Tool, and never focusable.
        #
        # `Tool` keeps it out of the taskbar and Alt-Tab (WS_EX_TOOLWINDOW).
        # `WindowDoesNotAcceptFocus` plus `WA_ShowWithoutActivating` is what stops it stealing
        # focus mid-typing -- and Nimbus appears *while* the user is working in another
        # application by definition, so focus theft here is not an edge case.
        #
        # Note the absence of `WA_TranslucentBackground`: it sets WS_EX_LAYERED, which makes
        # SetWindowDisplayAffinity fail outright (see the module docstring). Opaque body,
        # rounded by SetWindowRgn.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("Nimbus chat")
        self.setMouseTracking(True)

        self._build()
        self.resize(HUD_WIDTH, HUD_HEIGHT)
        self.reset_position()
        self.restore_position()

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)

        self.sig_message.connect(self.append)
        self.sig_delta.connect(self.stream_delta)
        self.sig_state.connect(self.set_state)

    # --- construction ---

    def _build(self) -> None:
        shell = QVBoxLayout(self)
        # A resize gutter, and the reason resizing did not work before.
        #
        # `_edges_at` was computing the right edges all along, but every pixel of the panel was
        # covered by a child widget. Mouse *move* events only reach a widget with mouse tracking
        # on, and children do not forward hover to a parent, so the cursor never changed near an
        # edge -- there was nothing to tell the user where to grab, and nothing under the pointer
        # that would have reported it.
        #
        # Insetting the body leaves a ring of bare ``ChatHud`` all the way round, which is what
        # `mouseMoveEvent` needs. Same approach as the shell window's `RESIZE_MARGIN`, and like
        # there it doubles as a bezel -- here an accent-tinted one, so the panel reads as having
        # a warm edge rather than a black gap.
        shell.setContentsMargins(
            RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN)
        shell.setSpacing(0)
        self.setMouseTracking(True)
        self.setObjectName("HudFrame")
        # The panel's one and only border lives here, on the outer edge. Radius matches the
        # ``SetWindowRgn`` corner radius so the painted corner and the clipped corner agree --
        # a rounded region over a square border leaves the border's corners sliced off.
        self.setStyleSheet(
            f"QWidget#HudFrame {{ background: {theme.panel_frame_gradient()};"
            f" border: 1px solid {theme.BORDER_STRONG};"
            f" border-radius: {theme.RADIUS_CARD}px; }}"
        )

        self._body = QWidget(self)
        self._body.setObjectName("HudBody")
        # Shaded, not flat, and for the same reason the shell's cards are: a floating panel on
        # top of someone else's application has to read as an object sitting above it.
        #
        # **No border of its own.** It had one, and once the resize gutter was inset around it
        # the panel had two visible frames -- the outer bezel and this -- reading as a box inside
        # a box. The single border now lives on ``#HudFrame`` (the outer edge, where a window's
        # border belongs) and the body is separated from the gutter by tone alone: the body's
        # gradient is lighter than the bezel, which is all an inset edge needs. Only the top
        # ``SHEEN`` line survives, because that is the lit edge rather than a frame.
        self._body.setStyleSheet(
            f"QWidget#HudBody {{ background: {theme.panel_gradient()};"
            f" border: none;"
            f" border-top: 1px solid {theme.SHEEN}; }}"
        )
        shell.addWidget(self._body)

        body = QVBoxLayout(self._body)
        body.setContentsMargins(1, 1, 1, 1)
        body.setSpacing(0)

        self._strip = QFrame(self._body)
        self._strip.setFixedHeight(STATE_STRIP_HEIGHT)
        self._strip.setStyleSheet(f"background: {state_colour(STATE_IDLE)};")
        body.addWidget(self._strip)

        body.addWidget(self._build_header())
        # Kept as a list so ``set_collapsed`` can hide them: two stray hairlines under a
        # collapsed bar read as a rendering fault.
        self._hairlines = [_hairline(self._body), _hairline(self._body)]
        body.addWidget(self._hairlines[0])
        body.addWidget(self._build_messages(), 1)
        body.addWidget(self._hairlines[1])
        body.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        header = QWidget(self._body)
        header.setObjectName("HudHeader")
        header.setFixedHeight(HEADER_HEIGHT)
        header.setCursor(Qt.CursorShape.SizeAllCursor)
        # Its own gradient, a shade lighter than the body, so the drag handle is visibly a
        # separate strip. Without it the header and the transcript are one flat field and there
        # is no cue that the top of the panel is the part you grab.
        # The same warm-left-edge treatment as the shell's title bar, so the two chromes are
        # recognisably the same material. The pointer mark sits in that warmth.
        header.setStyleSheet(
            f"QWidget#HudHeader {{ background: {theme.chrome_tint()}; }}")
        row = QHBoxLayout(header)
        row.setContentsMargins(theme.SPACE[2], 0, theme.SPACE[0], 0)
        row.setSpacing(theme.SPACE[0])

        # The pointer, not the abstract mark.
        #
        # This panel *is* the conversation with the thing the pointer represents, so the pointer
        # reads as "Nimbus is here" more directly. Trimmed by ``brand.py`` before scaling -- the
        # source is a 1536x1024 canvas with the artwork in the middle, so scaling the file
        # directly gave a pointer about a third of the requested size floating in its own padding.
        mark = brand.mark_label(brand.HUD_MARK_HEIGHT, header, asset=brand.CURSOR)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(theme.SPACE[0])
        self._mark = mark

        name = QLabel("Nimbus", header)
        name.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: {theme.FONT_BODY}pt;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD}; letter-spacing: 0.2px;"
        )
        # Same optical-centring nudge as the shell's wordmark: a QLabel centres its line box,
        # and "Nimbus" has one descender, so box-centring leaves the visible glyphs reading low
        # against a pixmap of the same nominal height.
        name.setContentsMargins(0, 0, 0, max(1, name.fontMetrics().descent() - 1))
        row.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)

        # The session name is **not** in the header any more.
        #
        # It sat next to "Nimbus" reading "new chat", which is the label of a session rather than
        # a name -- so on a fresh panel the header said "Nimbus  new chat" and looked like a
        # button someone forgot to finish. It also had to be a QPushButton to be clickable, and a
        # button in the middle of a title bar is a hole in the drag area.
        #
        # The picker is still reachable, from the two places that make sense: the footer's "New
        # chat" pill, and "Switch session..." on the header's right-click menu. Kept as a widget
        # (parented, unlaid-out) because ``set_session`` writes to it and the session tests read
        # it -- the label is now state rather than chrome.
        self._session_label = QPushButton("new chat", header)
        self._session_label.setVisible(False)
        self._session_label.clicked.connect(self.open_picker)
        row.addStretch(1)

        self._name_label = name

        # Three controls, in the order their effect grows: keep open, collapse, hide.
        self.pin_button = _icon_button(
            header, "\u25c9", "Keep open \u00b7 stop the panel hiding itself",
            checkable=True, size=WINDOW_BUTTON)
        self.pin_button.toggled.connect(self.set_pinned)
        row.addWidget(self.pin_button)

        # Two arrows rather than one toggle, because the two directions are two different
        # things a user wants and a single button cannot offer both.
        #
        # A toggle whose glyph flipped depending on where the panel sat meant the *direction was
        # chosen for you*: a panel low on the screen could only ever open upwards. These say what
        # they do -- up opens the transcript above the bar, down opens it below -- and either one
        # collapses when the panel is already open, so neither is a dead control in any state.
        self.up_button = _icon_button(
            header, "\u2303", "Open the transcript upwards", size=WINDOW_BUTTON)
        self.up_button.clicked.connect(lambda: self.toggle_in_direction(upwards=True))
        row.addWidget(self.up_button)

        self.down_button = _icon_button(
            header, "\u2304", "Open the transcript downwards", size=WINDOW_BUTTON)
        self.down_button.clicked.connect(lambda: self.toggle_in_direction(upwards=False))
        row.addWidget(self.down_button)

        # Retained as the programmatic entry point and for the double-click handler. Not in the
        # layout: the two arrows above are what the user sees.
        self.collapse_button = _icon_button(
            header, "\u2303", "Collapse to the bar", checkable=True, size=WINDOW_BUTTON)
        self.collapse_button.setVisible(False)
        self.collapse_button.toggled.connect(self.set_collapsed)

        self.close_button = _icon_button(
            header, "\u2715", "Hide the panel", danger=True, size=WINDOW_BUTTON)
        # hide(), never quit: Nimbus is a background tool and closing a panel must not stop
        # push-to-talk (Invariant 5's sibling).
        self.close_button.clicked.connect(self.hide)
        row.addWidget(self.close_button)

        self._header = header
        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move
        header.mouseReleaseEvent = self._header_release
        # Double-clicking the bar collapses it, which is what double-clicking a title bar does
        # everywhere else. Previously bound to minimise, which no longer exists.
        header.mouseDoubleClickEvent = lambda _e: self.set_collapsed(not self.collapsed)
        return header

    def _build_messages(self) -> QWidget:
        self._scroll = QScrollArea(self._body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {theme.PANEL_TOP}; border: none; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}"
            # Grey at rest, accent while in use -- restated here rather than inherited because this
            # widget carries its own stylesheet, and a local `QScrollArea` rule stops the
            # application sheet's scrollbar rules applying to its children.
            f"QScrollBar::handle:vertical {{ background: {theme.BORDER_STRONG};"
            f" border-radius: 5px; min-height: 28px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {theme.ACCENT}; }}"
            f"QScrollBar::handle:vertical:pressed {{ background: {theme.ACCENT_PRESS}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
        )

        self._messages_host = QWidget()
        self._messages_host.setStyleSheet(f"background: {theme.PANEL_TOP};")
        self._messages = QVBoxLayout(self._messages_host)
        self._messages.setContentsMargins(
            theme.SPACE[0], theme.SPACE[2], theme.SPACE[0], theme.SPACE[2])
        self._messages.setSpacing(theme.SPACE[2])

        self._empty_state = QLabel(self._empty_state_text(), self._messages_host)
        self._empty_state.setWordWrap(True)
        self._empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FONT_BODY}pt;"
            f" padding: {theme.SPACE[4]}px;"
        )
        self._messages.addWidget(self._empty_state)
        self._messages.addStretch(1)

        self._scroll.setWidget(self._messages_host)
        return self._scroll

    def _build_footer(self) -> QWidget:
        footer = QWidget(self._body)
        footer.setFixedHeight(FOOTER_HEIGHT)
        row = QHBoxLayout(footer)
        row.setContentsMargins(
            theme.SPACE[2], theme.SPACE[0], theme.SPACE[2], theme.SPACE[0])
        row.setSpacing(theme.SPACE[1])

        # The state dot and word, as a compact pair rather than one long string.
        #
        # This line used to be "⏻ idle · ctrl+alt+space", which at the panel's width collided
        # with the buttons to its right and elided mid-word -- reading as a truncated sentence
        # that looked like a bug. The hotkey moved to the empty state and the tooltip, where
        # there is room for it, and what is left is short enough that it cannot be cut.
        self._status_label = QLabel(self._status_text(), footer)
        self._status_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FONT_SMALL}pt;")
        self._status_label.setToolTip(
            f"Push-to-talk is {format_hotkey(self._hotkey)}. "
            "Hold it and ask about anything on your screen.")
        # Floored at the width of the **longest** state, not the current one.
        #
        # Shortening the text was not enough on its own: the label still sized itself to
        # whatever state happened to be showing when the layout ran, so switching from "idle" to
        # "listening..." squeezed it and Qt elided the longer word. Reserving room for the worst
        # case means the footer never reflows and nothing is ever cut, at a cost of a few pixels
        # that were empty anyway.
        self._status_label.setMinimumWidth(self._widest_status_width())
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        # Mouse-transparent so the label does not punch a dead spot in the footer's drag strip,
        # for the same reason the brand mark is.
        self._status_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self._status_label, 0)
        row.addStretch(1)

        # Auto-hide, said in words and clickable. A pin glyph alone never told anyone the panel
        # was *going* to disappear, so when it did it read as a bug rather than as a setting.
        self._autohide_label = _pill_button(
            footer, self._autohide_text(), self._autohide_tooltip())
        self._autohide_label.clicked.connect(lambda: self.set_pinned(not self._pinned))
        row.addWidget(self._autohide_label, 0)

        self.new_chat_button = _pill_button(
            footer, "New chat",
            "Start a fresh conversation. Nimbus stops sending the model the previous turns.",
            accent=True)
        self.new_chat_button.clicked.connect(self.sig_new_session.emit)
        row.addWidget(self.new_chat_button, 0)

        # The footer is a drag handle too.
        #
        # With the panel opening *upwards*, the bar can be at the bottom of the screen and the
        # top of the panel well above it -- so the header is no longer always the nearest grab
        # point. The footer's empty middle is bare widget, same as the header's, and the two
        # pills sit at its right end, so there is a wide strip to grab either way.
        footer.setCursor(Qt.CursorShape.SizeAllCursor)
        footer.mousePressEvent = self._header_press
        footer.mouseMoveEvent = self._header_move
        footer.mouseReleaseEvent = self._header_release
        self._footer = footer
        return footer

    def _empty_state_text(self) -> str:
        """The one place the core interaction can be taught at the moment it is relevant."""
        return (f"Hold {format_hotkey(self._hotkey)} and ask about anything "
                f"on your screen.")

    def _status_text(self) -> str:
        """Just the state. The hotkey lives in the empty state and this label's tooltip.

        Both used to be on one line -- ``⏻ idle · ctrl+alt+space`` -- which at the panel's width
        collided with the buttons beside it and elided mid-word into something that looked
        broken rather than shortened.
        """
        return f"\u25cf {_STATE_LABELS.get(self.state, self.state)}"

    def _widest_status_width(self) -> int:
        """Pixels needed by the longest state string, plus a little slack.

        Measured with the label's own font metrics rather than estimated from a character count:
        the font is a stack that resolves differently on Windows 10 and 11, so a guess in
        characters is a guess in the wrong unit.
        """
        metrics = self._status_label.fontMetrics()
        return max(
            metrics.horizontalAdvance(f"\u25cf {label}")
            for label in _STATE_LABELS.values()
        ) + theme.SPACE[1]

    def _autohide_text(self) -> str:
        """Two words, both of which are the *action*: "Keep open" or "Auto-hide".

        This used to read "hides after 45s · keep open", which was three facts and a separator
        crammed into a pill: a duration nobody can act on, the current behaviour, and the thing
        the button does. The duration lives in Settings, where it is changeable, and in this
        pill's tooltip. What is left is a two-state toggle that says what it will do.
        """
        return "Keep open" if not self._pinned else "Staying open"

    def _autohide_tooltip(self) -> str:
        seconds = self._autohide_seconds
        if self._pinned:
            return ("The panel stays until you hide it.\n\n"
                    "Click to let it hide itself again.")
        if seconds <= 0:
            return ("The panel already stays until you hide it "
                    "(Settings \u2192 Interface).")
        return (f"The panel hides itself after {seconds} seconds of quiet.\n\n"
                "Click to keep it open. Change the delay in Settings \u2192 Interface.")

    # --- the public contract -------------------------------------------------

    @_never_raises
    def append(self, message: ChatMessage) -> None:
        """Render a turn, and persist it when a store is injected.

        Persistence happens **here** rather than in ``_pipeline_worker`` on purpose. Three
        writers now share ``index.db``, which WAL permits only if writes are serialised -- and
        the Qt main thread is the one place the HUD is guaranteed to be. Doing it here makes
        that structural instead of a comment somebody has to honour.
        """
        self.note_activity()
        if self._store is not None and self.session_id:
            message_id = self._store.add_message(self.session_id, message)
            stored = self._store.message(message_id)
            if stored is not None:
                message = stored
        row = self._insert_row(message)
        self._open_row = row if message.role == ROLE_NIMBUS else None

    @_never_raises
    def stream_delta(self, text: str) -> None:
        """Extend the open Nimbus turn, creating one if the reply has not started.

        A second delta must extend the current turn rather than starting a new one -- the
        reply arrives sentence by sentence from the TTS split, and one row per sentence would
        shred a paragraph into a list.

        Text appearing in step with the voice is the cheapest perceived-performance win
        available: a panel that sits empty for four seconds then dumps a finished paragraph
        feels slower than one that fills as Nimbus talks, at identical latency.
        """
        if not text:
            return
        self.note_activity()
        if self._open_row is None:
            self.append(ChatMessage(role=ROLE_NIMBUS, text=""))
        if self._open_row is None:
            return
        self._open_row.append_text(text)
        if self._store is not None and self._open_row.message.message_id:
            self._store.append_delta(self._open_row.message.message_id, text)
        self._scroll_to_bottom()

    @_never_raises
    def set_state(self, state: str) -> None:
        """Bind the strip, the footer and the pill to the interaction state.

        An unrecognised state is ignored rather than treated as idle: a typo upstream should
        leave the last known state visible, not silently tell the user Nimbus stopped
        listening.
        """
        normalised = (state or "").strip().lower()
        if normalised not in STATES:
            return
        self.state = normalised
        self._strip.setStyleSheet(f"background: {state_colour(normalised)};")
        self._status_label.setText(self._status_text())
        if normalised != STATE_IDLE and self._auto_reveal:
            # "Returns on the next interaction" -- an auto-hidden panel must come back on its
            # own, or the user has to learn a shortcut to see what Nimbus heard.
            self.reveal()
        self.note_activity()

    def set_auto_reveal(self, enabled: bool) -> None:
        """Whether an interaction may bring the panel back on screen by itself.

        This is the *only* place the panel shows itself unasked, so one flag here is the whole
        feature: with it off, the transcript still accumulates and the session is still recorded,
        the panel simply stays where the user left it. Turning it back on does not pop the panel
        up either -- the caller decides that, because the user flipping a switch is a different
        event from Nimbus deciding to appear.

        Off means off for the state path only. ``reveal`` itself is untouched, so Ctrl+Alt+H and
        the Home page's switch still work; a flag that also blocked explicit requests would make
        the panel unreachable rather than unobtrusive.
        """
        self._auto_reveal = bool(enabled)

    def auto_reveal(self) -> bool:
        return self._auto_reveal

    @_never_raises
    def set_session(self, session_id: int, title: str) -> None:
        """Point the HUD at a session and rebuild the transcript from the store.

        Rebuilding the view here is what makes "switch session" honest from the user's side;
        ``sessions.switch_session`` is what makes it honest from the model's side by rebuilding
        ``_history``. Both are needed and they are deliberately separate calls -- this module
        must not reach into ``app.py``'s state.
        """
        self.session_id = int(session_id)
        self._session_label.setText((title or "").strip() or "new chat")
        self._clear_rows()
        if self._store is not None and self.session_id:
            for message in self._store.messages(self.session_id):
                self._insert_row(message)
        self._open_row = None

    @_never_raises
    def reset_position(self) -> None:
        """Return to top-centre of the cursor's monitor.

        The recovery path for a window dragged onto a monitor that has since been unplugged:
        its saved position is off the visible desktop and there is otherwise no way to reach
        it. Also on the header's right-click menu for exactly that reason.
        """
        geometry = self._screen_geometry_fn()
        x, y = top_centre_position(geometry, self.width())
        self.move(x, y)

    # --- rows ---

    def _insert_row(self, message: ChatMessage) -> _MessageRow:
        row = _MessageRow(message, self)
        self._rows.append(row)
        self._messages.insertWidget(self._messages.count() - 1, row)
        self._empty_state.setVisible(False)
        self._scroll_to_bottom()
        return row

    def _clear_rows(self) -> None:
        for row in self._rows:
            self._messages.removeWidget(row)
            row.setParent(None)
        self._rows = []
        self._open_row = None
        self._empty_state.setVisible(True)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def row_count(self) -> int:
        return len(self._rows)

    def rows(self) -> list[_MessageRow]:
        return list(self._rows)

    def message_texts(self) -> list[str]:
        return [row.message.text for row in self._rows]

    def empty_state_text(self) -> str:
        return self._empty_state.text()

    def state_strip_colour(self) -> str:
        return state_colour(self.state)

    def status_text(self) -> str:
        return self._status_label.text()

    def session_label(self) -> str:
        return self._session_label.text()

    # --- hover actions the rows delegate back here ---

    def transcript_before(self, row: _MessageRow) -> str:
        """The user turn a failed reply belongs to, for a retry that skips recording."""
        try:
            index = self._rows.index(row)
        except ValueError:
            return ""
        for candidate in reversed(self._rows[:index + 1]):
            if candidate.message.role == ROLE_USER:
                return candidate.message.text
        return ""

    @_never_raises
    def flag_wrong(self, row: _MessageRow) -> None:
        """One click, no dialog. Marks the turn and pulls it out of the review queue.

        Two honest uses: telemetry the user controls, and suppression from ``T3-3``'s review
        queue -- reviewing a known-wrong answer for thirty days would actively teach the wrong
        thing.
        """
        message_id = row.message.message_id
        if row.wrong_button is not None:
            row.wrong_button.setEnabled(False)
            row.wrong_button.setToolTip("Marked as wrong")
        if self._store is not None and message_id:
            self._store.flag_wrong(message_id)
        self._insert_row(ChatMessage(
            role=ROLE_SYSTEM, text="You marked that answer as wrong."))
        self.sig_flag_wrong.emit(int(message_id))

    def thumbnail_for(self, message: ChatMessage) -> QPixmap | None:
        """The stored thumbnail, or None when it is missing or there is no store."""
        if self._store is None or not message.screenshot:
            return None
        _full, thumb = self._store.screenshot_paths(message.screenshot)
        pixmap = QPixmap(str(thumb))
        return None if pixmap.isNull() else pixmap

    # --- session picker ---

    def session_records(self, search: str = "") -> list[dict]:
        if self._store is None:
            return []
        return self._store.sessions(search=search)

    def delete_session(self, session_id: int) -> None:
        if self._store is not None:
            self._store.delete_session(int(session_id))

    @_never_raises
    def open_picker(self) -> None:
        if self._picker is None:
            self._picker = _SessionPicker(self)
        self._picker.reload()
        # Centred horizontally and clamped inside the panel.
        #
        # It used to be pinned to the left edge under the header, because it hung off a label
        # there. Now that it opens from the right-click menu it belongs to the panel as a whole,
        # and a 380px popover pinned left in a 660px panel looked like it had come unmoored. The
        # clamp matters when the user has narrowed the panel below the popover's width.
        x = max(RESIZE_MARGIN, (self.width() - self._picker.width()) // 2)
        y = self._bar_height()
        available = self.height() - y - RESIZE_MARGIN
        if self._picker.height() > available > 0:
            # Taller than the panel: sit it just under the bar and let its own list scroll.
            self._picker.setFixedHeight(available)
        self._picker.move(x, y)
        self._picker.show()
        self._picker.raise_()
        self.note_activity()

    def picker(self) -> _SessionPicker | None:
        return self._picker

    # --- collapse / pin / auto-hide ---
    #
    # There used to be a *minimise* here as well, which shrank the panel to a 200x34 pill and
    # re-centred it at the top of the screen. It is gone, along with the pill: two controls that
    # both meant "make this smaller" and differed in ways nobody could predict before clicking
    # is one control too many, and the pill also lost the session name and the panel's position.
    # Collapse is the one that was wanted -- the bar stays exactly where the user put it, at the
    # width they chose, and the transcript goes away.

    @_never_raises
    def toggle_in_direction(self, upwards: bool) -> None:
        """What the two header arrows do.

        Open in the direction asked for; if already open, close. So the up arrow always means
        "the transcript belongs above the bar" and the down arrow "below it", and pressing either
        one while open puts the panel away -- which is what a user reaches for after reading.

        Pressing the *other* arrow while open re-opens the other way rather than collapsing,
        because that is plainly a request to move the transcript, not to hide it.
        """
        if not self.collapsed:
            if self.expand_upwards == bool(upwards):
                self.set_collapsed(True)
                return
            # Already open the other way: collapse and reopen, so the bar ends up anchored where
            # the requested direction needs it.
            self.set_collapsed(True)
        self._expand_upwards = bool(upwards)
        self.set_collapsed(False)

    def _refresh_direction_buttons(self) -> None:
        """Highlight whichever arrow describes the panel's current state.

        Checked-looking rather than disabled: an arrow that greys out reads as unavailable, and
        both directions are always available.
        """
        for button, is_this_way in (
            (getattr(self, "up_button", None), True),
            (getattr(self, "down_button", None), False),
        ):
            if button is None:
                continue
            active = (not self.collapsed) and self.expand_upwards == is_this_way
            button.setToolTip(
                "Collapse to the bar" if active
                else ("Open the transcript upwards" if is_this_way
                      else "Open the transcript downwards"))
            blocked = button.blockSignals(True)
            button.setChecked(False)  # not checkable; kept explicit against a future change
            button.blockSignals(blocked)

    @_never_raises
    def set_collapsed(self, collapsed: bool) -> None:
        """Hide everything below the bar, keeping the panel's width and position.

        The third state between "open" and "gone", and the one people actually asked for: they
        want to know Nimbus is there and what session they are in, without a transcript over
        their work. Minimise shrinks to a 200px pill and loses the session name; this keeps the
        bar exactly where it was and just drops the body.

        Height is driven by ``setFixedHeight`` rather than by hiding children alone, because a
        frameless window keeps its old height if nothing tells it otherwise -- the body would
        vanish and leave an empty rectangle behind.
        """
        collapsed = bool(collapsed)
        was_collapsed = self.collapsed
        # Which way the body goes is decided *before* anything moves, and remembered, so
        # expanding reverses exactly what collapsing did. Deciding again on expand would let a
        # panel dragged near a screen edge mid-collapse expand the other way and jump.
        if collapsed and not was_collapsed:
            self._expand_upwards = self._should_expand_upwards()
        self._collapsed = collapsed
        # Signals blocked while syncing the button, or `setChecked` re-emits `toggled`, which
        # re-enters this method. The inner call collapsed the panel and *then* the outer call
        # recorded `self.height()` -- by which point it was 38px -- so expanding restored the
        # panel to its minimum instead of the user's size. Same reason `PowerToggle.set_on` and
        # `NimbusTray.set_paused` block signals: a view syncing itself must not look like input.
        blocked = self.collapse_button.blockSignals(True)
        self.collapse_button.setChecked(collapsed)
        self.collapse_button.blockSignals(blocked)
        # The glyph is an **arrow pointing where the body will go**, not a state indicator.
        # Collapsed near the bottom of the screen it points up, because that is where the
        # transcript will appear; collapsed near the top it points down. Expanded it always
        # points the way the body will disappear.
        if collapsed:
            self.collapse_button.setText("\u2303" if self._expand_upwards else "\u2304")
            self.collapse_button.setToolTip(
                "Expand upwards" if self._expand_upwards else "Expand downwards")
        else:
            self.collapse_button.setText(
                "\u2304" if self._should_expand_upwards() else "\u2303")
            self.collapse_button.setToolTip("Collapse to the bar")

        # Recorded BEFORE the children are hidden. Hiding them makes the layout recalculate
        # immediately and shrink the window to its minimum height, so reading `self.height()`
        # afterwards returns MIN_HEIGHT rather than the size the user had chosen -- expanding
        # then "restored" the panel to 220px. Caught by
        # `test_expanding_restores_the_previous_height`.
        if collapsed and not was_collapsed:
            self._expanded_height = self.height()

        self._scroll.setVisible(not collapsed)
        self._footer.setVisible(not collapsed)
        for line in self._hairlines:
            line.setVisible(not collapsed)

        bar_height = self._bar_height()
        if collapsed:
            # **Collapsing never moves the bar.** It stays exactly where the panel's top edge
            # was, which is what makes it behave like a dropdown handle: the thing you clicked
            # is still under your pointer afterwards. An earlier version moved it down to where
            # the panel's bottom edge had been, and a bar that walks away from the click that
            # collapsed it is disorienting even when the arithmetic is right.
            self.setFixedHeight(bar_height)
        else:
            self.setMinimumHeight(MIN_HEIGHT)
            self.setMaximumHeight(MAX_HEIGHT)
            target = getattr(self, "_expanded_height", HUD_HEIGHT)
            if self._expand_upwards:
                # Grow upwards: the bar's *bottom* edge is the fixed point, so the window's top
                # moves up by the body height. This is the case that matters -- a panel sitting
                # low on the screen would otherwise expand straight off the bottom edge, and Qt
                # clamping it looks like the panel teleporting.
                self.move(self.x(), self.y() - max(0, target - bar_height))
            self.resize(self.width(), target)
        self._refresh_direction_buttons()
        self.note_activity()

    def _bar_height(self) -> int:
        """Window height when collapsed: the strip, the header, and every enclosing margin.

        **Measured from the live layouts, not a literal.** This was
        ``STATE_STRIP_HEIGHT + header + 2``, where the ``2`` stood for the body's 1px margins.
        Adding the resize gutter put another 10px between the window edge and the header, so the
        collapsed window came out 53px short of 43 -- the body could not fit the header, and the
        header spilled past the body's bottom edge and clipped the four buttons sitting in it.
        Deriving it means the next margin change cannot reintroduce that.
        """
        shell_margins = self.layout().contentsMargins()
        body_margins = self._body.layout().contentsMargins()
        return (
            shell_margins.top() + shell_margins.bottom()
            + body_margins.top() + body_margins.bottom()
            + STATE_STRIP_HEIGHT + self._header.height()
        )

    def _should_expand_upwards(self) -> bool:
        """Whether the body belongs above the bar rather than below it.

        Decided by where the panel actually is: a panel in the lower half of the screen has no
        room below it, and expanding downwards would run the transcript off the bottom edge --
        or, with Qt clamping, appear to teleport the whole panel upwards. Below the halfway line
        it opens upwards, above it opens downwards, which is how every menu on the platform
        behaves.

        Falls back to downwards with no screen, which is the safe direction: a panel that opens
        down and is clipped is still usable, one that opens up off the top of the screen is not.
        """
        try:
            geometry = self._screen_geometry_fn()
            if geometry is None:
                return False
            return self.y() + self.height() // 2 > geometry.center().y()
        except Exception:
            return False

    @property
    def expand_upwards(self) -> bool:
        """Which way the body will go. Set when the panel is collapsed."""
        return bool(getattr(self, "_expand_upwards", False))

    @property
    def collapsed(self) -> bool:
        return getattr(self, "_collapsed", False)

    @_never_raises
    def set_pinned(self, pinned: bool) -> None:
        """Pin defeats auto-hide, for users who want the panel permanent."""
        self._pinned = bool(pinned)
        # Blocked for the same reason as the collapse button. Harmless here today because
        # nothing below touches geometry, but the re-entrancy is real and the next person to add
        # a line to this method should not have to discover that.
        blocked = self.pin_button.blockSignals(True)
        self.pin_button.setChecked(self._pinned)
        self.pin_button.blockSignals(blocked)
        if hasattr(self, "_autohide_label"):
            self._autohide_label.setText(self._autohide_text())
            self._autohide_label.setToolTip(self._autohide_tooltip())
        if self._pinned:
            self._idle_timer.stop()
        else:
            self.note_activity()

    @property
    def pinned(self) -> bool:
        return self._pinned

    def note_activity(self) -> None:
        """Restart the auto-hide countdown. Called by every interaction path.

        An always-visible panel on a screen someone is working on becomes furniture they
        resent, and a panel that vanishes mid-read is worse. Any activity -- a message, a
        state change, a hover -- buys another full window.
        """
        if self._pinned or self._autohide_seconds <= 0:
            self._idle_timer.stop()
            return
        self._idle_timer.start(self._autohide_seconds * 1000)

    def autohide_seconds(self) -> int:
        return self._autohide_seconds

    def idle_timer_running(self) -> bool:
        return self._idle_timer.isActive()

    @_never_raises
    def _on_idle_timeout(self) -> None:
        if self._pinned:
            return
        self.dismiss()

    def _resolve_autohide(self) -> int:
        try:
            from config import resolve_bounded_int_setting
            return resolve_bounded_int_setting(
                "CHAT_HUD_AUTOHIDE_SECONDS", default=DEFAULT_AUTOHIDE_SECONDS,
                minimum=0, maximum=3600)
        except Exception:
            return DEFAULT_AUTOHIDE_SECONDS

    # --- show / hide, with fades that do not break capture exclusion ---

    # --- why there is no opacity fade any more --------------------------------
    #
    # ``reveal`` and ``dismiss`` used to animate a ``QGraphicsOpacityEffect`` on ``self._body``.
    # **Removed after it produced the black panel.**
    #
    # The docstring on the old ``_animate_opacity`` had the reason for the bug written in it
    # already: "the effect is attached only for the duration of the fade, because a permanent
    # ``QGraphicsOpacityEffect`` forces every repaint through an offscreen buffer". ``dismiss``
    # detached it when its animation finished. ``reveal`` never did. Measured: after one
    # ``reveal()``, ``self._body.graphicsEffect()`` is still a live ``QGraphicsOpacityEffect`` at
    # opacity 1.0 -- so from the first time the panel appeared, every repaint of the body went
    # through that buffer for the rest of the session. That is what produced "the chat has a black
    # bg" after reopening, and the black bars down the sides when switching session: a resize
    # re-creates the buffer, and whatever has not repainted into it yet is transparent black.
    #
    # There is no safe fade to put back. ``setWindowOpacity(<1.0)`` forces Qt's layered-window
    # path, and a layered window cannot be excluded from screen capture (module docstring) -- so a
    # window-level fade would trade Invariant 1 for 160ms of polish. The entrance still slides,
    # which animates ``pos`` and needs no buffer, and the shell's page crossfade was retired for
    # exactly the same class of artefact.

    @_never_raises
    def reveal(self) -> None:
        """Show without stealing focus, sliding down 12px into place."""
        if self.isVisible():
            self.note_activity()
            return
        # Any effect left over from an older build of this method, or from a future one, is cleared
        # rather than trusted. This is the state that caused the bug.
        self._clear_body_effect()
        target = self.pos()
        self.move(target.x(), target.y() - 12)
        self.show()
        self._animate_position(target)
        self.note_activity()

    @_never_raises
    def dismiss(self) -> None:
        """Hide. Exits are immediate: there is no fade left to run."""
        if not self.isVisible():
            return
        self._finish_dismiss()

    def _finish_dismiss(self) -> None:
        self.hide()
        self._clear_body_effect()

    def _clear_body_effect(self) -> None:
        """Detach any graphics effect from the body, so no repaint goes through a buffer."""
        try:
            if self._body.graphicsEffect() is not None:
                self._body.setGraphicsEffect(None)
        except Exception:
            pass

    def _animate_position(self, target: QPoint):
        """Slide to ``target`` with a slight overshoot -- the HUD arriving is worth noticing."""
        self._slide = self._replace(self._slide, QPropertyAnimation(self, b"pos", self))
        self._slide.setDuration(theme.duration(theme.DUR_ENTRANCE))
        self._slide.setEasingCurve(theme.easing("OutBack"))
        self._slide.setEndValue(target)
        self._slide.start()
        return self._slide

    @staticmethod
    def _replace(previous, animation):
        """Stop and dispose of the previous animation before starting its replacement.

        Animations are parented to the HUD so C++ keeps them alive for their run, which also
        means they accumulate unless retired. Stopping first matters more than the memory: two
        live animations on the same property fight, and the loser wins intermittently.
        """
        if previous is not None:
            try:
                previous.stop()
                previous.deleteLater()
            except RuntimeError:
                pass
        return animation

    def is_showing_transcript(self) -> bool:
        """Whether ``T4-5``'s caption should stand down (§6.1).

        Two copies of the same words on one screen is noise, so the caption defers while the
        HUD is showing the transcript -- and does its original job when the HUD is minimised
        or hidden.
        """
        return bool(self.isVisible() and not self.collapsed)

    # --- capture exclusion ---

    def showEvent(self, event) -> None:
        """Apply capture exclusion on every show, and log which path is active.

        Re-applied rather than done once: the affinity is a property of the HWND, and any
        future path that recreates the native window would silently lose it. A silent failure
        here is invisible until someone notices Nimbus pointing at its own chat panel, which
        is why the result is logged rather than merely stored.
        """
        super().showEvent(event)
        hwnd = int(self.winId())
        active = bool(self._exclude(hwnd))
        if active != self.capture_exclusion_active or not hasattr(self, "_logged_exclusion"):
            if active:
                _log("capture exclusion ACTIVE (WDA_EXCLUDEFROMCAPTURE) - "
                     "the HUD will not appear in screenshots")
            else:
                _log("capture exclusion UNAVAILABLE - falling back to hide-before-grab. "
                     "Needs Windows 10 build 19041+")
            self._logged_exclusion = True
        self.capture_exclusion_active = active
        apply_rounded_region(hwnd, self.width(), self.height())

    def needs_hide_for_capture(self) -> bool:
        """True when the pre-19041 fallback is required.

        On builds where ``SetWindowDisplayAffinity`` fails, the HUD has to join the existing
        hide-before-``mss.grab()`` cycle. That is worse -- it reintroduces the flicker ``T2-6``
        complains about -- but it is far better than a screenshot containing the panel, and a
        user on an old build gets a working app rather than a subtly wrong one.
        """
        return not self.capture_exclusion_active

    def hide_for_capture(self) -> None:
        """No-op when exclusion is active, so ``app.py`` can call it unconditionally."""
        if self.needs_hide_for_capture() and self.isVisible():
            self._hidden_for_capture = True
            self.hide()

    def show_after_capture(self) -> None:
        if getattr(self, "_hidden_for_capture", False):
            self._hidden_for_capture = False
            self.show()

    # --- drag, resize, geometry persistence ---

    def _header_press(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_drag(event.globalPosition().toPoint())

    def _header_move(self, event) -> None:
        if self._drag_origin is not None:
            self._drag_to(event.globalPosition().toPoint())

    def _header_release(self, _event) -> None:
        self._end_drag()

    def _begin_drag(self, global_pos: QPoint) -> None:
        self._drag_origin = global_pos - self.pos()
        self.note_activity()

    def _drag_to(self, global_pos: QPoint) -> None:
        if self._drag_origin is None:
            return
        self.move(global_pos - self._drag_origin)

    def _end_drag(self) -> None:
        if self._drag_origin is None:
            return
        self._drag_origin = None
        self._save_position()

    # --- resizing from any edge ---------------------------------------------
    #
    # The panel used to be resizable from its bottom edge only, which meant making it *wider*
    # was impossible and making it taller pushed the bottom down rather than growing it from
    # wherever the user grabbed. Every edge and corner now works.
    #
    # Done by hand rather than with ``QWindow.startSystemResize``, which the shell's window uses.
    # That call hands the gesture to Windows, and Windows *activates* the window it is resizing
    # -- which would steal focus from whatever the user is typing in. The HUD carries
    # ``WindowDoesNotAcceptFocus`` and ``WA_ShowWithoutActivating`` precisely so it cannot do
    # that, and keeping the resize in-process is the price of keeping that promise.

    _EDGE_CURSORS = {
        (True, False, False, False): Qt.CursorShape.SizeHorCursor,    # left
        (False, True, False, False): Qt.CursorShape.SizeHorCursor,    # right
        (False, False, True, False): Qt.CursorShape.SizeVerCursor,    # top
        (False, False, False, True): Qt.CursorShape.SizeVerCursor,    # bottom
        (True, False, True, False): Qt.CursorShape.SizeFDiagCursor,   # top-left
        (False, True, False, True): Qt.CursorShape.SizeFDiagCursor,   # bottom-right
        (False, True, True, False): Qt.CursorShape.SizeBDiagCursor,   # top-right
        (True, False, False, True): Qt.CursorShape.SizeBDiagCursor,   # bottom-left
    }

    def _edges_at(self, point) -> tuple[bool, bool, bool, bool]:
        """``(left, right, top, bottom)`` for a point, within reach of each edge.

        Edges use ``RESIZE_MARGIN``; **corners use ``CORNER_MARGIN``**, and that asymmetry is
        the fix for the diagonal cursor never appearing. With one 5px margin the corner hit zone
        is the 5x5 square where the two strips overlap -- 25 device pixels the user has to land
        on, and a pixel either way silently gives them a single-axis resize instead. Windows'
        own frames do the same thing: the sizing border is thin, the corner grab area is not.

        Widening ``RESIZE_MARGIN`` itself was the alternative and is worse: it is also the
        visible bezel and the gutter that keeps children off the resize strip, so a 12px value
        would put a 12px inset around the whole panel to fix a cursor.
        """
        if self.collapsed:
            # Collapsed, the panel is a fixed-height bar. Offering a vertical resize would
            # fight the fixed height and do nothing, which is worse than not offering it.
            return (False, False, False, False)
        x, y = point.x(), point.y()
        in_corner_x = x <= CORNER_MARGIN or x >= self.width() - CORNER_MARGIN
        in_corner_y = y <= CORNER_MARGIN or y >= self.height() - CORNER_MARGIN
        # A corner needs *both* axes near an edge. Inside that square each axis gets the wider
        # tolerance; outside it, only the thin strip counts, so the edges stay thin.
        margin_x = CORNER_MARGIN if in_corner_y else RESIZE_MARGIN
        margin_y = CORNER_MARGIN if in_corner_x else RESIZE_MARGIN
        return (
            x <= margin_x,
            x >= self.width() - margin_x,
            y <= margin_y,
            y >= self.height() - margin_y,
        )

    def mousePressEvent(self, event) -> None:
        edges = self._edges_at(event.position().toPoint())
        if any(edges) and event.button() == Qt.MouseButton.LeftButton:
            # The whole starting geometry is captured, not just a delta origin. Dragging a left
            # or top edge has to move the window *and* resize it, and computing that from the
            # frame's current position each frame accumulates rounding into a visible drift.
            self._resize_origin = (
                event.globalPosition().toPoint(), self.geometry(), edges)
            self.note_activity()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_origin is not None:
            self._resize_from_origin(event.globalPosition().toPoint())
        else:
            cursor = self._EDGE_CURSORS.get(self._edges_at(event.position().toPoint()))
            if cursor is None:
                self.unsetCursor()
                # `unsetCursor` alone is not enough, because this only runs while the pointer is
                # over bare panel. Moving from the gutter straight onto a child sends no further
                # move event here, so the resize cursor would stay -- see `leaveEvent`. The shell
                # window had the same bug and it is what "my cursor becomes the resize cursor and
                # stays like that" was.
            else:
                self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def _resize_from_origin(self, global_pos: QPoint) -> None:
        """Apply a drag against the geometry captured at press time."""
        start_pos, start_geometry, (left, right, top, bottom) = self._resize_origin
        delta_x = global_pos.x() - start_pos.x()
        delta_y = global_pos.y() - start_pos.y()

        width = start_geometry.width() + (delta_x if right else -delta_x if left else 0)
        height = start_geometry.height() + (delta_y if bottom else -delta_y if top else 0)
        width, height = clamp_size(width, height)

        # Clamping happens before the move, so dragging a left edge past the minimum width
        # pins the edge instead of sliding the whole panel sideways.
        x = start_geometry.right() - width + 1 if left else start_geometry.x()
        y = start_geometry.bottom() - height + 1 if top else start_geometry.y()
        self.setGeometry(x, y, width, height)

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_origin is not None:
            self._resize_origin = None
            self._save_position()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        """Drop the resize cursor the moment the pointer leaves the bare panel.

        Qt sends ``Leave`` when the pointer moves onto a *child* as well as off the window, which is
        exactly the case ``mouseMoveEvent`` cannot see: it only runs while the pointer is over
        ``ChatHud`` itself, so a move from the gutter onto the transcript would otherwise leave the
        resize cursor set for as long as the panel stayed open.
        """
        self.unsetCursor()
        super().leaveEvent(event)

    def _in_resize_zone(self, point) -> bool:
        """Whether a point is on any resize edge. Kept as the name the tests already use."""
        return any(self._edges_at(point))

    def _resize_to(self, width: int, height: int) -> None:
        """Resize within the clamped range. See ``clamp_size``."""
        clamped_w, clamped_h = clamp_size(width, height)
        self.resize(clamped_w, clamped_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # The region is fixed-size window geometry, so a stale one would clip the new size.
        if self.isVisible():
            apply_rounded_region(int(self.winId()), self.width(), self.height())

    def contextMenuEvent(self, event) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("New chat", self.sig_new_session.emit)
        # The picker's home now that the header no longer carries the session name.
        menu.addAction("Switch session\u2026", self.open_picker)
        menu.addSeparator()
        menu.addAction("Reset position", self.reset_position)
        menu.addAction("Hide", self.hide)
        menu.exec(event.globalPos())

    # --- per-monitor position persistence ---

    def _default_positions_path(self, override):
        """Where per-monitor positions live.

        A small JSON file beside the database rather than the keyring: this is UI state that
        changes on every drag, and Credential Manager is for secrets and settings, not for
        window geometry written several times a second.
        """
        if override is not None:
            return Path(override)
        try:
            from config import INDEX_DB_PATH
            return Path(INDEX_DB_PATH).parent / "chat_hud.json"
        except Exception:
            return None

    def _screen_key(self) -> str:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.name() if screen is not None else "default"

    def saved_positions(self) -> dict:
        """Remembered top-left per monitor name.

        Keyed by monitor because a position that is perfect on a 4K centre screen is off the
        edge of a 1366x768 laptop panel, and users dock and undock.
        """
        import json

        if self._positions_path is None:
            return {}
        try:
            return json.loads(self._positions_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_position(self) -> None:
        import json

        if self._positions_path is None:
            return
        positions = self.saved_positions()
        positions[self._screen_key()] = [self.x(), self.y()]
        try:
            self._positions_path.parent.mkdir(parents=True, exist_ok=True)
            self._positions_path.write_text(
                json.dumps(positions, indent=2), encoding="utf-8")
        except OSError as exc:
            _log(f"position not saved - {exc}")

    @_never_raises
    def restore_position(self) -> None:
        """Return to where the user left the HUD on this monitor, if it is still on screen.

        A saved position is discarded when it falls outside the current available geometry --
        that is the unplugged-monitor case, and silently snapping back to top-centre is
        friendlier than a panel the user cannot find.
        """
        saved = self.saved_positions().get(self._screen_key())
        if not saved:
            return
        x, y = int(saved[0]), int(saved[1])
        geometry = self._screen_geometry_fn()
        if not geometry.contains(QPoint(x, y)):
            self.reset_position()
            return
        self.move(x, y)

    def _default_screen_geometry(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()
