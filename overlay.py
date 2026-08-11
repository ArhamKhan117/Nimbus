"""Nimbus transparent click-through pointer overlay.

Per-monitor `OverlayWindow(QWidget)` overlays routed by `OverlayController`.
Each overlay covers exactly one physical monitor in DIP (logical) coords.
A blue animated pointer is drawn via `QPainter.paintEvent` and moved by
`QPropertyAnimation` on a `pyqtProperty`. Click-through is enforced by
Win32 extended window styles applied via ctypes AFTER `QWidget.show()`.

The per-monitor architecture (one overlay per physical screen) is what makes
the "islands of screens" mixed-DPI case render correctly.

Responsibility boundary:
- THIS MODULE lives in Space A (physical pixels from capture.py) and
  Space B (Qt logical/DIP pixels). It owns the math that maps A -> B
  per-screen via devicePixelRatio().
- capture.py owns Space A -> Space C (Nimbus declared resolution).
- app.py owns threading and calls OverlayController methods from the
  main Qt thread only (PyQt6 is not thread-safe).

Top-to-bottom order (so `python -m overlay` works):
    1. Module docstring
    2. Imports
    3. Win32 constants (_GWL_EXSTYLE, _WS_EX_*, _SWP_*, _HWND_TOPMOST,
       _CLICKTHROUGH_FLAGS)
    4. apply_clickthrough_styles(hwnd) ctypes helper
    5. screen_for_monitor(monitor, screens) pure function
    6. physical_to_local_logical(x, y, screen) pure function
    7. OverlayWindow(QWidget) class
    8. OverlayController class
    9. __main__ block for manual click-through verification
"""
from __future__ import annotations

import ctypes
import math
import time
from collections import deque
from itertools import cycle

from enum import Enum, auto

from PyQt6.QtCore import (
    QPoint,
    QPointF,
    QRectF,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QGuiApplication, QLinearGradient, QPainter,
    QPainterPath, QPen, QPolygonF, QRadialGradient, QScreen,
)
from PyQt6.QtWidgets import QWidget

import theme


class _OverlayState(Enum):
    IDLE = auto()
    POINTING = auto()
    LISTENING = auto()
    THINKING = auto()
    HIDDEN = auto()


# The only place the overlay's semantic colours live.  Keeping RGB tuples
# rather than QColor instances makes this mapping cheap and unit-testable.
_STATE_ACCENT_RGB: dict[_OverlayState, tuple[int, int, int]] = {
    _OverlayState.IDLE: theme.OVERLAY_STATE_RGB["idle"],
    _OverlayState.POINTING: theme.OVERLAY_STATE_RGB["pointing"],
    _OverlayState.LISTENING: theme.OVERLAY_STATE_RGB["listening"],
    _OverlayState.THINKING: theme.OVERLAY_STATE_RGB["thinking"],
    _OverlayState.HIDDEN: theme.OVERLAY_STATE_RGB["hidden"],
}
"""Central state -> accent palette used by every overlay visual.

Sourced from ``theme.OVERLAY_STATE_RGB`` rather than literals so the overlay, the shell and the
chat HUD cannot drift apart (SHELL_AND_CHAT.md §2.4). Retheming to orange was previously a
one-dict change *here*; it is now a one-dict change in the design system, which is the right
place for it.

``POINTING`` is Nimbus orange -- the brand moment, and the visual the user actually looks at.
``LISTENING`` stays green deliberately: recording indicators are green essentially everywhere,
and overriding a learned signal for palette consistency would cost the user certainty about
whether the microphone is live."""


def _accent_rgb(state: _OverlayState) -> tuple[int, int, int]:
    """Return the theme colour for an overlay interaction state."""
    return _STATE_ACCENT_RGB.get(state, _STATE_ACCENT_RGB[_OverlayState.IDLE])


def _with_alpha(rgb: tuple[int, int, int], alpha: int) -> QColor:
    """Make a QColor from a test-friendly RGB tuple, clamping alpha."""
    return QColor(*rgb, max(0, min(int(alpha), 255)))


def _tint(rgb: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """Mix an RGB tuple toward white by ``amount`` in 0..1.

    Exists so the cursor's specular highlight is *derived from* the state accent instead of
    being a literal. The first implementation hardcoded ``(225, 244, 255)`` -- a pale blue --
    which is why the pointer still read as blue after the palette moved to orange: the accent
    was correct and a cool highlight sitting on top of it was cancelling the hue out.
    """
    amount = max(0.0, min(float(amount), 1.0))
    return tuple(int(round(c + (255 - c) * amount)) for c in rgb)  # type: ignore[return-value]


def _shade(rgb: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """Mix an RGB tuple toward black by ``amount`` in 0..1.

    Same reasoning as `_tint`, for the fine lower edge that keeps the pointer readable against
    a bright application. That edge was hardcoded navy ``(10, 30, 60)``.
    """
    amount = max(0.0, min(float(amount), 1.0))
    return tuple(int(round(c * (1.0 - amount))) for c in rgb)  # type: ignore[return-value]


# --- Quadratic bezier flight arc math -------------------------
#
# A quadratic-bezier flight arc with ONE deliberate design choice: no tangent
# rotation. Our cursor is a tip-anchored polygon — the tip IS the pointer, so
# we keep it pointing at the target throughout the flight rather than rotating
# the shape along the tangent.

def _bezier_position(
    t: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> tuple[float, float]:
    """Quadratic Bezier: B(t) = (1-t)²·P0 + 2(1-t)t·P1 + t²·P2."""
    one_minus = 1.0 - t
    x = one_minus * one_minus * p0[0] + 2.0 * one_minus * t * p1[0] + t * t * p2[0]
    y = one_minus * one_minus * p0[1] + 2.0 * one_minus * t * p1[1] + t * t * p2[1]
    return (x, y)


def _smoothstep(t: float) -> float:
    """Hermite smoothstep: 3t² - 2t³. Eases in and out for natural motion."""
    return t * t * (3.0 - 2.0 * t)


def _flight_duration_s(distance_px: float) -> float:
    """Distance-scaled flight duration, clamped to [0.6s, 1.4s]."""
    return max(0.6, min(distance_px / 800.0, 1.4))


def _scale_pulse(linear_t: float) -> float:
    """Sine scale pulse: 1.0 at endpoints → 1.3 at midpoint. Not eased —
    runs on LINEAR progress (not smoothstep'd) so the peak lands dead-center."""
    return 1.0 + math.sin(linear_t * math.pi) * 0.3


def _idle_breath_scale(elapsed_s: float) -> float:
    """A restrained 3-second idle pulse in the inclusive range [1.0, 1.05]."""
    return 1.025 + math.sin(elapsed_s * math.tau / 3.0) * 0.025


def _ease_out_cubic(t: float) -> float:
    """Clamped easing used for annotation draw-in and fade transitions."""
    t = max(0.0, min(float(t), 1.0))
    return 1.0 - (1.0 - t) ** 3


def _annotation_opacity(elapsed_s: float, fade_start_s: float = 29.6) -> float:
    """Fade annotations in quickly, then out gracefully before the 30s clear."""
    if elapsed_s < 0:
        return 0.0
    if elapsed_s < 0.18:
        return _ease_out_cubic(elapsed_s / 0.18)
    if elapsed_s >= fade_start_s:
        return 1.0 - _ease_out_cubic((elapsed_s - fade_start_s) / 0.4)
    return 1.0


def _curved_arrow_control(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
    """Return a gentle perpendicular control point for an annotation arrow."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 0.001:
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    bend = min(length * 0.14, 28.0)
    return ((x1 + x2) / 2.0 - dy / length * bend,
            (y1 + y2) / 2.0 + dx / length * bend)


def _spinner_tail_segments(angle_deg: float, count: int = 12) -> list[tuple[float, float]]:
    """Comet-tail arc segments as (start angle, opacity fraction), head first."""
    return [(angle_deg - i * 7.0, (1.0 - i / count) ** 1.7) for i in range(count)]


def _waveform_color_rgb(audio_level: float, accent: tuple[int, int, int]) -> tuple[int, int, int]:
    """Brighten an accent as speech becomes stronger, without changing state hue."""
    level = max(0.0, min(float(audio_level), 1.0))
    lift = int(20 + 55 * level)
    return tuple(min(255, channel + lift) for channel in accent)


# --- Waveform widget (LISTENING state visual) -----------------
#
# While PTT is held, the cursor polygon hides and this 5-bar waveform renders
# at the cursor position. Bar heights are driven by mic RMS (from stt.py)
# × a profile curve + an independent sine idle-pulse so bars are
# never fully flat. Rendered at ~36 fps via QTimer.

_WAVEFORM_BAR_COUNT = 5
_WAVEFORM_BAR_PROFILE: tuple[float, ...] = (0.4, 0.7, 1.0, 0.7, 0.4)
"""Per-bar amplitude multiplier. Center bar (idx 2) scales by 1.0 = taller.
Edges (idx 0, 4) scale by 0.4 = shorter, for a natural center-weighted rhythm."""
_WAVEFORM_BASE_HEIGHT = 3.0  # minimum px — bars are never fully flat
_WAVEFORM_MAX_REACTIVE = 10.0  # max extra px from audio-driven component
_WAVEFORM_IDLE_PULSE_AMP = 1.5  # max extra px from independent sine pulse


def _waveform_bar_height(bar_index: int, audio_level: float, phase_seconds: float) -> float:
    """Compute a single bar's height in px.

    Formula:
        normalized = max(audio_level - 0.008, 0)
        eased = (min(normalized * 2.85, 1))^0.76
        reactive = eased * 10 * profile[bar_index]
        idle_pulse = (sin(phase * 3.6 + bar_index * 0.35) + 1) / 2 * 1.5
        height = 3 + reactive + idle_pulse

    - The 0.008 dead zone prevents flickering on near-silent chunks.
    - The 2.85× boost + 0.76 power curve make quiet speech visually punchy
      without saturating on loud speech.
    - The per-bar phase offset (0.35 rad) gives a subtle wave pattern even
      at silence.

    Args:
        bar_index: 0..4. Must be within _WAVEFORM_BAR_PROFILE bounds.
        audio_level: RMS-derived level in [0, 1] (from stt.py's on_audio_level).
        phase_seconds: elapsed time since widget startup (drives the idle pulse).

    Returns:
        Bar height in pixels, in range ~[3, 14.5].
    """
    normalized_level = max(audio_level - 0.008, 0.0)
    eased = pow(min(normalized_level * 2.85, 1.0), 0.76)
    reactive = eased * _WAVEFORM_MAX_REACTIVE * _WAVEFORM_BAR_PROFILE[bar_index]
    animation_phase = phase_seconds * 3.6 + bar_index * 0.35
    idle_pulse = (math.sin(animation_phase) + 1.0) / 2.0 * _WAVEFORM_IDLE_PULSE_AMP
    return _WAVEFORM_BASE_HEIGHT + reactive + idle_pulse

# --- Cursor polygon shape ----------------------------------------------------

_CURSOR_VERTICES = [
    (0.0, 0.0),      # tip (anchor point — lands on the target coordinate)
    (3.0, 0.0),      # short flat across the rounded tip
    (23.1, 12.8),    # upper-right edge, out to the widest point
    (23.7, 13.9),    # right shoulder
    (23.1, 15.9),    # turn back inward
    (13.2, 18.7),    # heel — where the arrowhead meets the tail
    (8.3, 27.1),     # tail barb, outer edge
    (7.1, 27.9),     # barb tip (lowest point)
    (5.7, 27.7),     # barb, inner edge
    (4.8, 26.4),     # back up toward the body
    (0.0, 1.7),      # left edge, closing to the tip
]
"""The pointer silhouette, **traced from `assets/cursor.png`** by `tools/trace_cursor.py`.

Not hand-authored. The tool thresholds the artwork's alpha, walks the outline with
Moore-neighbour boundary tracing, simplifies with Ramer-Douglas-Peucker, then normalises so the
tip lands on the origin and the shape fits the box `tests/test_overlay.py` pins. Re-run it after
changing the artwork and paste the output here — that is what keeps the flying pointer and the
logo from drifting apart.

Deriving it matters more than it sounds. The proportions are easy to eyeball; the *character* is
not. This shape is 23.7 wide by 27.9 tall — an aspect of 0.85, far broader than the 0.6 of the
hand-authored polygon it replaced — and the heel sits two thirds of the way down rather than
halfway. Guessing produced a generic operating-system arrow, which is precisely what the logo's
cursor is not.

Floats rather than integers because the shape is scaled per-monitor for DPI and pulsed to 1.3x
mid-flight, so rounding here would surface as a wobbling edge during the animation.

Colour is **not** baked in. It comes from `_accent_rgb(state)`, so the pointer is orange while
pointing, green while listening and amber while thinking — see `theme.OVERLAY_STATE_RGB`.
"""

_CURSOR_FOLLOW_LERP = 0.15
"""Spring interpolation factor for cursor following. Each frame, cursor moves
15% of the remaining distance toward the target. Lower = smoother/laggier.
0.15 gives a natural 'buddy following you' feel — like a puppy trotting after you.
"""


# --- Win32 constants ---------------------------------------------------------

_GWL_EXSTYLE = -20
"""SetWindowLongW index for the extended window style field."""

_WS_EX_LAYERED = 0x00080000
"""Required for WS_EX_TRANSPARENT to function on top-level windows."""
_WS_EX_TRANSPARENT = 0x00000020
"""The actual click-through flag (only works on layered windows)."""
_WS_EX_TOPMOST = 0x00000008
"""Always-on-top. Redundant with Qt.WindowStaysOnTopHint but harmless."""
_WS_EX_NOACTIVATE = 0x08000000
"""Prevents focus theft when the overlay receives any event."""
_WS_EX_TOOLWINDOW = 0x00000080
"""Hides the window from the taskbar and Alt-Tab list."""

_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
"""Forces WM_NCCALCSIZE so style changes take effect immediately."""
_HWND_TOPMOST = -1

_CLICKTHROUGH_FLAGS = (
    _WS_EX_LAYERED
    | _WS_EX_TRANSPARENT
    | _WS_EX_TOPMOST
    | _WS_EX_NOACTIVATE
    | _WS_EX_TOOLWINDOW
)
"""OR of all ex-styles to apply to overlay windows after show().

Bit pattern should be 0x080800A8. The test in test_overlay.py guards
against silent drift in the individual constants.
"""


# --- Win32 click-through helper ----------------------------------------------

def apply_clickthrough_styles(hwnd: int) -> None:
    """Apply Win32 extended window styles for click-through + no-taskbar
    + no-focus-theft on an existing top-level window.

    MUST be called AFTER QWidget.show() so the HWND exists. Reads the
    current GWL_EXSTYLE via GetWindowLongW, ORs in _CLICKTHROUGH_FLAGS
    (NEVER overwrites -- that would wipe Qt's own flags), then calls
    SetWindowLongW and forces the style change to take effect via
    SetWindowPos with SWP_FRAMECHANGED.

    This is the core of the click-through mechanism on Windows 11.
    Without SWP_FRAMECHANGED the new styles don't take effect until the
    window is resized or moved.

    Raises:
        RuntimeError: if SetWindowLongW returns 0, indicating the Win32
            call failed. Error details from ctypes.WinError() are included.
            This catches silent click-through breakage that would otherwise
            leave the user with no diagnostic signal.

    Args:
        hwnd: native window handle from int(QWidget.winId()).
    """
    user32 = ctypes.windll.user32
    current = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    new_style = current | _CLICKTHROUGH_FLAGS
    result = user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new_style)
    # SetWindowLongW returns the previous value on success, 0 on failure.
    # Previous value could legitimately be 0 if no ex-styles were set yet,
    # so we also check GetLastError. In practice current != 0 (Qt sets
    # some ex-styles) so a 0 return is always a failure.
    if result == 0 and current != 0:
        raise RuntimeError(
            f"SetWindowLongW failed for HWND {hwnd}: {ctypes.WinError()}"
        )
    user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST,
        0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )


# --- Pure coordinate math ----------------------------------------------------

def screen_for_monitor(monitor: dict, screens: list[QScreen]) -> QScreen:
    """Find the QScreen whose physical geometry matches a capture.py monitor dict.

    capture.py produces CaptureResult.monitor = {"left": phys_x, "top": phys_y,
    "width": phys_w, "height": phys_h} where all fields are in virtual-desktop
    physical pixel coordinates (from mss). QScreen.geometry() returns coords
    in Qt's DIP (logical) space. We compare by converting each QScreen's DIP
    dimensions to physical via its per-screen devicePixelRatio().

    Args:
        monitor: mss-style dict with 'left', 'top', 'width', 'height' keys
            (all values in physical pixels).
        screens: list of QScreen-compatible objects, each with geometry() ->
            QRect-like (DIP coords) and devicePixelRatio() -> float. In tests
            this is a list of _MockScreen duck-types, not real QScreens.

    Returns:
        The QScreen whose physical bounds match the monitor dict. Falls back
        to screens[0] (primary) if no match is found -- this can happen if
        mss state is stale or the monitor config changed mid-session.
    """
    target_w = monitor["width"]
    target_h = monitor["height"]
    target_left = monitor["left"]
    target_top = monitor["top"]
    for screen in screens:
        ratio = screen.devicePixelRatio()
        geom = screen.geometry()
        phys_w = int(geom.width() * ratio)
        phys_h = int(geom.height() * ratio)
        phys_left = int(geom.left() * ratio)
        phys_top = int(geom.top() * ratio)
        if (phys_w == target_w
                and phys_h == target_h
                and phys_left == target_left
                and phys_top == target_top):
            return screen
    return screens[0]


def physical_to_local_logical(
    physical_x: int,
    physical_y: int,
    screen: QScreen,
) -> tuple[int, int]:
    """Map a physical-pixel point (Space A) to within-screen logical DIP
    coords (Space B) inside the target QScreen's local coordinate system.

    Returns (local_x, local_y) where (0, 0) is the screen's top-left in
    the overlay widget's coordinate system. The per-monitor architecture
    means we never need global virtual-desktop coordinates -- each overlay
    lives in its own screen's local space.

    Critical: uses the PER-SCREEN devicePixelRatio(). Do NOT cache a
    global ratio. Mixed-DPI setups (e.g., laptop at 200% + external
    monitor at 100%) have different ratios per screen, and using the
    wrong one would land the pointer in the wrong place on one of them.

    Args:
        physical_x: virtual-desktop physical pixel x (from capture.py).
        physical_y: virtual-desktop physical pixel y.
        screen: QScreen-compatible object with geometry() returning a
            QRect-like (DIP coords) and devicePixelRatio() returning a float.

    Returns:
        (local_log_x, local_log_y) integer tuple in the screen's local
        logical coordinate space, ready to pass to QWidget.move or the
        pointer animation target.
    """
    ratio = screen.devicePixelRatio()
    geom = screen.geometry()
    screen_phys_left = int(geom.left() * ratio)
    screen_phys_top = int(geom.top() * ratio)
    local_phys_x = physical_x - screen_phys_left
    local_phys_y = physical_y - screen_phys_top
    local_log_x = int(local_phys_x / ratio)
    local_log_y = int(local_phys_y / ratio)
    return local_log_x, local_log_y


def annotations_to_local(annotations: list, screen) -> list:
    """Map teaching-annotation coords from PHYSICAL virtual-desktop pixels
    (Space A) to a screen's LOCAL-logical DIP coords (Space B).

    Points (centers, endpoints) go through physical_to_local_logical (which
    subtracts the screen origin + divides by the per-screen devicePixelRatio).
    Lengths (radius, width) are NOT positions — they only divide by the ratio,
    no origin subtraction. Returns NEW annotation objects (inputs untouched).

    Pure function so the coordinate math is unit-testable without a QApplication
    (the [POINT] path proved this transform correct: 253,52 -> 569,117 landed on
    the button). The per-screen ratio is never cached globally — mixed-DPI
    setups have a different ratio per screen.
    """
    from annotations import (
        Arrow, Circle, Highlight, Label, Rect, StepBadge, Underline,
    )

    ratio = screen.devicePixelRatio() or 1.0
    out: list = []
    for a in annotations:
        if isinstance(a, Circle):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Circle(lx, ly, int(a.r / ratio), a.label))
        elif isinstance(a, Arrow):
            x1, y1 = physical_to_local_logical(a.x1, a.y1, screen)
            x2, y2 = physical_to_local_logical(a.x2, a.y2, screen)
            out.append(Arrow(x1, y1, x2, y2))
        elif isinstance(a, Underline):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Underline(lx, ly, int(a.w / ratio)))
        elif isinstance(a, Label):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(Label(lx, ly, a.text))
        # T3-5. Rect and Highlight are position-plus-lengths, so the same rule applies:
        # the origin transforms, w/h only divide by the ratio. Rect was previously absent
        # here, which silently discarded every structured box_2d rectangle.
        elif isinstance(a, (Rect, Highlight)):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(type(a)(
                lx, ly, int(a.w / ratio), int(a.h / ratio), a.label,
            ))
        elif isinstance(a, StepBadge):
            lx, ly = physical_to_local_logical(a.x, a.y, screen)
            out.append(StepBadge(lx, ly, a.n, a.label))
    return out


# --- Overlay window ----------------------------------------------------------

class OverlayWindow(QWidget):
    """One transparent click-through overlay for a single QScreen.

    Responsibilities:
    - Cover exactly one physical monitor with a frameless transparent window
    - Paint a blue animated pointer via QPainter in paintEvent
    - Expose a pointerPos pyqtProperty so QPropertyAnimation can drive it
    - Apply Win32 click-through ex-styles via ctypes after show()

    The per-monitor architecture means each OverlayWindow
    operates entirely in its own screen's local DIP coordinate space. No
    global virtual-desktop coordinates are ever used here -- that's the
    whole point of the per-monitor design (rather than one window spanning
    the full virtual desktop).

    Thread safety: PyQt6 is NOT thread-safe. All methods must be called
    from the main Qt thread only. app.py enforces this via pyqtSignal
    cross-thread communication.
    """

    def __init__(self, screen: QScreen) -> None:
        """Construct the overlay window for a given QScreen.

        Args:
            screen: QScreen for this overlay to cover. Production uses real
                QScreens from QGuiApplication.screens(); tests never call
                this constructor directly (they use _MockOverlayWindow via
                OverlayController dependency injection).
        """
        super().__init__()

        # Qt window flags: frameless, always-on-top, Tool (no taskbar entry)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        # Attribute-based transparency -- NOT stylesheet. Stylesheet
        # transparency is the #1 flicker source on Win 11 per forum.qt.io.
        # Also: do NOT setWindowOpacity(<1.0), that forces Qt's own layered
        # path and overrides the Win32 ex-styles we apply later.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Cover this screen exactly. QScreen.geometry() returns DIP coords,
        # which is what setGeometry expects -- no conversion needed.
        self.setGeometry(screen.geometry())
        self.screen_name = screen.name()  # used by OverlayController._overlay_for_screen

        # Pointer state
        self._pointer_pos = QPoint(0, 0)
        self._pointer_visible = False
        self._visual_state = _OverlayState.IDLE
        self._visual_started_at = time.monotonic()
        self._is_flying = False
        self._recent_pointer_positions: deque[QPointF] = deque(maxlen=9)

        # Visual state flags — gates cursor polygon paint + widget positions.
        # Only one of these can be true at a time (a strict state machine —
        # cursor polygon never coexists with waveform or spinner).
        self._waveform_visible = False
        self._spinner_visible = False
        self._waveform_widget = None  # lazy-created on first show_waveform()
        self._spinner_widget = None   # lazy-created on first show_spinner()

        # Bezier flight animation. Replaces a linear QPropertyAnimation with a
        # quadratic-bezier arc + smoothstep + scale-pulse (no tangent rotation
        # — our cursor is tip-anchored; the tip stays on target through flight).
        self._flight_anim = QVariantAnimation(self)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._flight_anim.valueChanged.connect(self._on_flight_value)
        self._flight_p0: tuple[float, float] = (0.0, 0.0)
        self._flight_p1: tuple[float, float] = (0.0, 0.0)
        self._flight_p2: tuple[float, float] = (0.0, 0.0)
        self._flight_scale: float = 1.0
        self._flight_anim.finished.connect(self._finish_flight_visual)

        # Teaching annotations (arrows/circles/underlines/labels)
        # drawn IN ADDITION to the cursor. Stored in this window's LOCAL-logical
        # coords (the controller transforms physical->local before storing, the
        # same way point_at computes local_x/local_y). Auto-clear after 30s or
        # on the next set_annotations call (next-question clear).
        self._annotations: list = []
        self._annotation_clear_timer = QTimer(self)
        self._annotation_clear_timer.setSingleShot(True)
        self._annotation_clear_timer.timeout.connect(self.clear_annotations)
        self._annotation_started_at: float | None = None
        self._annotation_repaint_timer = QTimer(self)
        self._annotation_repaint_timer.setInterval(16)
        self._annotation_repaint_timer.timeout.connect(self.update)

    def set_visual_state(self, state: _OverlayState) -> None:
        """Set the semantic accent shared by pointer and child visual widgets."""
        self._visual_state = state
        self._visual_started_at = time.monotonic()
        rgb = _accent_rgb(state)
        if self._waveform_widget is not None:
            self._waveform_widget.set_accent(rgb)
        if self._spinner_widget is not None:
            self._spinner_widget.set_accent(rgb)
        self.update()

    def set_annotations(self, annotations: list) -> None:
        """Replace the current annotations (LOCAL-logical coords) + repaint.
        Auto-clears after 30s; a new call cancels the prior timer. An empty
        list clears immediately."""
        self._annotations = list(annotations)
        self._annotation_started_at = time.monotonic() if self._annotations else None
        self.update()
        self._annotation_clear_timer.stop()
        if self._annotations:
            self._annotation_clear_timer.start(30_000)
            self._annotation_repaint_timer.start()
        else:
            self._annotation_repaint_timer.stop()

    def clear_annotations(self) -> None:
        # Cheap no-op when already empty so clearing on every press (the stale-
        # annotation guard) costs nothing in the common no-annotation case.
        if not self._annotations:
            return
        self._annotations = []
        self._annotation_started_at = None
        self._annotation_clear_timer.stop()
        self._annotation_repaint_timer.stop()
        self.update()

    def paintEvent(self, _event) -> None:
        """Draw the accent-coloured arrow cursor polygon at the current pointer position.

        The tip vertex (0,0 in _CURSOR_VERTICES) is anchored at pointer_pos
        so point_at(x,y) puts the tip exactly on the target UI element.

        During FLYING state, self._flight_scale rises to 1.3 at mid-flight and
        returns to 1.0 on landing. We scale around the tip so the tip keeps
        tracking the Bezier curve position exactly (scale around any other
        point would drift the tip).
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Teaching annotations paint independently of the cursor (they show
        # during SPEAKING, when the cursor may be hidden or at rest).
        if self._annotations:
            self._paint_annotations(painter)

        if not self._pointer_visible:
            return
        px, py = self._pointer_pos.x(), self._pointer_pos.y()

        accent = _accent_rgb(self._visual_state)
        scale = self._flight_scale if self._is_flying else _idle_breath_scale(
            time.monotonic() - self._visual_started_at
        )

        # A short, fading flight trail. Positions are tip anchors, so no
        # coordinate transform is introduced by the cosmetic effect.
        if self._is_flying and len(self._recent_pointer_positions) > 1:
            trail = list(self._recent_pointer_positions)[:-1]
            for index, point in enumerate(trail):
                fraction = (index + 1) / len(trail)
                radius = 3.0 + fraction * 6.0
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_with_alpha(accent, int(12 + fraction * 55)))
                painter.drawEllipse(point, radius, radius)

        # Soft shadow + diffused state-colour glow.  The actual tip remains at
        # pointer_pos; only the shadow is offset.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_with_alpha((0, 0, 0), 55))
        painter.drawEllipse(QPointF(px + 7, py + 12), 15, 15)
        painter.setBrush(_with_alpha(accent, 42))
        painter.drawEllipse(QPointF(px + 5, py + 10), 23, 23)

        # Apply mid-flight scale pulse around the tip (0, 0 in cursor space).
        # painter.translate + scale + translate is standard Qt pattern for
        # scaling around a specific point.
        if scale != 1.0:
            painter.save()
            painter.translate(float(px), float(py))
            painter.scale(scale, scale)
            painter.translate(-float(px), -float(py))

        # Radial highlight-to-accent fill gives the vector cursor a little depth.
        # Both stops are DERIVED from the state accent via _tint/_shade rather than
        # written as literals: the highlight used to be a hardcoded pale blue and the
        # lower edge a hardcoded navy, which kept the pointer reading as blue even
        # after theme.OVERLAY_STATE_RGB moved POINTING to Nimbus orange.
        poly = QPolygonF([
            QPointF(px + dx, py + dy) for dx, dy in _CURSOR_VERTICES
        ])
        fill = QRadialGradient(QPointF(px + 4, py + 8), 24)
        fill.setColorAt(0.0, _with_alpha(_tint(accent, 0.82), 245))
        fill.setColorAt(0.42, _with_alpha(accent, 235))
        fill.setColorAt(1.0, _with_alpha(_shade(accent, 0.22), 235))
        # A black outline, drawn UNDER the fill so it never eats into the silhouette.
        #
        # The outline used to be white at alpha 190, which was chosen when the pointer was blue:
        # a pale edge separated a dark shape from dark application chrome. Against Nimbus orange
        # it does the opposite -- orange is already light, so a white edge lowers the contrast at
        # exactly the boundary that defines the shape, and on a light background the whole
        # pointer washed out.
        #
        # Black at 200 is the standard treatment for a system cursor for the same reason Windows
        # and macOS both use it: it is the only edge colour that separates the shape from *both*
        # a white document and a dark IDE. Stroked first at a wider pen so the fill covers the
        # inner half and the visible border stays a crisp ~1px.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_with_alpha((0, 0, 0), 200), 2.6))
        painter.drawPolygon(poly)

        painter.setBrush(fill)
        painter.setPen(QPen(_with_alpha((0, 0, 0), 235), 1.0))
        painter.drawPolygon(poly)

        if scale != 1.0:
            painter.restore()

    def _paint_annotations(self, painter: QPainter) -> None:
        """Draw the teaching annotations. Coords are this window's LOCAL-logical
        space (the controller already mapped physical->local). Shares the cursor's
        state accent from `theme.OVERLAY_STATE_RGB`; circles/underlines are outlines
        (never filled) so they frame the element without covering it."""
        from annotations import (
            Arrow, Circle, Highlight, Label, Rect, StepBadge, Underline,
        )

        elapsed = 0.0 if self._annotation_started_at is None else time.monotonic() - self._annotation_started_at
        opacity = _annotation_opacity(elapsed)
        if opacity <= 0.0:
            return
        draw_progress = _ease_out_cubic(elapsed / 0.3)
        accent_rgb = _accent_rgb(_OverlayState.POINTING)
        accent = _with_alpha(accent_rgb, int(255 * opacity))
        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))

        # T3-5: highlights paint FIRST, unconditionally, before every other shape.
        # A dim layer drawn afterwards would darken the very annotations it is meant to
        # draw attention to. Drawn in a separate pass rather than relying on list order,
        # because the model controls that order and must not be able to break the visual.
        for ann in self._annotations:
            if isinstance(ann, Highlight):
                self._draw_highlight_dim(painter, ann, accent_rgb, opacity)

        for ann in self._annotations:
            if isinstance(ann, Circle):
                rect = QRectF(
                    ann.x - ann.r, ann.y - ann.r, ann.r * 2, ann.r * 2
                )
                glow_pen = QPen(_with_alpha(accent_rgb, int(70 * opacity)))
                glow_pen.setWidth(8)
                painter.setPen(glow_pen)
                painter.drawArc(rect, -90 * 16, int(360 * 16 * draw_progress))
                pen = QPen(accent)
                pen.setWidth(3)
                painter.setPen(pen)
                painter.drawArc(rect, -90 * 16, int(360 * 16 * draw_progress))
                if ann.label:
                    self._draw_label_pill(painter, ann.x + ann.r + 8, ann.y, ann.label, accent_rgb, opacity)
            elif isinstance(ann, Arrow):
                cx, cy = _curved_arrow_control(ann.x1, ann.y1, ann.x2, ann.y2)
                path = QPainterPath(QPointF(ann.x1, ann.y1))
                path.quadTo(QPointF(cx, cy), QPointF(ann.x2, ann.y2))
                shadow = QPen(_with_alpha((0, 0, 0), int(75 * opacity)))
                shadow.setWidth(7)
                shadow.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(shadow)
                painter.drawPath(path)
                pen = QPen(accent)
                pen.setWidth(3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawPath(path)
                self._draw_arrowhead(painter, cx, cy, ann.x2, ann.y2, accent_rgb, opacity)
            elif isinstance(ann, Underline):
                pen = QPen(accent)
                pen.setWidth(3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(ann.x, ann.y), QPointF(ann.x + ann.w, ann.y))
            elif isinstance(ann, Label):
                self._draw_label_pill(painter, ann.x, ann.y, ann.text, accent_rgb, opacity)
            elif isinstance(ann, Rect):
                # Outline only, never filled — the point is to frame the control while
                # leaving it fully readable, same rule the circle follows.
                rect = QRectF(ann.x, ann.y, ann.w, ann.h)
                glow = QPen(_with_alpha(accent_rgb, int(70 * opacity)))
                glow.setWidth(8)
                painter.setPen(glow)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, 6, 6)
                pen = QPen(accent)
                pen.setWidth(3)
                painter.setPen(pen)
                painter.drawRoundedRect(rect, 6, 6)
                if ann.label:
                    self._draw_label_pill(
                        painter, ann.x + ann.w + 8, ann.y + ann.h / 2,
                        ann.label, accent_rgb, opacity,
                    )
            elif isinstance(ann, StepBadge):
                self._draw_step_badge(painter, ann, accent_rgb, opacity)
            # Highlight already painted in the pass above.

    def _draw_highlight_dim(
        self, painter: QPainter, ann, rgb: tuple[int, int, int], opacity: float,
    ) -> None:
        """Dim the whole window EXCEPT ``ann``'s rectangle (T3-5).

        Inverted relative to every other shape: this paints everywhere the target is *not*.
        Implemented as four rectangles around the target rather than a clip path, because
        four fills are cheap, exact, and leave no seam at the edges.

        Also outlines the clear region, so on a screen that is already dark the target is
        still identifiable rather than merely "the bit that did not change".
        """
        alpha = int(150 * opacity)
        dim = _with_alpha((3, 7, 18), alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dim)
        w, h = self.width(), self.height()
        left, top = max(0, ann.x), max(0, ann.y)
        right, bottom = min(w, ann.x + ann.w), min(h, ann.y + ann.h)
        painter.drawRect(QRectF(0, 0, w, top))                       # above
        painter.drawRect(QRectF(0, bottom, w, h - bottom))           # below
        painter.drawRect(QRectF(0, top, left, bottom - top))         # left
        painter.drawRect(QRectF(right, top, w - right, bottom - top))  # right
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_with_alpha(rgb, int(200 * opacity)), 2))
        painter.drawRoundedRect(
            QRectF(left, top, right - left, bottom - top), 6, 6)
        if ann.label:
            self._draw_label_pill(
                painter, right + 8, top + (bottom - top) / 2, ann.label, rgb, opacity)

    def _draw_step_badge(
        self, painter: QPainter, ann, rgb: tuple[int, int, int], opacity: float,
    ) -> None:
        """Draw a filled numbered disc at ``ann`` (T3-5).

        Filled rather than outlined — unlike the framing shapes, a badge is a marker in its
        own right, not a window onto the control underneath, and it needs to read at a
        glance against arbitrary backgrounds.
        """
        radius = 15.0
        rect = QRectF(ann.x - radius, ann.y - radius, radius * 2, radius * 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_with_alpha((0, 0, 0), int(90 * opacity)))
        painter.drawEllipse(rect.translated(0, 1.5))
        painter.setBrush(_with_alpha(rgb, int(240 * opacity)))
        painter.drawEllipse(rect)
        painter.setPen(_with_alpha((255, 255, 255), int(255 * opacity)))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(ann.n))
        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        if ann.label:
            self._draw_label_pill(
                painter, ann.x + radius + 8, ann.y, ann.label, rgb, opacity)

    @staticmethod
    def _draw_label_pill(painter, x: float, y: float, text: str, rgb: tuple[int, int, int], opacity: float) -> None:
        """Draw a readable rounded label chip at a coordinate without changing it."""
        metrics = painter.fontMetrics()
        padding_x, padding_y = 9.0, 5.0
        width = metrics.horizontalAdvance(text) + padding_x * 2
        height = metrics.height() + padding_y * 2
        rect = QRectF(x, y - height / 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_with_alpha((5, 15, 32), int(185 * opacity)))
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QPen(_with_alpha(rgb, int(170 * opacity)), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(_with_alpha((255, 255, 255), int(255 * opacity)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _draw_arrowhead(
        painter: QPainter, x1: float, y1: float, x2: float, y2: float,
        rgb: tuple[int, int, int], opacity: float,
    ) -> None:
        """Filled arrowhead at (x2,y2) pointing along the (x1,y1)->(x2,y2) line."""
        import math

        angle = math.atan2(y2 - y1, x2 - x1)
        size = 14.0
        tip = QPointF(x2, y2)
        left = QPointF(
            x2 - size * math.cos(angle - math.pi / 6),
            y2 - size * math.sin(angle - math.pi / 6),
        )
        right = QPointF(
            x2 - size * math.cos(angle + math.pi / 6),
            y2 - size * math.sin(angle + math.pi / 6),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_with_alpha(rgb, int(255 * opacity)))
        painter.drawPolygon(QPolygonF([tip, left, right]))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def animate_pointer_to(self, local_logical_x: int, local_logical_y: int) -> None:
        """Fly the pointer along a quadratic Bezier arc to (x, y).

        The one deliberate design choice: no tangent rotation. Our cursor is a
        tip-anchored polygon — the tip IS the pointer, so it keeps pointing at
        the target through flight instead of rotating along the tangent.

        Curve: P0=current pointer, P1=midpoint lifted up by min(dist*0.2, 80px),
        P2=target. Duration = clamp(distance/800 s, 0.6s, 1.4s). Smoothstep
        eases progress before bezier interpolation. Scale pulse 1.0→1.3→1.0
        applied on LINEAR progress (not eased) so the peak lands mid-arc.

        Args:
            local_logical_x: within-screen logical DIP x (from physical_to_local_logical)
            local_logical_y: within-screen logical DIP y
        """
        start_x = float(self._pointer_pos.x())
        start_y = float(self._pointer_pos.y())
        end_x = float(local_logical_x)
        end_y = float(local_logical_y)
        dx, dy = end_x - start_x, end_y - start_y
        distance = math.hypot(dx, dy)

        mid_x = (start_x + end_x) / 2.0
        mid_y = (start_y + end_y) / 2.0
        arc_height = min(distance * 0.2, 80.0)

        self._flight_p0 = (start_x, start_y)
        self._flight_p1 = (mid_x, mid_y - arc_height)
        self._flight_p2 = (end_x, end_y)

        duration_ms = int(_flight_duration_s(distance) * 1000.0)

        self._flight_anim.stop()
        self._flight_anim.setDuration(duration_ms)
        self._flight_anim.setStartValue(0.0)
        self._flight_anim.setEndValue(1.0)
        self._pointer_visible = True
        self._is_flying = True
        self._recent_pointer_positions.clear()
        self.set_visual_state(_OverlayState.POINTING)
        self._flight_anim.start()

    def _on_flight_value(self, linear_t) -> None:
        """QVariantAnimation.valueChanged callback: interpolate bezier + pulse.

        linear_t is Qt's raw interpolated value 0.0..1.0. We apply smoothstep
        BEFORE the bezier sample (eased position) but use LINEAR t for the
        scale pulse (peak lands at true midpoint, not eased midpoint).
        """
        t = float(linear_t)
        eased_t = _smoothstep(t)
        x, y = _bezier_position(eased_t, self._flight_p0, self._flight_p1, self._flight_p2)
        self._pointer_pos = QPoint(int(x), int(y))
        self._recent_pointer_positions.append(QPointF(float(x), float(y)))
        self._flight_scale = _scale_pulse(t)
        # On completion, snap to P2 and reset scale (defensive — Qt sometimes
        # emits valueChanged(1.0) slightly early and we want exact landing).
        if t >= 0.9999:
            self._pointer_pos = QPoint(int(self._flight_p2[0]), int(self._flight_p2[1]))
            self._flight_scale = 1.0
        self.update()

    def _finish_flight_visual(self) -> None:
        self._is_flying = False
        self._recent_pointer_positions.clear()
        self._flight_scale = 1.0
        self.update()

    def apply_win32_clickthrough(self) -> None:
        """Apply Win32 ex-styles for click-through. MUST be called after show()."""
        hwnd = int(self.winId())
        apply_clickthrough_styles(hwnd)

    # --- Waveform + Spinner widgets (LISTENING / THINKING states) --------
    #
    # Both widgets position themselves at self._pointer_pos every follow-tick,
    # so they track the OS cursor at 60Hz — they DO NOT stay pinned at the
    # press-time position.
    #
    # show_waveform / show_spinner just create the widget + flip a visibility
    # flag. The 60Hz _on_follow_tick drives their positions.

    def show_waveform(self) -> None:
        """Enter LISTENING state: show waveform widget, hide cursor polygon.
        Widget position is driven by _on_follow_tick (tracks mouse at 60Hz).
        """
        if getattr(self, "_waveform_widget", None) is None:
            self._waveform_widget = WaveformWidget(self)
        self.set_visual_state(_OverlayState.LISTENING)
        self._waveform_widget.show()
        self._waveform_visible = True
        self._pointer_visible = False  # cursor polygon hides during LISTENING
        self.update()

    def hide_waveform(self) -> None:
        """Exit LISTENING state. Does NOT restore cursor visibility — caller
        is expected to transition into THINKING (show_spinner) or IDLE (tick
        will re-show the cursor when no waveform/spinner is active)."""
        if getattr(self, "_waveform_widget", None) is not None:
            self._waveform_widget.hide()
        self._waveform_visible = False
        self.update()

    def show_spinner(self) -> None:
        """Enter THINKING state: show rotating arc, keep cursor hidden.
        Position tracks cursor via _on_follow_tick, same as waveform."""
        if getattr(self, "_spinner_widget", None) is None:
            self._spinner_widget = SpinnerWidget(self)
        self.set_visual_state(_OverlayState.THINKING)
        self._spinner_widget.show()
        self._spinner_visible = True
        self._pointer_visible = False
        self.update()

    def hide_spinner(self) -> None:
        """Exit THINKING state. Cursor will reappear via _on_follow_tick when
        no widget is active, OR via point_at() setting _pointer_visible=True
        right before the bezier arc starts."""
        if getattr(self, "_spinner_widget", None) is not None:
            self._spinner_widget.hide()
        self._spinner_visible = False
        if not self._waveform_visible:
            self.set_visual_state(_OverlayState.IDLE)
        self.update()

    def set_audio_level(self, level: float) -> None:
        """Forward audio level to the waveform widget (no-op if not shown yet)."""
        if getattr(self, "_waveform_widget", None) is not None:
            self._waveform_widget.set_audio_level(level)

    def set_caption(self, text: str) -> None:
        """Show/update the live transcript caption (T4-5). Lazily builds the widget."""
        if getattr(self, "_caption_widget", None) is None:
            self._caption_widget = CaptionWidget(self)
        self._caption_widget.set_caption(text)

    def clear_caption(self) -> None:
        """Hide the caption. No-op if it was never created."""
        if getattr(self, "_caption_widget", None) is not None:
            self._caption_widget.clear_caption()


# --- Spinner widget (THINKING state) -----------------------------------------
#
# The THINKING-state spinner: a rotating arc shown between hotkey RELEASE and
# Nimbus returning a coordinate, so the user sees feedback during the LLM wait.

_SPINNER_PERIOD_S = 0.8
"""Full-rotation period (0.8s), a smooth continuous spin."""

_SPINNER_ARC_START_DEG = 54.0   # 0.15 * 360
_SPINNER_ARC_SPAN_DEG = 252.0   # (0.85 - 0.15) * 360  → 70% of full circle

_SPINNER_WIDGET_SIZE = 28  # px — leaves room around the 14px arc for stroke + glow
_SPINNER_ARC_DIAMETER = 14.0
_SPINNER_STROKE_WIDTH = 2.5


def _spinner_angle_deg(elapsed_s: float) -> float:
    """Linear-rotation angle in degrees, wrapping at full period (0.8s).

    Returns angle in [0, 360). Pure function (no Qt), easy to unit test.
    """
    return (elapsed_s / _SPINNER_PERIOD_S * 360.0) % 360.0


class SpinnerWidget(QWidget):
    """14×14 rotating arc, shown during THINKING state (release → coord).

    The arc covers 70% of a circle (trimmed 15% at top + 15% at bottom).
    Rotates continuously, 0.8s per full rotation.

    Rendered via QPainter on a transparent, mouse-transparent QWidget. The
    widget size is larger than the arc so the stroke + anti-aliasing don't
    clip at the edges.

    Thread safety: show()/hide() are Qt-main-thread only (called via
    pyqtSignal slots in app.py). The timer ticks on the main thread too.
    """

    WIDGET_SIZE = _SPINNER_WIDGET_SIZE
    UPDATE_INTERVAL_MS = 28  # ~36 fps — same cadence as waveform for consistency

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.WIDGET_SIZE, self.WIDGET_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        import time as _t
        self._phase_start = _t.time()
        self._accent_rgb = _accent_rgb(_OverlayState.THINKING)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(self.UPDATE_INTERVAL_MS)

    def _tick(self) -> None:
        self.update()

    def set_accent(self, rgb: tuple[int, int, int]) -> None:
        self._accent_rgb = rgb
        self.update()

    def paintEvent(self, _event) -> None:
        """Draw a rotating 70% arc in dodger blue + subtle outer glow."""
        import time as _t
        from PyQt6.QtCore import QRectF as _QRectF

        elapsed = _t.time() - self._phase_start
        angle = _spinner_angle_deg(elapsed)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Center the arc in the widget + rotate by `angle` around the center.
        # Individual tail segments rotate from the current animation angle.

        # Outer glow — a faint circle slightly larger than the arc.
        glow_pen = QPen(_with_alpha(self._accent_rgb, 45))
        glow_pen.setWidthF(_SPINNER_STROKE_WIDTH + 2.0)
        glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        arc_rect = QRectF(
            (self.WIDGET_SIZE - _SPINNER_ARC_DIAMETER) / 2.0,
            (self.WIDGET_SIZE - _SPINNER_ARC_DIAMETER) / 2.0,
            _SPINNER_ARC_DIAMETER,
            _SPINNER_ARC_DIAMETER,
        )
        # QPainter.drawArc uses 1/16-degree units.
        painter.drawArc(
            arc_rect,
            int(angle * 16),
            int(11 * 16),
        )

        # Layer short, increasingly opaque round-capped arcs into a comet tail.
        for start, opacity in reversed(_spinner_tail_segments(angle)):
            tail_pen = QPen(_with_alpha(self._accent_rgb, int(235 * opacity)))
            tail_pen.setWidthF(_SPINNER_STROKE_WIDTH)
            tail_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(tail_pen)
            painter.drawArc(arc_rect, int(start * 16), int(11 * 16))

        # Main arc — fully opaque dodger blue.
        main_pen = QPen(_with_alpha(self._accent_rgb, 230))
        main_pen.setWidthF(_SPINNER_STROKE_WIDTH)
        main_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(main_pen)
        painter.drawArc(
            arc_rect,
            int(angle * 16),
            int(11 * 16),
        )


# --- Waveform widget -----------------------------------------------------------

class WaveformWidget(QWidget):
    """5-bar audio-level waveform rendered via QPainter at ~36 fps.

    During PTT hold, this widget shows at the cursor position (OverlayWindow
    hides the cursor polygon). Bar heights come from _waveform_bar_height() using
    the RMS level set via set_audio_level() + an independent idle-pulse sine
    so bars are never fully flat.

    Thread safety: set_audio_level may be called from any thread (it just
    assigns a float). The timer-driven update() runs on the Qt main thread.
    Rendering runs on the main thread via paintEvent.
    """

    BAR_WIDTH = 2
    BAR_SPACING = 2
    WIDGET_HEIGHT = 18  # px — slightly taller than cursor for visibility
    WIDGET_WIDTH = _WAVEFORM_BAR_COUNT * BAR_WIDTH + (_WAVEFORM_BAR_COUNT - 1) * BAR_SPACING  # = 18
    UPDATE_INTERVAL_MS = 28  # ~36 fps cadence

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.WIDGET_WIDTH, self.WIDGET_HEIGHT)
        # Transparent bg + mouse-transparent (clicks pass through to apps below).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._audio_level: float = 0.0
        self._accent_rgb = _accent_rgb(_OverlayState.LISTENING)
        import time as _t
        self._phase_start = _t.time()

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(self.UPDATE_INTERVAL_MS)

    def set_audio_level(self, level: float) -> None:
        """Update live audio level (called from app.py via pyqtSignal)."""
        self._audio_level = max(0.0, min(float(level), 1.0))

    def set_accent(self, rgb: tuple[int, int, int]) -> None:
        self._accent_rgb = rgb
        self.update()

    def _tick(self) -> None:
        """Trigger a repaint on each timer tick — bars redraw at ~36 fps."""
        self.update()

    def paintEvent(self, _event) -> None:
        """Draw the 5 vertical bars centered vertically in the widget."""
        import time as _t

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        phase = _t.time() - self._phase_start
        for i in range(_WAVEFORM_BAR_COUNT):
            bar_h = _waveform_bar_height(i, self._audio_level, phase)
            x = i * (self.BAR_WIDTH + self.BAR_SPACING)
            y = (self.WIDGET_HEIGHT - bar_h) / 2.0
            rect = QRectF(float(x), float(y), float(self.BAR_WIDTH), float(bar_h))
            bright = _waveform_color_rgb(self._audio_level, self._accent_rgb)
            fill = QLinearGradient(float(x), float(y), float(x), float(y + bar_h))
            fill.setColorAt(0.0, _with_alpha(bright, 245))
            fill.setColorAt(1.0, _with_alpha(self._accent_rgb, 210))
            painter.setBrush(fill)
            painter.drawRoundedRect(
                rect,
                1.5, 1.5,
            )


CAPTION_MAX_CHARS = 220
"""Longest caption rendered. Beyond this the head is elided (T4-5).

The tail is what matters: a caption exists so the user can check the *end* of what was
heard, which is the part still arriving."""

CAPTION_HIDE_DELAY_MS = 4_000
"""How long a caption lingers once no further text arrives (T4-5).

Long enough to read a misheard sentence and reach for Esc, short enough that it is gone
before the next interaction."""


def elide_caption(text: str, max_chars: int = CAPTION_MAX_CHARS) -> str:
    """Trim a caption to ``max_chars``, dropping from the FRONT (T4-5).

    Front-elision is deliberate and the opposite of most truncation: speech arrives
    incrementally, so the newest words are at the end and are the ones the user has not yet
    verified. Cutting the tail would hide exactly the part they are watching for.

    Pure function so the caption's only real logic is testable without Qt.
    """
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    return "\u2026" + text[-(max_chars - 1):]


class CaptionWidget(QWidget):
    """Live speech-to-text caption, click-through, hosted by one overlay window (T4-5).

    Closes the loop on the most common failure in a voice app: being misheard. Without this
    the user speaks, waits, and receives a confident answer to a question they never asked,
    with no way to tell which step went wrong.

    Pairs directly with ``T2-2``: seeing the wrong transcript while the spinner is still
    turning means Esc can abort *before* the wrong answer is spoken.

    **The caption is not equally live on every provider**, and the difference is the STT
    backend's, not this widget's:

    * ``AssemblyAIStreamingSTT`` emits genuine streaming partials, so text appears
      word-by-word while the user is still speaking.
    * ``FasterWhisperSTT`` is batch -- it fires exactly once, from ``stop_recording()``,
      after transcribing the whole buffer. The caption therefore appears at release, next to
      the thinking spinner.

    Both are useful; only the first is "live". Anchored bottom-centre rather than following
    the cursor, because text that chases the mouse is unreadable and the cursor region is
    already occupied by the waveform and spinner.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Non-negotiable: the overlay is click-through, and a caption that swallowed clicks
        # would make the screen underneath unusable while it was visible.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def set_caption(self, text: str) -> None:
        """Show or update the caption. Empty text hides it immediately."""
        self._text = elide_caption(text)
        if not self._text:
            self._timer.stop()
            self.hide()
            return
        self._reposition()
        self.show()
        self.raise_()
        # Restart on every update so a pause mid-sentence does not clear text the user is
        # still reading.
        self._timer.start(CAPTION_HIDE_DELAY_MS)
        self.update()

    def clear_caption(self) -> None:
        self._text = ""
        self._timer.stop()
        self.hide()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(920, max(360, int(parent.width() * 0.55)))
        height = 74
        self.resize(width, height)
        self.move((parent.width() - width) // 2, parent.height() - height - 96)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(QPen(_with_alpha((148, 163, 184), 150), 1))
        painter.setBrush(_with_alpha((10, 18, 35), 225))
        painter.drawRoundedRect(rect, 14, 14)
        painter.setPen(_with_alpha((226, 232, 240), 250))
        painter.setFont(QFont("Segoe UI", 11))
        painter.drawText(
            rect.adjusted(18, 10, -18, -10),
            int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
            self._text,
        )


class ToastWidget(QWidget):
    """A compact click-through status toast hosted by one overlay window."""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._message = ""
        self._severity = "error"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_toast(self, message: str, severity: str = "error") -> None:
        self._message, self._severity = message, severity
        self.resize(390, 58)
        if self.parentWidget() is not None:
            parent = self.parentWidget()
            self.move(max(16, parent.width() - self.width() - 24), 28)
        self.show()
        self.raise_()
        self._timer.start(5_000)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rgb = (239, 68, 68) if self._severity == "error" else (59, 130, 246)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(QPen(_with_alpha(rgb, 190), 1))
        painter.setBrush(_with_alpha((10, 18, 35), 235))
        painter.drawRoundedRect(rect, 12, 12)
        painter.setPen(_with_alpha((255, 255, 255), 245))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.drawText(rect.adjusted(16, 8, -16, -8), Qt.TextFlag.TextWordWrap, self._message)


# --- Controller --------------------------------------------------------------

class OverlayController:
    """Manages one OverlayWindow per physical monitor + cursor following.

    State machine:
    - IDLE: 16ms timer polls QCursor.pos(), cursor follows mouse with offset
    - POINTING: timer stopped, animation drives cursor to Nimbus's target,
      3s dwell, then fly back to mouse, resume IDLE
    - HIDDEN: timer stopped, overlays hidden for screen capture

    Always-visible mode: the cursor is visible from launch.

    Dependency injection: overlay_factory, screens, and cursor_pos_fn are
    injectable so tests can substitute mocks without real QWidgets.
    """

    _FOLLOW_OFFSET_X = 35
    _FOLLOW_OFFSET_Y = 25
    _DWELL_MS = 3000
    _FOLLOW_INTERVAL_MS = 16

    def __init__(
        self,
        overlay_factory=None,
        screens: list[QScreen] | None = None,
        cursor_pos_fn=None,
    ) -> None:
        if overlay_factory is None:
            overlay_factory = OverlayWindow
        if screens is None:
            screens = QGuiApplication.screens()
        self._cursor_pos_fn = cursor_pos_fn or QCursor.pos

        self.overlays: list[OverlayWindow] = []
        for qscreen in screens:
            overlay = overlay_factory(qscreen)
            overlay.show()
            overlay.apply_win32_clickthrough()
            self.overlays.append(overlay)

        self._state = _OverlayState.IDLE
        self._pointing_overlay: OverlayWindow | None = None

        self._follow_timer = QTimer()
        self._follow_timer.setInterval(self._FOLLOW_INTERVAL_MS)
        self._follow_timer.timeout.connect(self._on_follow_tick)
        self._follow_timer.start()

    def _on_follow_tick(self) -> None:
        """Poll cursor position and lerp the buddy cursor toward it.

        Instead of snapping directly to the mouse position (which looks like
        teleporting), each frame moves 15% of the remaining distance. This
        creates a smooth 'buddy following you' feel — the cursor lazily
        drifts toward your mouse like a puppy trotting after you.
        """
        if self._state != _OverlayState.IDLE:
            return
        global_pos = self._cursor_pos_fn()
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            return
        target_overlay = self._overlay_for_screen(screen)
        if target_overlay is None:
            return
        for ov in self.overlays:
            if ov is not target_overlay:
                ov._pointer_visible = False
                ov.update()

        local = target_overlay.mapFromGlobal(global_pos)
        target_x = local.x() + self._FOLLOW_OFFSET_X
        target_y = local.y() + self._FOLLOW_OFFSET_Y

        current_x = target_overlay._pointer_pos.x()
        current_y = target_overlay._pointer_pos.y()

        dx = target_x - current_x
        dy = target_y - current_y
        dist_sq = dx * dx + dy * dy

        if dist_sq < 4:
            new_x, new_y = target_x, target_y
        else:
            step_x = int(dx * _CURSOR_FOLLOW_LERP)
            step_y = int(dy * _CURSOR_FOLLOW_LERP)
            if dx != 0 and step_x == 0:
                step_x = 1 if dx > 0 else -1
            if dy != 0 and step_y == 0:
                step_y = 1 if dy > 0 else -1
            new_x = current_x + step_x
            new_y = current_y + step_y

        target_overlay._pointer_pos = QPoint(new_x, new_y)

        # Visibility gating: waveform (LISTENING) and spinner (THINKING) hide
        # the cursor polygon; otherwise cursor polygon is visible. Widgets are
        # repositioned to the new cursor position so they track the OS mouse
        # at 60Hz.
        wf_widget = getattr(target_overlay, "_waveform_widget", None)
        sp_widget = getattr(target_overlay, "_spinner_widget", None)

        if getattr(target_overlay, "_waveform_visible", False) and wf_widget is not None:
            wf_widget.move(
                int(new_x - wf_widget.width() // 2),
                int(new_y - wf_widget.height() // 2),
            )
            target_overlay._pointer_visible = False
        elif getattr(target_overlay, "_spinner_visible", False) and sp_widget is not None:
            sp_widget.move(
                int(new_x - sp_widget.width() // 2),
                int(new_y - sp_widget.height() // 2),
            )
            target_overlay._pointer_visible = False
        else:
            target_overlay._pointer_visible = True

        target_overlay.update()

    def point_at(
        self,
        physical_x: int,
        physical_y: int,
        monitor: dict,
    ) -> None:
        """Fly the cursor from current position to Nimbus's target coordinate."""
        self._follow_timer.stop()
        self._state = _OverlayState.POINTING

        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target_overlay = self._overlay_for_screen(target_screen)
        if target_overlay is None:
            if not self.overlays:
                return
            target_overlay = self.overlays[0]

        self._pointing_overlay = target_overlay
        local_x, local_y = physical_to_local_logical(
            physical_x, physical_y, target_screen
        )
        target_overlay._pointer_visible = True
        target_overlay.animate_pointer_to(local_x, local_y)
        target_overlay._flight_anim.finished.connect(self._on_point_animation_finished)

    def _on_point_animation_finished(self) -> None:
        """After arriving at target, dwell 3s then fly back to mouse."""
        if self._pointing_overlay:
            self._pointing_overlay._flight_anim.finished.disconnect(
                self._on_point_animation_finished
            )
        QTimer.singleShot(self._DWELL_MS, self._fly_back)

    def _fly_back(self) -> None:
        """Animate the cursor back to the current mouse position."""
        if self._state == _OverlayState.HIDDEN:
            return
        if self._pointing_overlay is None:
            self._resume_idle()
            return
        global_pos = self._cursor_pos_fn()
        local = self._pointing_overlay.mapFromGlobal(global_pos)
        target = QPoint(
            local.x() + self._FOLLOW_OFFSET_X,
            local.y() + self._FOLLOW_OFFSET_Y,
        )
        self._pointing_overlay._flight_anim.finished.connect(self._on_return_finished)
        self._pointing_overlay.animate_pointer_to(target.x(), target.y())

    def _on_return_finished(self) -> None:
        """Return flight complete — resume mouse following."""
        if self._pointing_overlay:
            self._pointing_overlay._flight_anim.finished.disconnect(
                self._on_return_finished
            )
        self._pointing_overlay = None
        self._resume_idle()

    def _resume_idle(self) -> None:
        if self._state == _OverlayState.HIDDEN:
            return
        self._state = _OverlayState.IDLE
        for overlay in self.overlays:
            if hasattr(overlay, "set_visual_state"):
                overlay.set_visual_state(_OverlayState.IDLE)
        self._follow_timer.start()

    def _overlay_for_screen(self, screen: QScreen) -> OverlayWindow | None:
        target_name = screen.name()
        for overlay in self.overlays:
            if overlay.screen_name == target_name:
                return overlay
        return None

    def show_annotations(self, annotations: list, monitor: dict) -> None:
        """Render teaching annotations (PHYSICAL-pixel coords) on the monitor's
        overlay. Routes to the per-monitor window like point_at, transforms the
        coords physical->local via annotations_to_local, and clears annotations
        on every OTHER overlay so only the active screen shows them."""
        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target_overlay = self._overlay_for_screen(target_screen)
        if target_overlay is None:
            if not self.overlays:
                return
            target_overlay = self.overlays[0]

        local = annotations_to_local(annotations, target_screen)
        for overlay in self.overlays:
            if overlay is target_overlay:
                overlay.set_annotations(local)
            else:
                overlay.clear_annotations()

    def clear_all_annotations(self) -> None:
        """Clear teaching annotations on every overlay. Called at the start of
        each new interaction so stale shapes never survive a no-speech /
        cancelled / errored turn (they'd otherwise linger until the 30s timer)."""
        for overlay in self.overlays:
            overlay.clear_annotations()

    def show_toast(self, message: str, severity: str = "error") -> None:
        """Show a non-modal error/status message on the active overlay."""
        for overlay in self.overlays:
            if not hasattr(overlay, "_toast_widget"):
                overlay._toast_widget = ToastWidget(overlay)
            overlay._toast_widget.show_toast(message, severity)

    def hide_for_capture(self) -> None:
        """Hide ALL overlays + stop timer for screen capture."""
        self._follow_timer.stop()
        if self._pointing_overlay and self._pointing_overlay._flight_anim.state() == QVariantAnimation.State.Running:
            self._pointing_overlay._flight_anim.stop()
            try:
                self._pointing_overlay._flight_anim.finished.disconnect()
            except TypeError:
                pass
        self._state = _OverlayState.HIDDEN
        for overlay in self.overlays:
            overlay._pointer_visible = False
            overlay.hide()

    def show_after_capture(self) -> None:
        """Re-show ALL overlays + restart cursor following."""
        for overlay in self.overlays:
            overlay.show()
            overlay.apply_win32_clickthrough()
        self._pointing_overlay = None
        self._state = _OverlayState.IDLE
        self._follow_timer.start()

    # --- Waveform + Spinner delegation (called by app.py state machine) ----
    #
    # Position is driven by _on_follow_tick, NOT by show_waveform/show_spinner
    # args. The monitor arg is retained for multi-monitor routing: the widget
    # is created on the OverlayWindow whose screen the cursor is on AT PRESS/
    # RELEASE time. If the cursor crosses monitors mid-hold, the widget stays
    # on its original monitor (known limitation, deferred as future work).

    def show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        """Show waveform on the overlay containing (physical_x, physical_y).
        Called by app.py on hotkey PRESS."""
        target_overlay = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        if target_overlay is not None and hasattr(target_overlay, "show_waveform"):
            target_overlay.show_waveform()

    def hide_waveform(self) -> None:
        """Hide waveform on all overlays. app.py calls this on hotkey RELEASE."""
        for overlay in self.overlays:
            if hasattr(overlay, "hide_waveform"):
                overlay.hide_waveform()

    def show_spinner(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        """Show spinner (THINKING state) on the overlay containing the cursor.
        Called by app.py on hotkey RELEASE, immediately after hide_waveform."""
        target_overlay = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        if target_overlay is not None and hasattr(target_overlay, "show_spinner"):
            target_overlay.show_spinner()

    def hide_spinner(self) -> None:
        """Hide spinner on all overlays. Called by app.py when:
        - Nimbus returns a coordinate (just before sig_point_at → bezier fires)
        - Text-only response path (no coordinate)
        - Pipeline error / cancel paths (don't leave spinner spinning)
        - Top of _handle_press (clear stale from prior interaction)"""
        for overlay in self.overlays:
            if hasattr(overlay, "hide_spinner"):
                overlay.hide_spinner()

    def set_audio_level(self, level: float) -> None:
        """Forward audio level to ALL overlays (only the one with a showing
        waveform widget renders — others are no-ops)."""
        for overlay in self.overlays:
            if hasattr(overlay, "set_audio_level"):
                overlay.set_audio_level(level)

    def set_caption(self, text: str, physical_x: int, physical_y: int,
                    monitor: dict) -> None:
        """Show the caption on the overlay containing the cursor point (T4-5).

        Routed to ONE monitor rather than broadcast like ``set_audio_level``: a sentence
        duplicated across three screens is noise, and the user is looking at the screen they
        asked about. Any stale caption elsewhere is cleared first, so a mid-session monitor
        switch cannot leave two captions on screen.
        """
        target = self._pick_overlay_for_point(physical_x, physical_y, monitor)
        for overlay in self.overlays:
            if overlay is not target and hasattr(overlay, "clear_caption"):
                overlay.clear_caption()
        if target is not None and hasattr(target, "set_caption"):
            target.set_caption(text)

    def clear_captions(self) -> None:
        """Hide the caption on every overlay."""
        for overlay in self.overlays:
            if hasattr(overlay, "clear_caption"):
                overlay.clear_caption()

    def _pick_overlay_for_point(
        self, physical_x: int, physical_y: int, monitor: dict,
    ):
        """Route a physical-pixel point to the right OverlayWindow.
        Returns None if no overlay exists (empty screens list)."""
        screens = QGuiApplication.screens()
        target_screen = screen_for_monitor(monitor, screens)
        target = self._overlay_for_screen(target_screen)
        if target is None and self.overlays:
            target = self.overlays[0]
        return target


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    # Manual click-through verification. Run: py -3.13 -m overlay
    #
    # Opens one overlay per physical monitor and animates a blue pointer
    # through 5 positions (4 corners + center) of the primary overlay,
    # cycling every 1.5 seconds. User confirms the 5-point checklist below
    # by watching the overlay and trying to click on apps underneath.
    import sys

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from capture import set_dpi_awareness

    set_dpi_awareness()  # Idempotent if already set by PyQt6

    print("=" * 70)
    print("Nimbus -- overlay.py manual click-through verification")
    print("=" * 70)

    app = QApplication(sys.argv)
    controller = OverlayController()

    print(f"\nCreated {len(controller.overlays)} overlay(s):")
    for i, overlay in enumerate(controller.overlays):
        geom = overlay.geometry()
        print(
            f"  [{i}] screen={overlay.screen_name} "
            f"geometry=({geom.x()}, {geom.y()}, {geom.width()}, {geom.height()}) DIP"
        )

    # Build a 5-point test pattern for the primary overlay:
    # top-left, top-right, bottom-right, bottom-left, center
    primary = controller.overlays[0]
    primary_geom = primary.geometry()
    primary_w = primary_geom.width()
    primary_h = primary_geom.height()
    margin = 100  # DIP
    test_positions = [
        (margin, margin),
        (primary_w - margin, margin),
        (primary_w - margin, primary_h - margin),
        (margin, primary_h - margin),
        (primary_w // 2, primary_h // 2),
    ]

    # itertools.cycle gives us an infinite iterator over the positions
    # without any mutable external state. Cleaner than a [0] counter.
    _positions_iter = cycle(test_positions)

    def _animate_next() -> None:
        x, y = next(_positions_iter)
        primary.animate_pointer_to(x, y)
        print(f"  -> pointer target: ({x}, {y}) DIP on {primary.screen_name}")

    _timer = QTimer()
    _timer.timeout.connect(_animate_next)
    _timer.start(1500)  # move every 1.5 seconds
    _animate_next()  # first position immediately

    print("\nManual verification checklist (confirm each):")
    print("  1. Blue arrow cursor visible, animates smoothly through 5 positions")
    print("  2. Clicks PASS THROUGH to apps underneath (try clicking desktop icons)")
    print("  3. No taskbar entry for overlay")
    print("  4. Overlay doesn't steal focus from the active app")
    print("  5. Pointer lands on plausible screen positions (corners, center)")
    print("\nClose with Ctrl+C in this terminal or close the Python process.")
    sys.exit(app.exec())
