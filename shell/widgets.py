"""Shared design-system primitives for the shell (SHELL_AND_CHAT.md §2.5, §2.7).

Every page needs cards, and Home and the nav both need the same accent bloom, so these live
here rather than being reinvented per page -- which is exactly how a dark theme ends up with
four slightly different greys.

**No value in this module is invented.** Colours, radii, durations and easing all come from
``theme``; the only bare numbers are the component dimensions §2.7 specifies by name (a 40x22
toggle track with an 18px knob, a 3px accent leading bar), and each is named and cited so it
is a design decision rather than a magic number. Spacing is always a ``theme.SPACE`` step.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPropertyAnimation, QRectF, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QCursor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import theme

# --- §2.7 component dimensions ----------------------------------------------

TOGGLE_TRACK_WIDTH = 40
TOGGLE_TRACK_HEIGHT = 22
TOGGLE_KNOB_SIZE = 18
"""The toggle recipe from §2.7, verbatim: 40x22 track, 18px knob."""

ACCENT_BAR_WIDTH = 3
"""The selected nav item's leading edge bar (§2.7)."""

GLOW_BLUR = 28
GLOW_ALPHA = 72
"""§2.5 #3's ambient bloom: blur 28, alpha 72 out of 255 -- ``theme.ACCENT_GLOW`` expressed
for ``QGraphicsDropShadowEffect``, which takes a ``QColor`` rather than a CSS string."""


def apply_accent_glow(widget: QWidget) -> None:
    """Put an ambient accent bloom behind ``widget`` (§2.5 #3).

    **Static elements only.** ``QGraphicsDropShadowEffect`` forces the widget into a
    software-rendered offscreen buffer, which is a real cost on anything that repaints per
    frame. Nav items, card borders and the power toggle qualify; an animated state strip does
    not, and should paint its bloom with a ``QRadialGradient`` in ``paintEvent`` the way
    ``overlay.py`` already does for the spinner.

    Offset is (0, 0) deliberately: this is ambient light, not a directional shadow.
    """
    glow = QGraphicsDropShadowEffect(widget)
    glow.setBlurRadius(GLOW_BLUR)
    glow.setColor(theme.qcolor(theme.ACCENT, GLOW_ALPHA))
    glow.setOffset(0, 0)
    widget.setGraphicsEffect(glow)


focus_visible_only = theme.focus_visible_only
"""Re-exported from ``theme`` so shell code keeps reaching for it here with the other widget
helpers. It lives there because ``settings_dialog`` needs it too, and ``settings_dialog`` must not
import from ``shell`` -- the shell is one of its two hosts. See ``theme.focus_visible_only`` for the
measurement and the reasoning."""


class Card(QFrame):
    """The §2.7 card: ``BG_ELEVATED``, radius 12, hairline border, top highlight, no shadow.

    All of that comes from ``QFrame#Card`` in ``theme.build_qss()``, so this class only owns
    the padding, the optional uppercase header, and a ``body`` layout children go into.

    Cards get no shadow on purpose. The top-edge highlight and the two-tone border already
    read as elevation, and shadowing every surface is how a dark theme starts looking like
    2014 Material.
    """

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            theme.SPACE[4], theme.SPACE[4], theme.SPACE[4], theme.SPACE[4])
        outer.setSpacing(theme.SPACE[1])

        self.header: QLabel | None = None
        if title:
            self.header = QLabel(title.upper())
            self.header.setObjectName("CardHeader")
            # Fixed height, and this is not cosmetic. A `QLabel` defaults to a *Preferred*
            # vertical policy, so when a card is stretched taller than its content -- the
            # Knowledge page gives its list card `stretch=1` -- `QVBoxLayout` hands the spare
            # height to whatever can grow, and a Preferred label grows and centres its text.
            # That is where the large empty band above and below "PER APPLICATION" came from:
            # the heading was not padded, it was 60px tall with 12px of text in the middle.
            self.header.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            outer.addWidget(self.header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.SPACE[1])
        # The body takes the spare height, so it lands on whichever child asked for it rather
        # than being shared out with the heading.
        outer.addLayout(self.body, 1)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """Add ``widget`` to the card body and return it, so callers can keep a reference.

        ``stretch`` lets a card name the one child that should absorb spare height -- a table,
        typically. Without it every child shares the surplus and the card looks padded apart.
        """
        self.body.addWidget(widget, stretch)
        return widget


def label(text: str, role: str = "") -> QLabel:
    """A ``QLabel`` carrying one of the QSS type roles from ``theme.build_qss()``.

    ``role`` is an object name -- ``Display``, ``Title``, ``Secondary``, ``Muted``,
    ``Mono``, ``CardHeader`` -- so font size and colour stay in the generated stylesheet
    instead of being set per widget.
    """
    result = QLabel(text)
    if role:
        result.setObjectName(role)
    result.setWordWrap(True)
    return result


def row(*widgets: QWidget, spacing: int | None = None) -> QWidget:
    """Lay widgets out horizontally in a container, using a ``theme.SPACE`` step."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.SPACE[1] if spacing is None else spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return container


class StatusDot(QLabel):
    """A small coloured dot plus caption, for the sidebar's always-visible status block.

    Reassurance that costs no click: the provider mode and the Privacy Guard are the two
    things a user wants confirmed without opening anything.
    """

    def __init__(self, text: str = "", colour: str = theme.TEXT_MUTED,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Muted")
        self._colour = colour
        self._text = text
        self._refresh()

    def set_status(self, text: str, colour: str) -> None:
        self._text, self._colour = text, colour
        self._refresh()

    @property
    def colour(self) -> str:
        """The dot's current colour, so a test can assert state without reading pixels."""
        return self._colour

    def _refresh(self) -> None:
        # A styled bullet rather than an icon: no asset to ship, and it recolours for free.
        self.setText(f"\u2022  {self._text}")
        self.setStyleSheet(f"color: {self._colour};")


TABLE_ROW_HEIGHT = 38
TABLE_HEADER_HEIGHT = 34
"""Row and header heights for the shell's tables.

**These are the only thing that reserves vertical space**, which is the lesson from getting it
wrong: CSS padding on a `QTableView::item` or a `QHeaderView::section` does not grow the row, it
shrinks the text rectangle inside it. The header was 24px tall with 16px of padding declared,
leaving 8px for 9pt uppercase text, so every column title was sliced through the middle -- and
the same arithmetic clipped the first column's cells. The stylesheet now sets horizontal padding
only and the heights live here.

38 and 34 are measured to fit, not guessed: `test_table_rows_are_tall_enough_for_their_text`
compares them against the real `QFontMetrics` height at the sizes in use."""


def style_table(table, stretch_column: int = 0) -> None:
    """Apply the shell's table treatment. One function so two tables cannot diverge.

    Fixes four things that each looked like a bug:

    1. **The white band across the top.** Qt's Windows style paints the header *widget*
       background from the palette -- which is light -- and the stylesheet only claimed
       ``QHeaderView::section``. So the strip beyond the last column, and the corner button,
       stayed near-white on a near-black card. ``theme.build_qss`` now styles ``QHeaderView``
       and ``QTableCornerButton::section`` too; this turns the corner button off entirely.
    2. **Centred header text.** Qt centres horizontal header labels by default, so every column
       title sat over the middle of left-aligned data. Left-aligned here, once.
    3. **Stale pixels showing through.** A table with a transparent viewport inside a
       transparent parent has nothing painting the gap. The viewport is given
       ``WA_OpaquePaintEvent`` off and an explicit autofill so the card gradient shows instead
       of whatever was last drawn there.
    4. **Rows too tight to read**, and long text wrapping into two-line rows of different
       heights. Fixed height, no wrap, elide at the end.
    """
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QAbstractItemView, QFrame, QHeaderView

    table.setFrameShape(QFrame.Shape.NoFrame)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setCornerButtonEnabled(False)
    table.viewport().setAutoFillBackground(False)

    # Blank the palette's Highlight role, in every colour group.
    #
    # Measured, because the stylesheet alone was not enough: Qt paints `Highlight` *underneath* a
    # stylesheet `background`, and on Windows that role is `#0078d7` when the view has focus and
    # `#f0f0f0` when it does not. A translucent orange wash over near-white is a pale peach --
    # the "light blue/white shade" on a selected row. The stylesheet now uses a pre-blended
    # opaque colour, and this makes sure there is nothing behind it either way.
    palette = table.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        palette.setColor(group, QPalette.ColorRole.Highlight,
                         theme.qcolor(theme.SELECTION_ROW))
        palette.setColor(group, QPalette.ColorRole.HighlightedText,
                         theme.qcolor(theme.TEXT_PRIMARY))
    table.setPalette(palette)

    vertical = table.verticalHeader()
    vertical.setVisible(False)
    vertical.setDefaultSectionSize(TABLE_ROW_HEIGHT)
    vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

    header = table.horizontalHeader()
    header.setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    header.setSectionsClickable(False)
    header.setFixedHeight(TABLE_HEADER_HEIGHT)
    header.setMinimumSectionSize(theme.SPACE[6])
    header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    for column in range(table.columnCount()):
        if column != stretch_column:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)


def table_item(text: str, muted: bool = False, mono: bool = False):
    """A left-aligned, non-editable cell.

    Left is not a default worth relying on: a ``QTableWidgetItem`` inherits alignment from the
    style, and the numeric columns in particular came out centred, which broke the left edge
    every other column shared.
    """
    from PyQt6.QtWidgets import QTableWidgetItem

    item = QTableWidgetItem(text)
    item.setTextAlignment(
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
    if muted:
        item.setForeground(theme.qcolor(theme.TEXT_MUTED))
    if mono:
        from PyQt6.QtGui import QFont

        item.setFont(QFont("Cascadia Mono", theme.FONT_SMALL))
    return item


class GrainedFrame(QFrame):
    """A ``QFrame`` that draws the grain tile over its own stylesheet background.

    Both permanent items in the nav rail use it -- the chat-panel switch and the Privacy Guard chip
    -- so the two look like a set rather than one textured chip beside one flat one.

    **Painted here rather than by adding a ``GrainOverlay`` child.** That class states its own rule,
    "one instance per window, never per widget", because a per-widget overlay is another widget
    sitting above the controls it covers and another composite on every repaint. Drawing the same
    tile inside ``paintEvent`` gets the texture with neither cost.

    The rail is outside the window's own grain overlay, which covers the page stack only, so without
    this the rail's components are the only surfaces in the interface with no texture on them.
    """

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        try:
            painter = QPainter(self)
            path = QPainterPath()
            # Inset by the 1px border and clipped to the radius, so the tile cannot square off the
            # rounded corners or draw over the border itself.
            path.addRoundedRect(
                QRectF(self.rect()).adjusted(1, 1, -1, -1),
                theme.RADIUS_CONTROL, theme.RADIUS_CONTROL)
            painter.setClipPath(path)
            painter.drawTiledPixmap(self.rect(), GrainOverlay.tile())
            painter.end()
        except Exception:
            # A texture is not worth a failed repaint of the nav rail.
            pass


class StatusChip(GrainedFrame):
    """A bordered pill: a state-coloured dot and a word. The sidebar's permanent reassurance.

    Replaces a bare bullet plus caption. A lone coloured dot has to be learned before it means
    anything, whereas a pill with a border reads as a *status* at a glance -- and it survives
    being the only thing in the footer, which a floating bullet did not.

    Grained and warm-cornered like the switch above it: see ``GrainedFrame``.
    """

    def __init__(self, text: str = "", colour: str = theme.TEXT_MUTED,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self._colour = colour

        row = QHBoxLayout(self)
        row.setContentsMargins(
            theme.SPACE[1], theme.SPACE[0], theme.SPACE[1], theme.SPACE[0])
        row.setSpacing(theme.SPACE[1])

        self._dot = QLabel("\u25cf", self)
        self._dot.setFixedWidth(theme.SPACE[2])
        row.addWidget(self._dot)

        self._label = QLabel(text, self)
        self._label.setObjectName("Muted")
        self._label.setWordWrap(True)
        row.addWidget(self._label, 1)

        self.set_status(text, colour)

    def set_status(self, text: str, colour: str, detail: str = "") -> None:
        """Recolour and relabel. ``detail`` becomes the tooltip, which is where the *why* goes.

        A four-word status can only ever be a label; the sentence explaining what it means
        belongs on hover rather than permanently in a 216px rail.
        """
        self._colour = colour
        self._label.setText(text)
        self._dot.setStyleSheet(f"color: {colour}; font-size: {theme.FONT_MICRO}pt;")
        if detail:
            self.setToolTip(detail)

    @property
    def colour(self) -> str:
        """The chip's current state colour, so a test can assert state without reading pixels."""
        return self._colour

    @property
    def text(self) -> str:
        return self._label.text()


class SidebarSwitch(GrainedFrame):
    """A labelled switch sized for the nav rail: caption and sliding toggle.

    Built for the chat-panel control, which started life as a bare ``QCheckBox`` tucked under the
    hotkey line inside Home's push-to-talk card. Two problems with that: it was a *setting* hidden
    inside the card that answers "is Nimbus listening", so it read as part of push-to-talk; and a
    system checkbox is the one control in this interface that looks like it came from a different
    decade.

    Here it is a chip in the rail, directly above the Privacy Guard chip, so the two things that are
    permanently true about the session -- what is being captured, and whether the transcript shows
    itself -- sit together and neither costs a page visit.

    Whole-chip click target, not just the toggle. A 40x22 switch at the bottom of a 216px rail is a
    small thing to aim at, and the caption beside it is dead space otherwise. The chip carries a lit
    top-left corner and the grain tile in both states, so it reads as a surface rather than as a box
    drawn on the rail, and takes more of the accent when on.

    **One line of text, no sub-caption.** It briefly had a second line describing the state, which at
    216px minus the toggle left about 130px for it -- "Hidden · Ctrl+Alt+H to show" was elided
    mid-word. A caption that cannot finish its sentence is worse than no caption, and the state is
    already legible from the knob and the chip's colour. The shortcut lives in the tooltip.

    **Holds no authoritative state**, exactly like ``PowerToggle``: ``set_on`` reflects what the
    owner pushed in and does not emit, and ``toggled`` fires only for a real user action.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarSwitch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(
            theme.SPACE[1], theme.SPACE[1], theme.SPACE[1], theme.SPACE[1])
        row.setSpacing(theme.SPACE[1])

        self._label = QLabel(text, self)
        self._label.setObjectName("SidebarSwitchLabel")
        row.addWidget(self._label, 1)

        self.toggle = PowerToggle(self)
        # The chip is the click target; the knob must not also handle the press or a click on it
        # would toggle twice.
        self.toggle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.toggle)

        self._on = False
        self._refresh()

    def is_on(self) -> bool:
        """The switch's view of the state. The owner holds the truth."""
        return self._on

    def set_on(self, on: bool) -> None:
        """Reflect externally-owned state. Deliberately silent -- see ``PowerToggle.set_on``."""
        self._on = bool(on)
        self.toggle.set_on(self._on)
        self._refresh()

    def mouseReleaseEvent(self, event) -> None:
        """Toggle on release inside the chip, which is how a button behaves.

        On release rather than press so dragging off the chip cancels, and only for the left
        button so a right-click can still reach a context menu later.
        """
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.set_on(not self._on)
            self.toggled.emit(self._on)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _refresh(self) -> None:
        self.setProperty("on", "true" if self._on else "false")
        # Qt does not re-evaluate a property selector until the style is re-polished.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class PowerToggle(QAbstractButton):
    """The §2.7 toggle: 40x22 track, 18px knob, sliding over ``DUR_STANDARD``.

    Painted rather than styled because a ``QCheckBox`` indicator cannot animate its knob, and
    the sliding knob is the whole point -- it is the one control on Home that answers "is
    Nimbus on?".

    **Holds no authoritative state.** ``isChecked()`` is a *view* of whatever the owner last
    pushed in with ``set_on``, and the owner reads the truth from ``hotkey.enabled``. That is
    why ``set_on`` does not emit: the window, the tray item and the tray icon must never be
    able to disagree, and they cannot if none of them keeps its own copy (`S-3`).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(TOGGLE_TRACK_WIDTH, TOGGLE_TRACK_HEIGHT)
        self._knob = 0.0
        self._animation = QPropertyAnimation(self, b"knob", self)
        self._animation.setEasingCurve(theme.easing(theme.EASE_STANDARD))
        self.toggled.connect(self._animate_to_state)

    def set_on(self, on: bool) -> None:
        """Reflect externally-owned state without emitting ``toggled``.

        Emitting here would loop: the owner sets the view from the source of truth, the view
        tells the owner it changed, and the owner writes back.
        """
        blocked = self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(blocked)
        self._animate_to_state(bool(on))

    def _animate_to_state(self, on: bool) -> None:
        self._animation.stop()
        self._animation.setDuration(theme.duration(theme.DUR_STANDARD))
        self._animation.setStartValue(self._knob)
        self._animation.setEndValue(1.0 if on else 0.0)
        self._animation.start()

    def get_knob(self) -> float:
        return self._knob

    def set_knob(self, value: float) -> None:
        self._knob = max(0.0, min(1.0, float(value)))
        self.update()

    knob = pyqtProperty(float, fget=get_knob, fset=set_knob)
    """0.0 = off (knob left), 1.0 = on (knob right). Animated, not set directly."""

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        track_colour = theme.ACCENT if self.isChecked() else theme.BG_ACTIVE
        painter.setBrush(QBrush(theme.qcolor(track_colour)))
        radius = TOGGLE_TRACK_HEIGHT / 2
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)

        inset = (TOGGLE_TRACK_HEIGHT - TOGGLE_KNOB_SIZE) / 2
        travel = self.width() - TOGGLE_KNOB_SIZE - (inset * 2)
        painter.setBrush(QBrush(theme.qcolor(
            theme.ON_ACCENT if self.isChecked() else theme.TEXT_SECONDARY)))
        painter.drawEllipse(QRectF(
            inset + travel * self._knob, inset, TOGGLE_KNOB_SIZE, TOGGLE_KNOB_SIZE))
        painter.end()


POWER_HEIGHT = 52
POWER_MIN_WIDTH = 240
POWER_KNOB = 34
"""The hero power control. Sized to be the largest interactive thing on Home, because it
answers the page's first question and was previously a 40x22 switch that reviewers could not
find at all."""

RIPPLE_DURATION = theme.DUR_STANDARD


class PowerSwitch(PowerToggle):
    """The full-width push-to-talk switch: a labelled track, a sliding knob, a click ripple.

    ## Why this exists rather than the 40x22 toggle

    The small toggle was correct by the §2.7 recipe and wrong on the page. Home's first job is
    to answer "is Nimbus on?", and a 40px switch in the corner of a card answered it so quietly
    that the honest reviewer feedback was *"I don't see the turn on Nimbus button"*. A control
    that has to be hunted for has failed regardless of how well it is drawn.

    So this one carries its own state in words -- ``LISTENING`` / ``PAUSED`` -- on the track
    itself, is the width of the card, and fills with the metallic accent when on. There is no
    ambiguity left about what it is or what state it is in.

    **It still holds no authoritative state.** Inherited from ``PowerToggle`` precisely to keep
    that: ``set_on`` does not emit, the owner re-reads ``hotkey.enabled``, and `S-3`'s three
    views cannot drift. This class changes the drawing, not the contract.

    The ripple is the one genuine click animation in the shell. It is painted in ``paintEvent``
    from an animated property rather than done with a ``QGraphicsEffect``, because effects force
    the widget through a software offscreen buffer -- and, as the page-transition fade showed,
    can leak stale pixels.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(POWER_HEIGHT)
        self.setMinimumWidth(POWER_MIN_WIDTH)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        self._ripple = 0.0
        self._ripple_at = QPoint(0, 0)
        self._ripple_animation = QPropertyAnimation(self, b"ripple", self)
        self._ripple_animation.setEasingCurve(theme.easing(theme.EASE_OUT))
        self.pressed.connect(self._start_ripple)

    # -- ripple ---------------------------------------------------------------

    def get_ripple(self) -> float:
        return self._ripple

    def set_ripple(self, value: float) -> None:
        self._ripple = max(0.0, min(1.0, float(value)))
        self.update()

    ripple = pyqtProperty(float, fget=get_ripple, fset=set_ripple)
    """0.0 = no ripple, 1.0 = fully expanded and faded out."""

    def _start_ripple(self) -> None:
        self._ripple_at = self.mapFromGlobal(QCursor.pos())
        self._ripple_animation.stop()
        self._ripple_animation.setDuration(theme.duration(RIPPLE_DURATION))
        self._ripple_animation.setStartValue(0.0)
        self._ripple_animation.setEndValue(1.0)
        self._ripple_animation.start()

    # -- painting -------------------------------------------------------------

    @staticmethod
    def _draw_power_mark(painter: QPainter, box: QRectF, colour: str) -> None:
        """The IEC power symbol: a broken ring with a stem through the gap.

        Drawn rather than typed. The gap is at the top and the stem rises through it, which is
        what makes it the power symbol rather than a circle with a line in it -- and getting that
        right is why this is 6 lines of geometry instead of one `drawText`.
        """
        from PyQt6.QtGui import QPen

        # 44% of the knob, so the mark has a clear margin inside it at any size.
        size = box.width() * 0.44
        ring = QRectF(0, 0, size, size)
        ring.moveCenter(box.center())
        stroke = max(1.6, size * 0.16)

        pen = QPen(theme.qcolor(colour))
        pen.setWidthF(stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 290 degrees, centred on the bottom: leaves a 70-degree gap at the top for the stem.
        painter.drawArc(ring, int(-55 * 16), int(290 * 16))
        stem_top = ring.top() - size * 0.22
        painter.drawLine(
            int(ring.center().x()), int(stem_top),
            int(ring.center().x()), int(ring.top() + size * 0.30))
        painter.setPen(Qt.PenStyle.NoPen)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        on = self.isChecked()
        radius = self.height() / 2
        track = QRectF(0, 0, self.width(), self.height())

        if on:
            # The metallic accent, built from the same stops as the stylesheet's gradient so a
            # painted control and a styled one cannot end up different oranges.
            fill = QLinearGradient(0, 0, 0, self.height())
            fill.setColorAt(0.0, theme.qcolor(theme.ACCENT_LIGHT))
            fill.setColorAt(0.45, theme.qcolor(theme.ACCENT))
            fill.setColorAt(1.0, theme.qcolor(theme.ACCENT_DEEP))
        else:
            fill = QLinearGradient(0, 0, 0, self.height())
            fill.setColorAt(0.0, theme.qcolor(theme.CONTROL_TOP))
            fill.setColorAt(1.0, theme.qcolor(theme.CONTROL_BOTTOM))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(track, radius, radius)

        # Border, and a lit top edge. Same trick as HIGHLIGHT_TOP in the stylesheet.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(theme.qcolor(theme.ACCENT_DEEP if on else theme.BORDER_STRONG))
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)

        if self._ripple > 0.0:
            # Expands past the control's own width so the wave leaves the edge rather than
            # stopping dead, and fades as it goes.
            reach = self.width() * 1.15 * self._ripple
            colour = theme.qcolor(
                theme.ON_ACCENT if on else theme.ACCENT,
                int(60 * (1.0 - self._ripple)))
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(QRectF(
                self._ripple_at.x() - reach / 2, self._ripple_at.y() - reach / 2,
                reach, reach))

        inset = (self.height() - POWER_KNOB) / 2
        travel = self.width() - POWER_KNOB - (inset * 2)
        knob_x = inset + travel * self._knob
        painter.setBrush(QBrush(theme.qcolor(
            theme.ON_ACCENT if on else theme.TEXT_SECONDARY)))
        painter.drawEllipse(QRectF(knob_x, inset, POWER_KNOB, POWER_KNOB))

        # The power mark rides on the knob, so the thing that moves is the thing that means
        # "on". **Drawn, not a font glyph.** U+23FB comes from Segoe UI Symbol, which renders it
        # at a different weight and baseline from the surrounding text and looked crude and
        # off-centre at 34px -- a hinted symbol font at an arbitrary size is not something you
        # can control. An arc plus a line is four lines of QPainter, is perfectly centred by
        # construction, and scales with the knob.
        self._draw_power_mark(
            painter, QRectF(knob_x, inset, POWER_KNOB, POWER_KNOB),
            theme.ACCENT if on else theme.TEXT_MUTED)

        # State in words, on the empty side of the track: right of the knob when off, left when
        # on. It follows the knob so the label is never underneath it.
        label_font = painter.font()
        label_font.setPointSize(theme.FONT_BODY)
        label_font.setWeight(theme.WEIGHT_SEMIBOLD)
        painter.setFont(label_font)
        painter.setPen(theme.qcolor(theme.ON_ACCENT if on else theme.TEXT_SECONDARY))
        text_rect = (
            QRectF(inset, 0, knob_x - inset - theme.SPACE[1], self.height()) if on
            else QRectF(knob_x + POWER_KNOB + theme.SPACE[1], 0,
                        self.width() - knob_x - POWER_KNOB - inset - theme.SPACE[1],
                        self.height())
        )
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignRight if on else Qt.AlignmentFlag.AlignLeft)),
            "LISTENING" if on else "PAUSED")
        painter.end()


class GrainOverlay(QWidget):
    """The §2.5 #5 noise texture, drawn **once** as a window-level overlay.

    Large low-contrast gradients on dark backgrounds band -- visible stepped stripes, worse
    after Windows' colour management. A ~4% noise tile destroys that completely.

    **Two things make this safe, and both were verified rather than assumed:**

    1. ``WA_TransparentForMouseEvents``. Without it this widget sits above every control and
       eats their clicks. Measured on this machine with ``QWidget.childAt``: without the flag
       a click at a button's centre resolves to the grain widget; with it, to the
       ``QPushButton``. It is the difference between a texture and a broken window.
    2. One instance per window, never per widget. Per-widget grain tiles inconsistently
       across boundaries and costs a composite on every repaint.

    The pixmap is generated once per process and shared: ``theme.grain_pixmap`` walks
    128x128 pixels, which is cheap but not free, and the tile is deterministic so there is
    nothing to gain from regenerating it.
    """

    _pixmap: QPixmap | None = None

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @classmethod
    def tile(cls) -> QPixmap:
        if cls._pixmap is None:
            cls._pixmap = theme.grain_pixmap()
        return cls._pixmap

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawTiledPixmap(self.rect(), self.tile())
        painter.end()


class ClickableLabel(QLabel):
    """A label that emits ``clicked`` -- for the "open folder" affordances on paths.

    A ``QPushButton`` styled to look like a path reads as a button and invites a hunt for its
    edges; a monospaced path that responds to a click is what users expect.
    """

    clicked = pyqtSignal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Mono")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                QPoint(int(event.position().x()), int(event.position().y()))):
            self.clicked.emit()
        super().mouseReleaseEvent(event)
