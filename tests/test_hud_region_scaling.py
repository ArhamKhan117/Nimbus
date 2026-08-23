"""The chat panel's clipping region must be built in physical pixels, not logical ones.

Reported as three separate faults: the panel showed only partially when first opened, it could be
resized from the left edge and the top-left corner but no others, and text looked cut off rather than
wrapped. One cause. ``SetWindowRgn`` works in physical device pixels while Qt reports widget sizes in
logical ones, and the region was built from the logical numbers.

At 100% scale those are the same number and nothing is wrong, which is why it survived. At 125% a
660x430 panel occupies 825x538 physical pixels, so the region covered 80% of the window. Measured with
``GetWindowRgnBox`` against ``GetWindowRect``: region 660x430, window 825x538.

A window region clips **mouse input as well as painting**, which is what made one bug look like three.
The missing edges were not missing, they were outside the region and received no events.
"""
from __future__ import annotations

import pytest


class FakeGdi32:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int, int, int, int]] = []

    def CreateRoundRectRgn(self, left, top, right, bottom, ellipse_w, ellipse_h):
        self.calls.append((left, top, right, bottom, ellipse_w, ellipse_h))
        return 12345  # any non-zero handle


class FakeUser32:
    def __init__(self) -> None:
        self.regions: list[tuple[int, int, bool]] = []

    def SetWindowRgn(self, hwnd, region, redraw):
        self.regions.append((hwnd, region, bool(redraw)))
        return 1


@pytest.fixture
def fake_windll(monkeypatch):
    import chat_hud

    gdi32, user32 = FakeGdi32(), FakeUser32()
    windll = type("WinDll", (), {"gdi32": gdi32, "user32": user32})()
    monkeypatch.setattr(chat_hud.ctypes, "windll", windll, raising=False)
    return gdi32, user32


class TestTheRegionSize:
    def test_a_scale_of_one_is_unchanged(self, fake_windll):
        """The 100% case, which is why the bug went unnoticed for so long."""
        import chat_hud

        gdi32, _ = fake_windll
        assert chat_hud.apply_rounded_region(1, 660, 430, radius=10, scale=1.0)

        _, _, right, bottom, _, _ = gdi32.calls[-1]
        # +1 because CreateRoundRectRgn's bottom-right is exclusive.
        assert (right, bottom) == (661, 431)

    def test_a_scaled_display_gets_physical_pixels(self, fake_windll):
        """The reported case. 125% of 660x430 is 825x538, and the region must cover all of it."""
        import chat_hud

        gdi32, _ = fake_windll
        assert chat_hud.apply_rounded_region(1, 660, 430, radius=10, scale=1.25)

        _, _, right, bottom, _, _ = gdi32.calls[-1]
        assert (right, bottom) == (826, 539), (
            "a region smaller than the window clips painting and swallows mouse input")

    def test_the_corner_radius_scales_too(self, fake_windll):
        """Otherwise the rounding shrinks visually as the display scale rises."""
        import chat_hud

        gdi32, _ = fake_windll
        chat_hud.apply_rounded_region(1, 660, 430, radius=12, scale=2.0)

        *_, ellipse_w, ellipse_h = gdi32.calls[-1]
        assert (ellipse_w, ellipse_h) == (48, 48)

    def test_a_scale_below_one_is_ignored(self, fake_windll):
        """A ratio under 1 would shrink the region, which is the bug in the other direction."""
        import chat_hud

        gdi32, _ = fake_windll
        chat_hud.apply_rounded_region(1, 660, 430, radius=10, scale=0.5)

        _, _, right, bottom, _, _ = gdi32.calls[-1]
        assert (right, bottom) == (661, 431)

    def test_a_missing_scale_still_works(self, fake_windll):
        """Callers that predate the parameter must keep behaving as they did."""
        import chat_hud

        gdi32, _ = fake_windll
        chat_hud.apply_rounded_region(1, 500, 400, radius=10)

        _, _, right, bottom, _, _ = gdi32.calls[-1]
        assert (right, bottom) == (501, 401)

    def test_a_failed_region_is_reported_rather_than_raised(self, monkeypatch):
        """A panel with square corners beats a panel that will not open."""
        import chat_hud

        class Failing(FakeGdi32):
            def CreateRoundRectRgn(self, *args):
                return 0

        windll = type("WinDll", (), {"gdi32": Failing(), "user32": FakeUser32()})()
        monkeypatch.setattr(chat_hud.ctypes, "windll", windll, raising=False)

        assert chat_hud.apply_rounded_region(1, 660, 430) is False


class TestTheHudPassesItsOwnScale:
    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication

        yield QApplication.instance() or QApplication([])

    def test_the_panel_asks_for_its_device_pixel_ratio(self, qt_app, monkeypatch):
        """Read per call rather than cached: a window dragged to a monitor with a different
        scale gets a new ratio without a resize event of its own."""
        import chat_hud

        seen = {}

        def record(hwnd, width, height, radius=None, scale=1.0):
            seen.update(width=width, height=height, scale=scale)
            return True

        monkeypatch.setattr(chat_hud, "apply_rounded_region", record)
        hud = chat_hud.ChatHud()
        try:
            hud._apply_region()
        finally:
            hud.deleteLater()

        assert seen["width"] == hud.width()
        assert seen["height"] == hud.height()
        assert seen["scale"] == pytest.approx(hud.devicePixelRatioF())
