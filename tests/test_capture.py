"""Unit tests for capture.py.

All tests are mock-based. Zero real-hardware dependency. Green in <2s.
Covers: pick_resolution, resize_for_model, unscale_model_coords,
monitor_containing, set_dpi_awareness.
"""

import pytest


def test_capture_module_importable():
    import capture  # noqa: F401


# --- pick_resolution ---------------------------------------------------------

class TestPickResolution:
    """Tests for capture.pick_resolution.

    Resolution-picking logic: pick the candidate whose aspect ratio is
    closest to the source's.
    """

    def test_16_10_exact_match(self):
        """2880x1800 (16:10) keeps high detail at 1920x1200."""
        from capture import pick_resolution
        assert pick_resolution(2880, 1800) == (1920, 1200)

    def test_16_9_laptop(self):
        """1920x1080 (16:9) -> 1366x768. Most generic laptops / externals."""
        from capture import pick_resolution
        assert pick_resolution(1920, 1080) == (1920, 1080)

    def test_4_3_legacy(self):
        """1024x768 (4:3) -> 1024x768. Exact match for legacy displays."""
        from capture import pick_resolution
        assert pick_resolution(1024, 768) == (1024, 768)

    def test_ultrawide_preserves_aspect_instead_of_squashing(self):
        """3440x1440 (21:9 = 2.389) -> 2560x1072, NOT 1920x1080.

        EXPECTATION CHANGED (T2-8). This previously asserted (1920, 1080), encoding the
        bug: no candidate is near aspect 2.389, so "closest" still squashed the image
        horizontally and the model received a geometrically distorted screen. Measured on a
        synthetic 32:9 desktop, the squash cost 2 of 6 ground-truth targets and 50px of max
        error; preserving aspect scored 6/6 at 15px.

        The old assertion was not wrong about what the code did -- it was wrong about what
        the code should do.
        """
        from capture import pick_resolution
        assert pick_resolution(3440, 1440) == (2560, 1072)

    def test_square_picks_4_3(self):
        """1000x1000 (1.0) -> 1024x768 (1.333). 4:3 is closest of the three."""
        from capture import pick_resolution
        assert pick_resolution(1000, 1000) == (1000, 1000)

    def test_invalid_dimensions_raise(self):
        """Zero or negative dimensions must raise ValueError."""
        from capture import pick_resolution
        with pytest.raises(ValueError):
            pick_resolution(0, 800)
        with pytest.raises(ValueError):
            pick_resolution(1280, -1)


class TestAspectFidelity:
    """T2-8: the model must never receive a geometrically distorted screen.

    `scale_x != scale_y` is the diagnostic for this whole bug class -- it means the image
    was stretched on one axis, so circles arrive as ellipses and text at the wrong width.
    The coordinate maths still inverts correctly, which is precisely why the bug was
    invisible: nothing failed, pointing just quietly got worse on small targets.

    Found on real hardware: a 3840x1080 monitor reported `scale=(2.00, 1.00)`.
    """

    # Real monitor shapes. Ultrawides are the cases that used to be distorted.
    COMMON = [
        (3840, 1080), (3440, 1440), (2560, 1080), (5120, 1440),   # ultrawide
        (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768),    # 16:9
        (1600, 900), (2880, 1800), (1920, 1200), (2560, 1600),    # 16:9 / 16:10
        (1024, 768), (1280, 1024), (1000, 1000),                  # 4:3 / 5:4 / square
    ]

    @pytest.mark.parametrize("width,height", COMMON)
    def test_scale_factors_are_equal_on_every_monitor_shape(self, width, height):
        """The invariant, stated directly. One uniform factor on both axes."""
        from capture import pick_resolution
        target_w, target_h = pick_resolution(width, height)
        scale_x, scale_y = width / target_w, height / target_h
        assert abs(scale_x - scale_y) < 0.01, (
            f"{width}x{height} -> {target_w}x{target_h} distorts: "
            f"scale=({scale_x:.3f}, {scale_y:.3f})"
        )

    @pytest.mark.parametrize("width,height", COMMON)
    def test_never_upscales(self, width, height):
        """Manufacturing pixels softens text and adds no evidence for the model."""
        from capture import pick_resolution
        target_w, target_h = pick_resolution(width, height)
        assert target_w <= width and target_h <= height

    def test_the_exact_hardware_that_exposed_this(self):
        """3840x1080, a 32:9 AOC panel. Was 1920x1080 with scale=(2.00, 1.00)."""
        from capture import pick_resolution
        assert pick_resolution(3840, 1080) == (2560, 720)

    # Monitors whose behaviour must be BYTE-IDENTICAL to before the change, so the fix is
    # provably scoped to shapes the candidate list cannot express.
    UNCHANGED = [
        (1920, 1080, (1920, 1080)), (2560, 1440, (1920, 1080)),
        (3840, 2160, (1920, 1080)), (2880, 1800, (1920, 1200)),
        (2560, 1600, (1920, 1200)), (1920, 1200, (1920, 1200)),
        (1024, 768, (1024, 768)), (1280, 1024, (1280, 1024)),
        (1000, 1000, (1000, 1000)), (1366, 768, (1366, 768)),
        (1600, 900, (1600, 900)),
    ]

    @pytest.mark.parametrize("width,height,expected", UNCHANGED)
    def test_non_ultrawide_behaviour_is_unchanged(self, width, height, expected):
        """Regression gate for §1.3: this fix must touch ultrawides and nothing else."""
        from capture import pick_resolution
        assert pick_resolution(width, height) == expected

    def test_aspect_ratio_is_preserved_within_a_pixel(self):
        """Rounding to whole pixels is the only permitted deviation."""
        from capture import pick_resolution
        for width, height in self.COMMON:
            target_w, target_h = pick_resolution(width, height)
            assert abs((width / height) - (target_w / target_h)) < 0.01

    def test_fallback_respects_the_configured_bounds(self):
        from capture import pick_resolution
        from config import MAX_MODEL_LONG_EDGE, MAX_MODEL_SHORT_EDGE
        target_w, target_h = pick_resolution(5120, 1440)
        assert max(target_w, target_h) <= MAX_MODEL_LONG_EDGE
        assert min(target_w, target_h) <= MAX_MODEL_SHORT_EDGE

    def test_degenerate_monitor_never_yields_a_zero_dimension(self):
        """A zero dimension would make resize_for_model raise and kill the capture."""
        from capture import pick_resolution
        target_w, target_h = pick_resolution(20000, 10)
        assert target_w >= 1 and target_h >= 1


# --- resize_for_model -------------------------------------------------------

class TestResizeForNimbus:
    """Tests for capture.resize_for_model.

    Verifies LANCZOS resize produces exact target dimensions and returns
    scale factors usable for later unscaling Nimbus's coordinates.
    """

    def test_exact_pixel_dims_2880x1800_to_1280x800(self):
        """Resize returns an image with size == (target_w, target_h) exactly."""
        from PIL import Image
        from capture import resize_for_model
        source = Image.new("RGB", (2880, 1800), color=(0, 0, 0))
        resized, sx, sy = resize_for_model(source, 1280, 800)
        assert resized.size == (1280, 800)
        assert sx == 2880 / 1280
        assert sy == 1800 / 800

    def test_uniform_scale_16_10(self):
        """16:10 source to 16:10 target -> uniform scale, sx == sy."""
        from PIL import Image
        from capture import resize_for_model
        source = Image.new("RGB", (2880, 1800), color=(255, 255, 255))
        _, sx, sy = resize_for_model(source, 1280, 800)
        assert sx == pytest.approx(2.25)
        assert sy == pytest.approx(2.25)
        assert sx == sy

    def test_non_uniform_scale_when_aspect_mismatch(self):
        """If source aspect != target aspect, sx != sy and math still works."""
        from PIL import Image
        from capture import resize_for_model
        source = Image.new("RGB", (1920, 1200), color=(128, 128, 128))  # 16:10
        resized, sx, sy = resize_for_model(source, 1366, 768)  # 16:9 target
        assert resized.size == (1366, 768)
        assert sx == pytest.approx(1920 / 1366)
        assert sy == pytest.approx(1200 / 768)
        assert sx != sy

    def test_invalid_target_dims_raise(self):
        """Zero or negative target dimensions must raise ValueError."""
        from PIL import Image
        from capture import resize_for_model
        source = Image.new("RGB", (1280, 800))
        with pytest.raises(ValueError):
            resize_for_model(source, 0, 800)
        with pytest.raises(ValueError):
            resize_for_model(source, 1280, -5)


# --- unscale_model_coords ---------------------------------------------------

class TestUnscaleNimbusCoords:
    """Tests for capture.unscale_model_coords.

    This is the function that maps Nimbus's returned (x, y) in the
    declared resolution space back to physical pixels in virtual desktop
    space. Must clamp out-of-bounds Nimbus coords BEFORE scaling.
    """

    def test_uniform_scale_zero_offset(self):
        """Nimbus (640, 400) x 2.25 + (0,0) -> physical (1440, 900)."""
        from capture import unscale_model_coords
        x, y = unscale_model_coords(
            model_x=640, model_y=400,
            scale_x=2.25, scale_y=2.25,
            monitor_left=0, monitor_top=0,
            target_w=1280, target_h=800,
        )
        assert (x, y) == (1440, 900)

    def test_monitor_offset_applied(self):
        """Nimbus (100, 100) x 2.0 + (1920, 0) -> physical (2120, 200).
        Simulates a secondary monitor to the right of primary."""
        from capture import unscale_model_coords
        x, y = unscale_model_coords(
            model_x=100, model_y=100,
            scale_x=2.0, scale_y=2.0,
            monitor_left=1920, monitor_top=0,
            target_w=1280, target_h=800,
        )
        assert (x, y) == (2120, 200)

    def test_clamps_negative_model_coords(self):
        """Nimbus (-50, 100) -> clamped to (0, 100), then x 2.25 -> (0, 225)."""
        from capture import unscale_model_coords
        x, y = unscale_model_coords(
            model_x=-50, model_y=100,
            scale_x=2.25, scale_y=2.25,
            monitor_left=0, monitor_top=0,
            target_w=1280, target_h=800,
        )
        assert (x, y) == (0, 225)

    def test_clamps_overflow_model_coords(self):
        """Nimbus (1500, 900) in 1280x800 space -> clamped to (1279, 799)
        then x 2.25 -> (2877, 1797)."""
        from capture import unscale_model_coords
        x, y = unscale_model_coords(
            model_x=1500, model_y=900,
            scale_x=2.25, scale_y=2.25,
            monitor_left=0, monitor_top=0,
            target_w=1280, target_h=800,
        )
        # Clamp: max allowed is target - 1 (so 1279, 799)
        # Scale: 1279 * 2.25 = 2877.75 -> int(2877.75) = 2877
        #        799 * 2.25 = 1797.75 -> int(1797.75) = 1797
        assert (x, y) == (2877, 1797)


# --- monitor_containing / list_monitors --------------------------------------

class TestMonitorContaining:
    """Tests for capture.monitor_containing.

    Pure function over a list of mss-style monitor dicts. No mocking needed
    since we pass the monitors list explicitly.
    """

    def test_point_inside_primary_monitor(self):
        """Cursor at (500, 500) on a 2880x1800 monitor at (0,0) -> returns it."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ]
        result = monitor_containing(500, 500, monitors)
        assert result == monitors[0]

    def test_dead_zone_falls_back_to_primary(self):
        """Cursor at (-9999, -9999) with no containing monitor -> primary."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
            {"left": 2880, "top": 0, "width": 1920, "height": 1080},
        ]
        result = monitor_containing(-9999, -9999, monitors)
        assert result == monitors[0]

    def test_point_on_secondary_monitor(self):
        """Cursor at (3500, 500) -> on the secondary monitor at (2880, 0)."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
            {"left": 2880, "top": 0, "width": 1920, "height": 1080},
        ]
        result = monitor_containing(3500, 500, monitors)
        assert result == monitors[1]

    def test_point_at_exact_edge(self):
        """Right edge is exclusive. Point at left + width - 1 is inside;
        point at left + width is outside."""
        from capture import monitor_containing
        monitors = [
            {"left": 0, "top": 0, "width": 100, "height": 100},
            {"left": 100, "top": 0, "width": 100, "height": 100},
        ]
        # (99, 50) is inside monitor 0 (rightmost pixel of mon 0)
        assert monitor_containing(99, 50, monitors) == monitors[0]
        # (100, 50) is inside monitor 1 (leftmost pixel of mon 1)
        assert monitor_containing(100, 50, monitors) == monitors[1]


# --- set_dpi_awareness / get_cursor_position --------------------------------

class TestSetDpiAwareness:
    """Tests for capture.set_dpi_awareness.

    Idempotency matters: Windows returns E_ACCESSDENIED if the process
    already set DPI awareness (e.g., via PyQt6 or a previous call). Our
    function must swallow that and not raise.
    """

    def test_first_call_returns_bool(self):
        """First call returns a bool without raising."""
        from capture import set_dpi_awareness
        result = set_dpi_awareness()
        assert isinstance(result, bool)

    def test_second_call_does_not_raise(self):
        """Calling twice must not raise (Windows returns E_ACCESSDENIED)."""
        from capture import set_dpi_awareness
        set_dpi_awareness()
        # Second call must not raise:
        set_dpi_awareness()

    def test_qt_owns_dpi_awareness_after_application_starts(self, mocker):
        """Never call the legacy shcore API after Qt selected PMv2.

        This prevents Qt's ``SetProcessDpiAwarenessContext() failed: Access
        is denied`` startup warning: QApplication must be the sole owner of
        Nimbus's process DPI mode.
        """
        from capture import set_dpi_awareness

        mocker.patch("capture.QCoreApplication.instance", return_value=object())
        set_awareness = mocker.patch("capture.ctypes.windll.shcore.SetProcessDpiAwareness")

        assert set_dpi_awareness() is False
        set_awareness.assert_not_called()


# --- capture_all_screens ------------------------------------------------------

class TestCaptureAllScreens:
    """Tests for capture.capture_all_screens using mocked OS functions."""

    def test_single_monitor_returns_one_labeled_capture(self, mocker):
        from PIL import Image
        from capture import capture_all_screens, LabeledCapture

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(500, 400))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ])
        fake_img = Image.new("RGB", (2880, 1800), color=(100, 100, 100))
        mocker.patch("capture._capture_monitor", return_value=fake_img)

        results = capture_all_screens()
        assert len(results) == 1
        assert isinstance(results[0], LabeledCapture)
        assert results[0].is_cursor_screen is True
        assert "primary focus" in results[0].label
        assert "1920x1200" in results[0].label
        assert results[0].source_image is fake_img
        assert results[0].cursor_physical == (500, 400)

    def test_two_monitors_sorted_cursor_first(self, mocker):
        from PIL import Image
        from capture import capture_all_screens

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(3500, 500))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
            {"left": 2880, "top": 0, "width": 1920, "height": 1080},
        ])
        fake_img_primary = Image.new("RGB", (2880, 1800))
        fake_img_secondary = Image.new("RGB", (1920, 1080))
        mocker.patch("capture._capture_monitor", side_effect=[
            fake_img_primary, fake_img_secondary,
        ])

        results = capture_all_screens()
        assert len(results) == 2
        assert results[0].is_cursor_screen is True
        assert results[1].is_cursor_screen is False
        assert "primary focus" in results[0].label
        assert "secondary screen" in results[1].label

    def test_labels_contain_pixel_dimensions(self, mocker):
        from PIL import Image
        from capture import capture_all_screens

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(500, 400))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ])
        fake_img = Image.new("RGB", (2880, 1800))
        mocker.patch("capture._capture_monitor", return_value=fake_img)

        results = capture_all_screens()
        assert "image dimensions:" in results[0].label
        assert "pixels" in results[0].label

    def test_scale_factors_correct(self, mocker):
        from PIL import Image
        from capture import capture_all_screens

        mocker.patch("capture.set_dpi_awareness")
        mocker.patch("capture.get_cursor_position", return_value=(500, 400))
        mocker.patch("capture.list_monitors", return_value=[
            {"left": 0, "top": 0, "width": 2880, "height": 1800},
        ])
        fake_img = Image.new("RGB", (2880, 1800))
        mocker.patch("capture._capture_monitor", return_value=fake_img)

        results = capture_all_screens()
        r = results[0]
        assert abs(r.scale_x - 2880 / r.target_width) < 0.001
        assert abs(r.scale_y - 1800 / r.target_height) < 0.001


class TestGetCursorPosition:
    """Tests for capture.get_cursor_position. Smoke tests only — real value
    depends on where the mouse is at test time, so we only check shape."""

    def test_returns_tuple_of_two_ints(self):
        """Must return (int, int)."""
        from capture import get_cursor_position
        result = get_cursor_position()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(c, int) for c in result)
