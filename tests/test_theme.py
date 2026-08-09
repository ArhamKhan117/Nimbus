"""Tests for the design system (SHELL_AND_CHAT.md §2).

Two workstreams consume `theme.py`, so its job is to make drift impossible. The tests that
matter most are therefore not "does this constant exist" but:

* **Contrast is measured, not eyeballed.** These caught a real failure: the first draft used
  `#6B6B75` for muted text, which is 3.49:1 on an elevated surface and fails WCAG AA for body
  text -- while being intended for exactly the small labels it fails for.
* **The stylesheet contains no literal colours.** A hardcoded `#1a1a1a` next to
  `BG_ELEVATED = "#141417"` is invisible in review and impossible to grep for.
* **Reduced motion is honoured**, including the subtle part: a 0ms animation must still emit
  `finished`, because cleanup logic hangs off that signal.
"""

import pytest


BACKGROUNDS = ("BG_BASE", "BG_ELEVATED", "BG_RAISED", "BG_SUNKEN")


class TestColourMaths:
    def test_parse_hex(self):
        from theme import parse_hex
        assert parse_hex("#FF7A1A") == (255, 122, 26)
        assert parse_hex("ff7a1a") == (255, 122, 26)

    @pytest.mark.parametrize("bad", ["#FFF", "", "#GGGGGG-", "nope"])
    def test_parse_hex_rejects_malformed(self, bad):
        from theme import parse_hex
        with pytest.raises(ValueError):
            parse_hex(bad)

    def test_contrast_ratio_extremes(self):
        """Black on white is 21:1; a colour against itself is 1:1."""
        from theme import contrast_ratio
        assert round(contrast_ratio("#000000", "#FFFFFF"), 1) == 21.0
        assert round(contrast_ratio("#123456", "#123456"), 2) == 1.0

    def test_contrast_ratio_is_symmetric(self):
        from theme import contrast_ratio
        assert contrast_ratio("#FF7A1A", "#0B0B0D") == contrast_ratio(
            "#0B0B0D", "#FF7A1A")

    def test_rgba_builds_a_qt_string(self):
        from theme import rgba
        assert rgba("#FF7A1A", 0.5) == "rgba(255,122,26,0.500)"

    def test_rgba_clamps_alpha(self):
        from theme import rgba
        assert rgba("#FFFFFF", 5.0).endswith("1.000)")
        assert rgba("#FFFFFF", -1.0).endswith("0.000)")


class TestContrast:
    """WCAG AA. These are the tests that found the bug."""

    @pytest.mark.parametrize("fg", ["TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED"])
    @pytest.mark.parametrize("bg", BACKGROUNDS)
    def test_body_text_meets_wcag_aa(self, fg, bg):
        """4.5:1 minimum on every surface it can appear on.

        TEXT_MUTED was #6B6B75 (3.49:1 on BG_ELEVATED) and would have shipped failing.
        """
        import theme
        ratio = theme.contrast_ratio(getattr(theme, fg), getattr(theme, bg))
        assert ratio >= 4.5, f"{fg} on {bg} is {ratio:.2f}:1, needs 4.5:1"

    @pytest.mark.parametrize("fg", ["ACCENT", "SUCCESS", "WARNING", "DANGER"])
    @pytest.mark.parametrize("bg", ["BG_BASE", "BG_ELEVATED", "BG_RAISED"])
    def test_accent_and_state_colours_meet_aa(self, fg, bg):
        import theme
        ratio = theme.contrast_ratio(getattr(theme, fg), getattr(theme, bg))
        assert ratio >= 4.5, f"{fg} on {bg} is {ratio:.2f}:1"

    def test_text_disabled_is_deliberately_below_aa(self):
        """Documents intent. Disabled text must NOT be readable as content -- that is the
        point of it. Without this test someone 'fixes' the contrast and disabled stops
        looking disabled."""
        import theme
        assert theme.contrast_ratio(theme.TEXT_DISABLED, theme.BG_ELEVATED) < 4.5

    def test_dark_text_on_accent_beats_white_text_on_accent(self):
        """Why primary buttons use ON_ACCENT rather than white. White-on-orange is a common
        and entirely avoidable readability mistake."""
        import theme
        dark = theme.contrast_ratio(theme.ON_ACCENT, theme.ACCENT)
        white = theme.contrast_ratio("#FFFFFF", theme.ACCENT)
        assert dark > white

    def test_on_accent_meets_aa_against_every_accent_state(self):
        import theme
        for accent in (theme.ACCENT, theme.ACCENT_HOVER, theme.ACCENT_PRESS):
            assert theme.contrast_ratio(theme.ON_ACCENT, accent) >= 4.5


class TestPalette:
    def test_every_solid_colour_is_valid_hex(self):
        import theme
        for name in dir(theme):
            value = getattr(theme, name)
            if not (name.isupper() and isinstance(value, str)):
                continue
            if value.startswith("#"):
                theme.parse_hex(value)  # must not raise

    def test_alpha_colours_are_valid_rgba(self):
        import theme
        for name in ("HIGHLIGHT_TOP", "ACCENT_WASH", "ACCENT_GLOW", "ACCENT_HAIR"):
            value = getattr(theme, name)
            assert value.startswith("rgba(") and value.endswith(")"), name

    def test_palette_has_exactly_one_accent_hue(self):
        """A dark theme stays legible because the accent is scarce. Two accents means neither
        reads as 'Nimbus'."""
        import theme
        hues = set()
        for name in ("ACCENT", "ACCENT_HOVER", "ACCENT_PRESS"):
            r, g, b = theme.parse_hex(getattr(theme, name))
            assert r > g > b, f"{name} is not a warm orange"
            hues.add(round((g - b) / max(r, 1), 1))
        assert len(hues) <= 2, "accent variants drifted to different hues"

    def test_elevation_ramp_is_monotonic(self):
        """Each step must be lighter than the one below, or 'elevation' means nothing."""
        import theme
        ramp = [theme.BG_SUNKEN, theme.BG_BASE, theme.BG_ELEVATED,
                theme.BG_RAISED, theme.BG_HOVER, theme.BG_ACTIVE]
        lums = [theme.relative_luminance(c) for c in ramp]
        assert lums == sorted(lums), "elevation ramp is not monotonically lighter"

    def test_surfaces_share_a_consistent_cool_tint(self):
        """The surfaces are slightly COOL, and that is deliberate.

        This test originally asserted the opposite, because the design doc claimed the ramp was
        warm. It is not -- #141417 is r=20, b=23 -- and the colours are right while the prose
        was wrong. A cool neutral is the complement of a warm orange accent, so the accent
        reads as vivid; a warm grey would sit next to it on the wheel and muddy it.

        What actually matters is that the tint is *consistent*, which is what makes five greys
        read as one material.
        """
        import theme
        for name in ("BG_BASE", "BG_ELEVATED", "BG_RAISED", "BG_HOVER", "BG_ACTIVE"):
            r, g, b = theme.parse_hex(getattr(theme, name))
            assert b >= r, f"{name} broke the cool tint (r={r}, b={b})"
            assert b - r <= 10, f"{name} is too blue; it will read as navy, not neutral"

    def test_accent_is_warm_against_cool_surfaces(self):
        """The complementary relationship the ramp exists to serve."""
        import theme
        ar, _, ab = theme.parse_hex(theme.ACCENT)
        sr, _, sb = theme.parse_hex(theme.BG_ELEVATED)
        assert ar > ab, "accent must be warm"
        assert sb >= sr, "surface must be cool"

    def test_borders_are_darker_than_the_surface_they_outline(self):
        """Two-tone borders (dark outline + light top edge) are what read as a bevel."""
        import theme
        assert theme.relative_luminance(theme.BORDER) > theme.relative_luminance(
            theme.BG_ELEVATED)
        assert theme.relative_luminance(
            theme.BORDER_STRONG) > theme.relative_luminance(theme.BORDER)


class TestGeometryAndType:
    def test_spacing_scale_is_ascending_and_unique(self):
        from theme import SPACE
        assert list(SPACE) == sorted(set(SPACE))

    def test_spacing_starts_at_four(self):
        """A 4px base keeps every value divisible and avoids half-pixel rendering."""
        from theme import SPACE
        assert SPACE[0] == 4
        assert all(step % 4 == 0 for step in SPACE)

    def test_radius_scale_is_ordered(self):
        import theme
        assert theme.RADIUS_CONTROL < theme.RADIUS_CARD < theme.RADIUS_PILL

    def test_font_sizes_descend_by_role(self):
        import theme
        assert (theme.FONT_DISPLAY > theme.FONT_TITLE > theme.FONT_BODY
                > theme.FONT_SMALL > theme.FONT_MICRO)

    def test_line_height_is_generous_enough_for_dark(self):
        """Light text on dark blooms optically; tighter leading reads as cramped."""
        from theme import LINE_HEIGHT
        assert 1.35 <= LINE_HEIGHT <= 1.6


class TestMotion:
    def test_exit_durations_are_faster_than_entrances(self):
        """An arriving element deserves notice; the same element leaving is in the way. Equal
        durations make dismissal feel sluggish."""
        import theme
        assert theme.DUR_EXIT < theme.DUR_ENTRANCE
        assert theme.DUR_EXIT < theme.DUR_STANDARD

    def test_no_duration_exceeds_the_cap(self):
        """Anything over ~300ms in a utility app reads as lag, not polish."""
        import theme
        for name in ("DUR_MICRO", "DUR_STANDARD", "DUR_ENTRANCE", "DUR_EXIT"):
            assert getattr(theme, name) <= theme.DUR_MAX

    def test_durations_ascend_by_weight(self):
        import theme
        assert theme.DUR_MICRO < theme.DUR_STANDARD < theme.DUR_ENTRANCE

    def test_easing_curves_are_normalised_beziers(self):
        import theme
        for curve in (theme.EASE_STANDARD, theme.EASE_OUT, theme.EASE_IN):
            assert len(curve) == 4
            assert all(0.0 <= value <= 1.0 for value in curve)

    def test_duration_passthrough_when_animations_enabled(self, mocker):
        import theme
        mocker.patch.object(theme, "animations_enabled", return_value=True)
        assert theme.duration(200) == 200

    def test_reduced_motion_collapses_every_duration_to_zero(self, mocker):
        import theme
        mocker.patch.object(theme, "animations_enabled", return_value=False)
        for value in (theme.DUR_MICRO, theme.DUR_STANDARD, theme.DUR_ENTRANCE):
            assert theme.duration(value) == 0

    def test_reduce_motion_setting_overrides_the_system(self, mocker):
        import theme
        mocker.patch.object(theme, "_animations_enabled", None)
        mocker.patch("config.resolve_setting", return_value="on")
        assert theme.animations_enabled() is False
        mocker.patch.object(theme, "_animations_enabled", None)
        mocker.patch("config.resolve_setting", return_value="off")
        assert theme.animations_enabled() is True

    def test_animations_enabled_fails_open(self, mocker):
        """A broken syscall must not silently strip the interface of motion."""
        import theme
        mocker.patch.object(theme, "_animations_enabled", None)
        mocker.patch("config.resolve_setting", side_effect=RuntimeError("no config"))
        mocker.patch("ctypes.windll.user32.SystemParametersInfoW",
                     side_effect=OSError("nope"))
        assert theme.animations_enabled() is True

    def test_animations_enabled_is_cached(self, mocker):
        """It is read on the hover path; a syscall per animation would be wasteful."""
        import theme
        mocker.patch.object(theme, "_animations_enabled", None)
        mocker.patch("config.resolve_setting", return_value="on")
        theme.animations_enabled()
        spy = mocker.patch("config.resolve_setting", return_value="off")
        theme.animations_enabled()
        spy.assert_not_called()


class TestQtHelpers:
    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_qcolor_from_hex(self, qt_app):
        import theme
        colour = theme.qcolor(theme.ACCENT)
        assert (colour.red(), colour.green(), colour.blue()) == (255, 122, 26)
        assert colour.alpha() == 255

    def test_qcolor_from_rgba_preserves_alpha(self, qt_app):
        import theme
        colour = theme.qcolor(theme.ACCENT_WASH)
        assert (colour.red(), colour.green(), colour.blue()) == (255, 122, 26)
        assert 0 < colour.alpha() < 255

    def test_qcolor_alpha_override(self, qt_app):
        import theme
        assert theme.qcolor(theme.ACCENT, 128).alpha() == 128

    def test_easing_from_tuple(self, qt_app):
        import theme
        from PyQt6.QtCore import QEasingCurve
        assert isinstance(theme.easing(theme.EASE_STANDARD), QEasingCurve)

    def test_easing_from_name(self, qt_app):
        import theme
        from PyQt6.QtCore import QEasingCurve
        curve = theme.easing("OutBack")
        assert curve.type() == QEasingCurve.Type.OutBack

    def test_zero_duration_animation_still_emits_finished(self, qt_app):
        """The subtle failure mode of honouring reduced motion.

        Cleanup logic hangs off `finished`. If a 0ms animation never fires it, disabling
        animation silently breaks those paths -- an accessibility setting causing functional
        bugs.
        """
        from PyQt6.QtCore import QPropertyAnimation, QVariantAnimation
        from PyQt6.QtWidgets import QWidget

        widget = QWidget()
        widget.resize(100, 100)
        animation = QPropertyAnimation(widget, b"windowOpacity")
        animation.setDuration(0)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)

        fired = []
        animation.finished.connect(lambda: fired.append(True))
        animation.start()
        while animation.state() == QVariantAnimation.State.Running:
            qt_app.processEvents()
        qt_app.processEvents()
        assert fired == [True]


class TestGrain:
    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_grain_is_a_tileable_square(self, qt_app):
        import theme
        pixmap = theme.grain_pixmap()
        assert pixmap.width() == pixmap.height() == theme.GRAIN_TILE

    def test_grain_is_nearly_transparent(self, qt_app):
        """1-2%. Enough to break gradient banding, not enough to be seen as noise."""
        import theme
        assert 0 < theme.GRAIN_ALPHA <= 12

    def test_grain_is_deterministic(self, qt_app):
        """Fixed seed. A texture that changes per launch makes screenshots and visual diffs
        useless."""
        import theme
        first = theme.grain_pixmap().toImage()
        second = theme.grain_pixmap().toImage()
        assert first == second

    def test_grain_actually_varies(self, qt_app):
        """A uniform tile would not break banding, which is its only job."""
        import theme
        image = theme.grain_pixmap().toImage()
        values = {image.pixel(x, y) for x in range(0, 32) for y in range(0, 32)}
        assert len(values) > 8


class TestStylesheet:
    def test_build_qss_returns_content(self):
        from theme import build_qss
        assert len(build_qss()) > 1000

    def test_qss_references_no_literal_colours(self):
        """Drift guard. A hardcoded #1a1a1a beside BG_ELEVATED = "#141417" is invisible in
        review and impossible to grep for. Generating the stylesheet from constants makes that
        class of mistake impossible -- this test proves it stayed that way."""
        import re
        import theme

        qss = theme.build_qss()
        known = {getattr(theme, name).lower()
                 for name in dir(theme)
                 if name.isupper() and isinstance(getattr(theme, name), str)
                 and getattr(theme, name).startswith("#")}
        for literal in re.findall(r"#[0-9a-fA-F]{6}", qss):
            assert literal.lower() in known, f"{literal} is not a theme constant"

    def test_qss_rgba_values_come_from_theme(self):
        """Every translucent colour must be a theme colour at some alpha.

        The **colour** is what drifts; the alpha is a per-use design decision. An earlier
        version of this test allowlisted exact `(name, alpha)` pairs, which meant every new
        opacity needed the test edited -- so the test was maintenance rather than a guard, and a
        guard nobody wants to touch eventually gets deleted. Checking the RGB triple keeps the
        thing that matters: an invented colour still fails.

        White and black are allowed as pure alpha overlays. That is what a sheen or a scrim
        *is* -- light or shadow, not a colour -- and forcing them through a named hex constant
        would imply they are part of the palette.
        """
        import re
        import theme

        known_rgb = {theme.parse_hex(getattr(theme, name))
                     for name in dir(theme)
                     if name.isupper() and isinstance(getattr(theme, name), str)
                     and getattr(theme, name).startswith("#")}
        known_rgb |= {(255, 255, 255), (0, 0, 0)}

        literals = re.findall(r"rgba\(([^)]*)\)", theme.build_qss())
        assert literals, "the stylesheet uses translucency; this guard must have something to do"
        for literal in literals:
            parts = [p.strip() for p in literal.split(",")]
            rgb = tuple(int(float(p)) for p in parts[:3])
            assert rgb in known_rgb, f"rgba({literal}) is not a theme colour"
            assert 0.0 <= float(parts[3]) <= 1.0, f"rgba({literal}) has an invalid alpha"

    def test_qss_gradients_are_built_from_theme_stops(self):
        """Gradients are where literal colours would hide most easily -- a `qlineargradient`
        is long enough that an odd hex in the middle of one reads as noise."""
        import re
        import theme

        qss = theme.build_qss()
        gradients = re.findall(r"qlineargradient\([^)]*\)", qss)
        assert gradients, "surfaces are meant to be shaded, not flat"
        known = {getattr(theme, name).lower()
                 for name in dir(theme)
                 if name.isupper() and isinstance(getattr(theme, name), str)
                 and getattr(theme, name).startswith("#")}
        # Three sanctioned directions, and no others.
        #
        # Vertical (`x2:0,y2:1`) is light from above -- controls and strips. Diagonal
        # (`x2:1,y2:1`) is one warm source beside the window, so a row of cards looks lit by the
        # same lamp. Horizontal (`x2:1,y2:0`) is for chrome only: a wash down the *face* of the
        # title bar would fight the content, but a warm left edge behind the logo does not.
        #
        # Enumerating them is the point. A fourth direction is almost always a typo -- `x2:1`
        # where `y2:1` was meant puts a horizontal ramp on a card, which reads as a rendering
        # fault rather than as light. `test_cards_are_lit_diagonally` pins that specific case.
        directions = ("x2:0,y2:1", "x2:1,y2:1", "x2:1,y2:0")
        for gradient in gradients:
            compact = gradient.replace(" ", "")
            assert compact.startswith("qlineargradient(x1:0,y1:0"), gradient
            assert any(d in compact for d in directions), (
                f"unrecognised gradient direction: {gradient}")
            for literal in re.findall(r"#[0-9a-fA-F]{6}", gradient):
                assert literal.lower() in known, f"{literal} is not a theme constant"

    def test_cards_are_lit_diagonally(self):
        """The direction guard applied where the mistake would actually show."""
        import re

        import theme

        card_rule = re.search(r"QFrame#Card \{[^}]*\}", theme.build_qss())
        assert card_rule, "the card rule moved; this guard needs updating"
        assert "x2:1, y2:1" in card_rule.group(0)

    def test_the_chrome_is_tinted_horizontally_not_down_its_face(self):
        """A gradient down a 48px title bar reads as a smear; a warm left edge does not."""
        import re

        import theme

        titlebar = re.search(r"QFrame#TitleBar \{[^}]*\}", theme.build_qss())
        assert titlebar, "the title bar rule moved; this guard needs updating"
        assert "x2:1, y2:0" in titlebar.group(0)

    def test_the_warm_corner_tint_is_a_tint_and_not_a_colour(self):
        """`SURFACE_GLOW` is the accent mixed into a card's lit corner.

        It has to be strong enough to see beside the neutral stop and weak enough that a card on
        its own does not look orange. Asserted as a contrast ratio against `SURFACE_TOP` rather
        than by eye, because "subtle" is exactly the kind of judgement that drifts.
        """
        import theme

        ratio = theme.contrast_ratio(theme.SURFACE_GLOW, theme.SURFACE_TOP)
        assert 1.0 < ratio < 1.35, f"{ratio:.3f}:1 is not a tint"
        # Warm: more red than blue, unlike every neutral surface in the ramp.
        r, _g, b = theme.parse_hex(theme.SURFACE_GLOW)
        assert r > b, "the point of the corner stop is that it is warm"
        for neutral in ("SURFACE_TOP", "SURFACE_BOTTOM", "BG_ELEVATED"):
            nr, _ng, nb = theme.parse_hex(getattr(theme, neutral))
            assert nb >= nr, f"{neutral} must stay cool so the tint reads as light"

    def test_qss_styles_the_widgets_the_shell_needs(self):
        from theme import build_qss
        qss = build_qss()
        for selector in ("QPushButton", "QLineEdit", "QCheckBox", "QComboBox",
                         "QGroupBox", "QScrollBar", "QToolTip", "QMenu",
                         "#Card", "#NavItem", "#Primary"):
            assert selector in qss, f"{selector} is unstyled"

    def test_qss_is_applicable_to_a_real_application(self):
        """Catches syntax errors Qt would otherwise swallow with a console warning."""
        from PyQt6.QtWidgets import QApplication
        from theme import build_qss

        app = QApplication.instance() or QApplication([])
        app.setStyleSheet(build_qss())
        assert app.styleSheet()
        app.setStyleSheet("")

    def test_cards_have_no_shadow(self):
        """Shadowing every surface is how a dark theme starts looking like 2014 Material; the
        top highlight and two-tone border do the work instead (§2.5)."""
        from theme import build_qss
        card_block = build_qss().split("QFrame#Card")[1].split("}")[0]
        assert "box-shadow" not in card_block

    def test_cards_have_the_top_highlight(self):
        """The single highest-impact shading technique in the design system."""
        import theme
        card_block = theme.build_qss().split("QFrame#Card")[1].split("}")[0]
        assert "border-top" in card_block
        assert theme.HIGHLIGHT_TOP in card_block


class TestDarkTitlebar:
    """The licence gate is the only window with a *system* frame, and it was white.

    Everything else Nimbus draws is frameless, so a white caption bar with a white close button on a
    near-black dialog had never come up -- on the first screen a new user sees.
    """

    @pytest.fixture(scope="class")
    def qt_app(self):
        # Same class-scoped pattern as `TestQtHelpers` and `TestGrain` above; `qt_app` is not a
        # global fixture in this suite.
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_colorref_reverses_the_byte_order(self):
        """Win32 ``COLORREF`` is ``0x00BBGGRR``, not ``0x00RRGGBB``.

        Guarded because getting it wrong fails *silently*: you get a plausible wrong colour rather
        than an error, red and blue swap, and nobody notices until the warm tint looks blue.
        """
        from theme import _colorref

        assert _colorref("#241A16") == 0x161A24
        assert _colorref("#FF0000") == 0x0000FF, "pure red must land in the low byte"
        assert _colorref("#0000FF") == 0xFF0000, "pure blue must land in the high byte"
        assert _colorref("#000000") == 0
        assert _colorref("FFFFFF") == 0xFFFFFF, "a missing leading # must still parse"

    def test_it_applies_to_a_real_window(self, qt_app):
        """It runs against a real window and reports a boolean, without raising.

        This used to assert the result was ``True`` -- that the OS had *accepted* the dark caption --
        and that was the wrong thing to pin. Whether ``DWMWA_USE_IMMERSIVE_DARK_MODE`` succeeds is a
        property of the machine: it needs Windows 10 build 19041 or later, and a CI runner on Windows
        Server without a full composition session can decline it. A test that fails on a different
        Windows than mine is testing the runner, not Nimbus.

        What Nimbus actually promises is the line below: ask for a dark caption, get a truthful answer,
        never take the window down over it. The colour arithmetic is pinned separately in
        ``test_colorref_reverses_the_byte_order``, which is the part that can genuinely regress.
        """
        from PyQt6.QtWidgets import QDialog

        from theme import apply_dark_titlebar

        dialog = QDialog()
        result = apply_dark_titlebar(dialog)

        assert isinstance(result, bool)
        if not result:
            pytest.skip("DWM declined the dark caption on this machine; nothing to assert about it")

    def test_a_failure_is_swallowed_rather_than_breaking_a_launch(self, mocker):
        """A title bar colour is not worth failing to start over."""
        from theme import apply_dark_titlebar

        broken = mocker.MagicMock()
        broken.winId.side_effect = RuntimeError("no native handle")

        assert apply_dark_titlebar(broken) is False
