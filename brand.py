"""Brand artwork: one loader for the Nimbus mark, shared by every surface that shows it.

## Why this is a module and not four calls to ``QPixmap``

The mark appears in the window's title bar, the chat panel's header, the window icon, the
Settings dialog and the welcome dialog. Loading it per site meant five different sizes, five
different bits of scaling code, and -- because the source artwork is a 1536x1024 canvas with the
mark floating in the middle of it -- five different amounts of accidental padding. A logo that is
28px on one surface and 20px on another, each with its own invisible margin, is the sort of thing
nobody can point at but everybody notices.

## Trimming is the whole point

``assets/Nimbus tranparent .png`` is 1536x1024 with the artwork occupying 557x469 of it. Scaling
that file to a 24px box gives a mark about 9px tall in a 24px space. So every load goes through
``trimmed_pixmap``, which crops to the alpha bounding box first. Measured, not assumed -- the
bounding boxes are asserted in ``tests/test_brand.py`` so a re-exported asset with different
padding fails there rather than silently shrinking the logo.

Results are cached per (asset, height) because these are read on every window construction and
the crop walks the alpha channel.
"""
from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"

MARK = "Nimbus tranparent .png"
"""The orange wordmark-free mark. Filename typo and all -- renaming a user-supplied asset is a
change that has to be made in two places at once, and the string is only written here."""

CURSOR = "cursor.png"
"""The orange pointer.

Two consumers, for different reasons. ``overlay.py`` draws a *traced vector* of it, because the
on-screen pointer scales and pulses every frame and a bitmap would soften; the chat panel shows
the bitmap itself as its header mark, because that panel *is* the conversation with the thing the
pointer represents, and a pointer reads as "Nimbus is here" more directly than an abstract mark.
"""

TITLEBAR_MARK_HEIGHT = 22
"""Mark height in the shell's 48px title bar. Optically matched to the 15pt wordmark beside it:
cap height, not line height, so the two read as one lockup rather than a logo and some text."""

HUD_MARK_HEIGHT = 16
"""Mark height in the chat panel's 34px header."""

_cache: dict[tuple[str, int], object] = {}
_rect_cache: dict[str, tuple[int, int, int, int] | None] = {}
"""The alpha scan is cached **per asset**, not per size.

Measured: scanning ``Nimbus tranparent .png`` costs ~100ms, and the first version recomputed it
for every requested height -- 323ms to prepare three sizes, all of it on the startup path before
the window appears. The crop is a property of the artwork, so it is computed once."""


def asset_path(name: str) -> Path:
    """Absolute path to a bundled asset. Resolved from ``__file__`` so it works frozen."""
    return ASSETS / name


def trimmed_pixmap(name: str, height: int):
    """The asset cropped to its artwork and scaled to ``height``, or a null pixmap.

    Never raises. A missing or unreadable asset costs the caller a logo, not a window -- the
    same reasoning as ``SettingsDialog``'s icon load, which has always been wrapped.
    """
    from PyQt6.QtGui import QPixmap

    key = (name, int(height))
    if key in _cache:
        return _cache[key]

    result = QPixmap()
    try:
        source = QPixmap(str(asset_path(name)))
        if not source.isNull():
            if name not in _rect_cache:
                # `QPixmap` has no alpha bbox, so the crop goes via QImage.
                _rect_cache[name] = _content_rect(source.toImage())
            rect = _rect_cache[name]
            if rect is not None:
                source = source.copy(*rect)
            result = source.scaledToHeight(
                int(height),
                _smooth_transformation(),
            )
    except Exception:
        result = QPixmap()

    _cache[key] = result
    return result


def _smooth_transformation():
    from PyQt6.QtCore import Qt

    return Qt.TransformationMode.SmoothTransformation


def _content_rect(image) -> tuple[int, int, int, int] | None:
    """``(x, y, w, h)`` of the non-transparent artwork, or ``None`` if it fills the canvas.

    Walks rows and columns rather than every pixel: a 1536x1024 source is 1.5M pixels and this
    runs at window construction. Scanning edges inward touches a few thousand.
    """
    if image.isNull():
        return None
    width, height = image.width(), image.height()
    alpha_threshold = 8

    def row_has_content(y: int) -> bool:
        return any(image.pixelColor(x, y).alpha() > alpha_threshold
                   for x in range(0, width, max(1, width // 96)))

    def column_has_content(x: int) -> bool:
        return any(image.pixelColor(x, y).alpha() > alpha_threshold
                   for y in range(0, height, max(1, height // 96)))

    top = next((y for y in range(height) if row_has_content(y)), None)
    if top is None:
        return None  # fully transparent, or opaque with no alpha channel
    bottom = next(y for y in range(height - 1, -1, -1) if row_has_content(y))
    left = next(x for x in range(width) if column_has_content(x))
    right = next(x for x in range(width - 1, -1, -1) if column_has_content(x))

    if (left, top, right, bottom) == (0, 0, width - 1, height - 1):
        return None  # already tight; skip the copy
    return (left, top, right - left + 1, bottom - top + 1)


def mark_label(height: int = TITLEBAR_MARK_HEIGHT, parent=None, asset: str = MARK):
    """A ``QLabel`` holding ``asset`` at ``height``, sized to it and transparent to the mouse.

    Mouse-transparent because on both surfaces the mark sits in a **drag handle**. A label that
    ate clicks would put a dead spot in the middle of the title bar, which is the bug that made
    the chat panel undraggable before.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel

    label = QLabel(parent)
    pixmap = trimmed_pixmap(asset, height)
    label.setPixmap(pixmap)
    label.setFixedSize(pixmap.width() or height, pixmap.height() or height)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    label.setScaledContents(False)
    return label


ICON_NUDGE = 0.16
"""How far down its canvas the mark sits, as a fraction of the canvas side.

Beyond about 0.08 this stops being free: the square canvas runs out of slack, the canvas grows above
the mark, and Windows renders the same artwork smaller to fit the icon slot. See ``window_icon`` for
the measurements. 0.16 buys a clearly lower mark for roughly a tenth of its height."""


def window_icon(nudge: float = ICON_NUDGE):
    """The application/window icon, from the orange mark rather than the old blue ``.ico``.

    The ``.ico`` stays in ``nimbus.spec`` as the *executable* resource, because Windows reads
    that from the PE header for the taskbar and Alt-Tab and it must be a real multi-resolution
    icon file. This is the in-process icon for windows and dialogs.

    ## How the nudge works, and what it costs

    The mark cannot move inside its own bounding box: there is no slack there, so any offset clips.
    Squaring the canvas creates some, because the trimmed mark is wider than it is tall (557x469 in
    the source, 304x256 once scaled) — 48px of it, which the first version used up entirely.

    Measured at the size that matters, a 32px taskbar icon: at ``nudge`` 0.06 the artwork already sits
    flush with the bottom of a square canvas (``bottom_gap=0``), so raising the value did nothing at
    all. Everything above 0.06 rendered identically.

    Going lower therefore requires **growing the canvas above the mark**, and that has a real cost:
    Windows fits an icon to its slot, so a taller canvas renders the same artwork smaller. This is the
    trade-off, stated rather than hidden — the mark loses roughly a tenth of its height in exchange for
    sitting visibly lower. There is no arrangement that gives both.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon, QPainter, QPixmap

    mark = trimmed_pixmap(MARK, 256)
    if mark.isNull():
        return QIcon()

    side = max(mark.width(), mark.height())
    x = (side - mark.width()) // 2
    slack = side - mark.height()
    y = (slack // 2) + int(round(side * nudge))

    # Grow rather than clamp. Clamping is what made every value above 0.06 identical.
    canvas = QPixmap(side, max(side, y + mark.height()))
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    try:
        painter.drawPixmap(x, y, mark)
    finally:
        # `QPainter` must be ended before the pixmap is used, or Qt warns and the paint may be lost.
        painter.end()

    return QIcon(canvas)
