"""Tests for brand.py — the shared logo loader.

The interesting assertions here are about the **source artwork**, not the code. The mark is a
1536x1024 canvas with the logo occupying 557x469 in the middle of it, so anything that scales the
file directly produces a logo about a third of the size it asked for, floating in its own
padding. Pinning the crop is what makes a re-exported asset with different padding fail here
rather than silently shrinking the logo on every surface at once.
"""

import pytest


@pytest.fixture(scope="module")
def qt_app():
    """Qt must exist before any QPixmap. Module-scoped, matching the house pattern."""
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class TestAssets:
    def test_the_referenced_assets_exist(self):
        """Both are user-supplied artwork, so the filenames are the fragile part."""
        import brand

        assert brand.asset_path(brand.MARK).is_file()
        assert brand.asset_path(brand.CURSOR).is_file()

    def test_the_mark_has_an_alpha_channel(self, qt_app):
        """Without one there is nothing to trim, and the logo would carry a black box."""
        import brand

        image = brand.trimmed_pixmap(brand.MARK, 64).toImage()
        assert image.hasAlphaChannel()


class TestTrimming:
    def test_the_source_artwork_really_is_mostly_padding(self, qt_app):
        """The measurement this module exists for.

        If this ever fails because the asset was re-exported tight, the trim becomes a no-op and
        everything still works -- but the numbers in ``brand.py``'s docstring would be lying.
        """
        import brand
        from PyQt6.QtGui import QPixmap

        source = QPixmap(str(brand.asset_path(brand.MARK)))
        rect = brand._content_rect(source.toImage())

        assert (source.width(), source.height()) == (1536, 1024)
        assert rect is not None, "the artwork is already tight; update brand.py's docstring"
        _x, _y, width, height = rect
        assert width < source.width() * 0.6
        assert height < source.height() * 0.6

    @pytest.mark.parametrize("height", [16, 22, 32, 256])
    def test_the_result_is_exactly_the_height_asked_for(self, qt_app, height):
        """The whole point: a 22px request must produce 22px of logo, not 9px of logo in a
        22px box."""
        import brand

        pixmap = brand.trimmed_pixmap(brand.MARK, height)

        assert not pixmap.isNull()
        assert pixmap.height() == height
        assert pixmap.width() > 0

    def test_aspect_ratio_is_preserved(self, qt_app):
        import brand

        small = brand.trimmed_pixmap(brand.MARK, 20)
        large = brand.trimmed_pixmap(brand.MARK, 200)

        assert abs((small.width() / small.height()) - (large.width() / large.height())) < 0.05

    def test_the_trimmed_mark_reaches_its_own_edges(self, qt_app):
        """A correctly trimmed logo has opaque pixels touching at least one edge of each axis.
        Padding left in would leave every edge transparent."""
        import brand

        image = brand.trimmed_pixmap(brand.MARK, 64).toImage()
        width, height = image.width(), image.height()

        def opaque_in(points):
            return any(image.pixelColor(x, y).alpha() > 8 for x, y in points)

        assert opaque_in([(x, 0) for x in range(width)]) or opaque_in(
            [(x, height - 1) for x in range(width)]), "vertical padding survived the trim"
        assert opaque_in([(0, y) for y in range(height)]) or opaque_in(
            [(width - 1, y) for y in range(height)]), "horizontal padding survived the trim"


class TestCaching:
    def test_the_alpha_scan_happens_once_per_asset_not_once_per_size(self, qt_app):
        """Measured: the scan costs ~135ms, and the first version repeated it for every
        requested height -- 323ms on the startup path before the window appeared."""
        import time

        import brand

        brand._cache.clear()
        brand._rect_cache.clear()

        started = time.perf_counter()
        brand.trimmed_pixmap(brand.MARK, 22)
        first = time.perf_counter() - started

        started = time.perf_counter()
        for height in (16, 24, 32, 48, 64, 128):
            brand.trimmed_pixmap(brand.MARK, height)
        rest = time.perf_counter() - started

        assert brand.MARK in brand._rect_cache
        assert rest < first, "six more sizes must cost less than the one that did the scan"

    def test_the_same_request_returns_the_same_object(self, qt_app):
        import brand

        assert brand.trimmed_pixmap(brand.MARK, 22) is brand.trimmed_pixmap(brand.MARK, 22)


class TestFailureIsCosmetic:
    def test_a_missing_asset_costs_a_logo_not_a_window(self, qt_app):
        """Same reasoning as ``SettingsDialog``'s icon load, which has always been wrapped."""
        import brand

        brand._cache.clear()
        pixmap = brand.trimmed_pixmap("no-such-file.png", 22)

        assert pixmap.isNull()

    def test_the_label_still_has_a_size_with_no_artwork(self, qt_app, mocker):
        """A zero-sized label would collapse the title bar's spacing and shift the wordmark."""
        from PyQt6.QtGui import QPixmap

        import brand

        mocker.patch.object(brand, "trimmed_pixmap", return_value=QPixmap())
        label = brand.mark_label(22)

        assert label.width() == 22
        assert label.height() == 22


class TestTheLabel:
    def test_the_asset_is_selectable(self, qt_app):
        """The chat panel uses the pointer, the shell uses the abstract mark."""
        import brand

        pointer = brand.mark_label(16, asset=brand.CURSOR)
        mark = brand.mark_label(16, asset=brand.MARK)

        assert pointer.height() == mark.height() == 16
        assert pointer.pixmap().width() != mark.pixmap().width(), (
            "two different assets should not produce identical geometry")

    def test_the_mark_does_not_eat_clicks(self, qt_app):
        """On both surfaces it sits inside a drag handle. A label that consumed mouse events
        would put a dead spot in the middle of the title bar -- the bug that made the chat
        panel undraggable."""
        from PyQt6.QtCore import Qt

        import brand

        label = brand.mark_label(22)
        assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def test_the_label_is_sized_to_the_artwork(self, qt_app):
        import brand

        label = brand.mark_label(22)
        assert label.height() == 22
        assert label.width() == label.pixmap().width()


class TestWindowIcon:
    def test_the_window_icon_comes_from_the_orange_mark(self, qt_app):
        """Not the old blue ``nimbus_tray.ico``. That file stays in ``nimbus.spec`` as the
        *executable* resource, because Windows reads the taskbar icon from the PE header and it
        has to be a real multi-resolution .ico."""
        import brand

        icon = brand.window_icon()

        assert not icon.isNull()
        assert icon.availableSizes(), "an icon with no sizes renders as nothing"

    def test_the_icon_canvas_is_at_least_as_tall_as_it_is_wide(self, qt_app):
        """Narrowed from "is exactly square", which the growable nudge made false.

        Squaring was never the goal, it was the *mechanism*: the trimmed mark is wider than it is tall,
        so a square canvas is the cheapest way to get vertical slack to move within. The invariant that
        actually matters is that the canvas is never wider than tall -- if it were, Qt would fit it by
        width, centre it vertically, and the downward nudge would vanish.
        """
        import brand

        size = brand.window_icon().availableSizes()[0]

        assert size.height() >= size.width()
        # And not runaway tall, which would shrink the mark to a dot in the taskbar.
        assert size.height() <= size.width() * 1.5

    def test_the_mark_sits_below_centre_and_is_never_clipped(self, qt_app):
        """The point of the nudge, measured on the alpha channel rather than assumed.

        The bottom edge is *allowed* to be flush now, and that changed deliberately: the mark reaching
        the bottom of its canvas is what "as low as it goes" means, and the earlier version asserted a
        transparent bottom row, which capped the nudge at a value that could not be exceeded. What must
        still hold is that nothing is cut off -- the artwork is entirely inside the canvas.
        """
        import brand

        image = brand.window_icon().pixmap(256, 256).toImage()
        rect = brand._content_rect(image)
        assert rect is not None, "a fully transparent icon means the mark was not drawn"

        _x, top, _width, height = rect

        assert top + height / 2 > image.height() / 2, "the mark must sit below the canvas centre"
        assert top >= 0 and top + height <= image.height(), "the mark must not be clipped"

    def test_a_bigger_nudge_actually_moves_the_mark(self, qt_app):
        """The guard that would have caught the first attempt.

        That version clamped the offset to the square canvas's slack, so every value above ~0.06
        rendered *identically* -- asking for lower did nothing. Two nudges must give two positions.
        """
        import brand

        def top_gap(nudge: float) -> int:
            image = brand.window_icon(nudge=nudge).pixmap(64, 64).toImage()
            return brand._content_rect(image)[1]

        assert top_gap(0.20) > top_gap(0.06) > top_gap(0.0)

    def test_a_zero_nudge_leaves_the_mark_centred(self, qt_app):
        """Guards the arithmetic: with no nudge the mark is centred, so a failure of the test above
        is the offset being wrong rather than the whole layout being off."""
        import brand

        image = brand.window_icon(nudge=0.0).pixmap(256, 256).toImage()
        _x, top, _width, height = brand._content_rect(image)

        assert abs((top + height / 2) - image.height() / 2) <= 2
