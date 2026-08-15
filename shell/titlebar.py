"""Frameless title bar: drag, minimise, maximise, close (SHELL_AND_CHAT.md §3 `S-1`).

## The resize decision, and the measurement behind it

§3 warns that frameless windows lose snap and edge-resize, and that this is the single most
underestimated part of a custom title bar. It offers two routes: implement ``WM_NCHITTEST`` via
``nativeEvent``, or accept a resize grip.

**Measured before choosing, and the measurement rules out the ``WM_NCHITTEST`` route as
written.** A Qt window with ``Qt.FramelessWindowHint`` loses ``WS_THICKFRAME``:

    ordinary window   GWL_STYLE = 0x96CF0000   THICKFRAME=True  CAPTION=True
    frameless window  GWL_STYLE = 0x96000000   THICKFRAME=False CAPTION=False

Windows only runs its sizing loop for a window that has ``WS_THICKFRAME``, so returning
``HTLEFT``/``HTBOTTOMRIGHT`` from ``WM_NCHITTEST`` does nothing on its own. Making it work
means adding ``WS_THICKFRAME`` back and then handling ``WM_NCCALCSIZE`` to suppress the frame
it brings with it -- and that path also has to convert ``lParam``'s **physical** pixels to
logical ones per monitor, which is precisely the per-monitor-DPI assumption §3's ⚠ VERIFY #2
warns against.

**So: Qt-level edge hit-testing that hands off to the OS's own modal loops** --
``QWindow.startSystemResize(edge)`` for the borders and ``QWindow.startSystemMove()`` for the
title bar. Both were verified present on this build. That choice buys three things the
``nativeEvent`` route would have had to re-earn:

* **Snap and Aero shake come back for free**, because the drag is the OS's drag, not ours.
* **No DPI maths anywhere.** The OS owns the resize, so nothing in the shell converts
  coordinates and nothing caches a device-pixel ratio. Dragging between monitors at different
  scaling is not our problem to get wrong.
* Native double-click-an-edge behaviour and the correct resize cursors.

A ``QSizeGrip`` still sits in the window's bottom-right corner (see ``shell/window.py``) as a
visible affordance and as the fallback when there is no native handle at all -- under pytest,
for instance, where the window is never shown.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

import brand
import theme

TITLEBAR_HEIGHT = theme.SPACE[7]
"""48px, the top of the spacing scale. Tall enough for a 34px control plus breathing room."""

BUTTON_WIDTH = theme.SPACE[6]
BUTTON_HEIGHT = theme.SPACE[5]
"""32x24 window buttons, as chips inset in the bar rather than full-height hit zones.

Full-height buttons on a transparent fill were the original design and they were close to
invisible against a near-black title bar -- the first thing anyone said about the window. An
inset chip with a border and a resting background reads as a control at a glance, which is the
entire job of a window button."""


_GLYPH_MINIMISE = "minimise"
_GLYPH_MAXIMISE = "maximise"
_GLYPH_RESTORE = "restore"
_GLYPH_CLOSE = "close"
"""Names, not characters. See ``GlyphButton`` for why the glyphs are painted."""


class GlyphButton(QPushButton):
    """A window button whose glyph is drawn, so no font can misrender it.

    The maximise glyph was ``\\u2b1c`` WHITE LARGE SQUARE, which Segoe UI renders as a **solid
    white block** -- a filled white chip sitting in the title bar. Swapping in another codepoint
    only moves the problem: ``\\u25a1`` is too small against a 15pt wordmark, ``\\u2610`` is a
    ballot box with its own metrics and baseline. Two strokes and a rectangle outline cost less
    than picking a font-safe character, and look identical on every machine.

    Kept as a ``QPushButton`` so the whole ``#WindowButton`` stylesheet -- resting chip, hover,
    the close button's ``DANGER`` -- still applies to the background. Only the glyph is ours.
    """

    def __init__(self, glyph: str, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._glyph = glyph

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter, QPen

        super().paintEvent(event)  # the styled chip, hover and press states

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Enabled state only: a disabled window button would still need to read as one, and
        # TEXT_DISABLED is deliberately below AA.
        colour = theme.qcolor(theme.TEXT_PRIMARY if self.isEnabled() else theme.TEXT_MUTED)
        painter.setPen(QPen(colour, 1.4))

        box = min(self.width(), self.height()) // 2
        cx, cy = self.width() / 2, self.height() / 2
        half = box / 2

        if self._glyph == _GLYPH_MINIMISE:
            painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
        elif self._glyph == _GLYPH_MAXIMISE:
            painter.setBrush(Qt.BrushStyle.NoBrush)  # outline only: no white fill
            painter.drawRect(int(cx - half), int(cy - half), int(box), int(box))
        elif self._glyph == _GLYPH_RESTORE:
            # Two offset outlines, the Windows convention for "restore down".
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = box * 0.28
            painter.drawRect(int(cx - half), int(cy - half + inset),
                             int(box - inset), int(box - inset))
            painter.drawRect(int(cx - half + inset), int(cy - half),
                             int(box - inset), int(box - inset))
        else:  # close
            painter.drawLine(int(cx - half), int(cy - half), int(cx + half), int(cy + half))
            painter.drawLine(int(cx + half), int(cy - half), int(cx - half), int(cy + half))
        painter.end()


class TitleBar(QFrame):
    """The window's own title bar. Emits intent; the window decides what to do with it.

    Deliberately does not call ``parent().close()`` and friends itself. ``MainWindow``'s
    ``closeEvent`` hides to tray rather than quitting (Invariant 5), and a title bar that
    reached past the window to kill it would bypass that.
    """

    sig_minimise = pyqtSignal()
    sig_maximise_toggled = pyqtSignal()
    sig_close = pyqtSignal()

    def __init__(self, title: str = "Nimbus", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLEBAR_HEIGHT)

        layout = QHBoxLayout(self)
        # `SPACE[2]` on the left, down from `SPACE[4]`. The lockup moves as one -- the gap between
        # the mark and the wordmark is set separately below and is unchanged -- so this is purely
        # how far the pair sits from the window edge. At 20px it read as indented; at 12px it lines
        # up closer to the nav rail's own left padding beneath it.
        layout.setContentsMargins(
            theme.SPACE[2], theme.SPACE[1], theme.SPACE[1], theme.SPACE[1])
        layout.setSpacing(theme.SPACE[1])

        # The mark and the wordmark, as one lockup.
        #
        # `SPACE[1]` between them, not the layout's default: the mark's own artwork carries no
        # side bearing once trimmed, so anything wider reads as two unrelated elements and
        # anything tighter has the glyph touching the N.
        self.mark = brand.mark_label(brand.TITLEBAR_MARK_HEIGHT, self)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        # `SPACE[0]`, down from `SPACE[1]`. The trimmed artwork has no side bearing of its own,
        # so 8px of layout gap plus the wordmark's natural left sidebearing read as a wider space
        # than the number suggests -- the mark and the text looked like two elements rather than
        # one lockup. 4px closes it without the glyph touching the N.
        layout.addSpacing(theme.SPACE[0])

        # The product name, and nothing else.
        #
        # It used to carry the current page as a subtitle too -- "Nimbus  Home" -- which is
        # redundant: the nav rail already shows which page is selected, in a larger type size,
        # a few pixels below. A title bar restating what is visible next to it is noise, and it
        # made the wordmark read as a breadcrumb rather than as the product.
        self._title = QLabel(title)
        self._title.setObjectName("WordMark")
        # Optically centred, not box-centred.
        #
        # Both widgets were vertically centred and the wordmark still sat low, because a QLabel
        # centres its *line box* -- ascent plus descent -- while "NIMBUS" is all caps and has no
        # descenders. So roughly the descent's worth of empty space sits under the letters and
        # pushes the visible glyphs up... except the label is taller than the mark, so the net
        # effect was the text reading low against it. Nudging by the descent aligns the cap
        # heights, which is what the eye actually compares.
        descent = self._title.fontMetrics().descent()
        self._title.setContentsMargins(0, 0, 0, descent)
        layout.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        # Glyphs are painted, not typed. `\u2b1c` (the previous maximise glyph) is WHITE LARGE
        # SQUARE, and Segoe UI renders it as a *filled* white block -- a solid white chip in the
        # title bar, which is what it looked like. Every text substitute has the same class of
        # problem on some font fallback: `\u25a1` is too small, `\u2610` is a ballot box with
        # different metrics. Painting three primitives removes the font from the question.
        self._minimise = self._window_button(_GLYPH_MINIMISE, "Minimise", self.sig_minimise)
        self._maximise = self._window_button(
            _GLYPH_MAXIMISE, "Maximise", self.sig_maximise_toggled)
        self._close = self._window_button(_GLYPH_CLOSE, "Close to tray", self.sig_close)
        self._close.setObjectName("WindowButtonClose")
        for button in (self._minimise, self._maximise, self._close):
            layout.addWidget(button)

    # -- public ---------------------------------------------------------------

    def set_subtitle(self, text: str) -> None:
        """Accepted and ignored (see ``__init__``): the nav rail already names the page.

        Kept as a no-op rather than removed because ``MainWindow.show_page`` calls it on every
        navigation, and the alternative is either a conditional at the call site or a page
        change that can raise. It stays part of the interface in case a *non-redundant*
        subtitle earns its place later -- a licence state, say.
        """
        return None

    def set_maximised(self, maximised: bool) -> None:
        """Swap the maximise glyph so the button describes what it will do next."""
        self._maximise.set_glyph(_GLYPH_RESTORE if maximised else _GLYPH_MAXIMISE)
        self._maximise.setToolTip("Restore" if maximised else "Maximise")

    # -- internals ------------------------------------------------------------

    def _window_button(self, glyph: str, tooltip: str, signal) -> QPushButton:
        button = GlyphButton(glyph, self)
        button.setObjectName("WindowButton")
        button.setFixedSize(BUTTON_WIDTH, BUTTON_HEIGHT)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(signal.emit)
        return button

    def mousePressEvent(self, event) -> None:
        """Hand the drag to Windows so snap, Aero shake and multi-monitor DPI all work.

        Moving the window by hand from ``mouseMoveEvent`` is the obvious implementation and
        the wrong one: it loses snap entirely and has to do its own logical/physical
        conversion on every move.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        window = self.window().windowHandle()
        if window is not None and window.startSystemMove():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.sig_maximise_toggled.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def titlebar_qss() -> str:
    """Deprecated, and deliberately empty.

    The title bar's styling moved into ``theme.build_qss`` so the window buttons, the wordmark
    and the close button's red hover live beside every other rule in the application. Two
    stylesheets both claiming a say over ``#WindowButton`` is how the close button ends up a
    different red from ``DANGER``.

    Kept as a function so ``shell/window.py``'s stylesheet composition is unchanged, and so a
    caller outside this workstream does not break on import.
    """
    return ""
