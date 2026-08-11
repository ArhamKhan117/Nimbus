"""Tests for the richer annotation vocabulary (T3-5): Rect, Highlight, StepBadge.

Two properties are non-negotiable for every shape, and they are what the existing 18
annotation tests protect:

1. **Its tag never reaches TTS.** Coordinates spoken aloud is the hard invariant in this
   codebase. A new shape whose keyword is missing from the strip regexes would leak numbers
   into speech, and worse, only on the *truncated* path -- so it would pass every happy-path
   test.
2. **Its coordinates map correctly.** Positions transform, lengths only scale (invariant 4).

This file also pins a latent bug T3-5 found: `Rect` was added in T1-2 for structured
`box_2d` output, but neither `app._annotations_to_physical` nor
`overlay.annotations_to_local` learned about it, so every rectangle was silently discarded
before reaching the screen. `draw_box` could fire and nothing would ever appear.
"""

import pytest


class TestShapeParsing:
    def test_rect_parses(self):
        from annotations import Rect, parse_annotations
        spoken, anns = parse_annotations("here it is [RECT:10,20,100,40:save]")
        assert anns == [Rect(10, 20, 100, 40, "save")]
        assert spoken == "here it is"

    def test_rect_label_is_optional(self):
        from annotations import Rect, parse_annotations
        _, anns = parse_annotations("[RECT:10,20,100,40]")
        assert anns == [Rect(10, 20, 100, 40, "")]

    def test_highlight_parses(self):
        from annotations import Highlight, parse_annotations
        _, anns = parse_annotations("[HIGHLIGHT:5,6,700,300:export panel]")
        assert anns == [Highlight(5, 6, 700, 300, "export panel")]

    def test_step_badge_parses(self):
        from annotations import StepBadge, parse_annotations
        _, anns = parse_annotations("[STEP:40,50,2:insert tab]")
        assert anns == [StepBadge(40, 50, 2, "insert tab")]

    def test_lowercase_tags_parse(self):
        """The annotation prompt asks for all-lowercase prose, so the model emits
        lowercase tags -- they must parse AND strip."""
        from annotations import parse_annotations
        for tag in ("[rect:1,2,3,4:a]", "[highlight:1,2,3,4:a]", "[step:1,2,3:a]"):
            spoken, anns = parse_annotations(f"look {tag}")
            assert len(anns) == 1, tag
            assert spoken == "look", tag

    def test_whitespace_variants_parse(self):
        from annotations import parse_annotations
        spoken, anns = parse_annotations("x [ RECT : 1 , 2 , 3 , 4 : a ]")
        assert len(anns) == 1
        assert spoken == "x"

    def test_shapes_returned_in_order_of_appearance(self):
        from annotations import Highlight, Rect, StepBadge, parse_annotations
        _, anns = parse_annotations(
            "a [HIGHLIGHT:1,1,9,9:p] b [STEP:2,2,1:s] c [RECT:3,3,4,4:r]")
        assert [type(a) for a in anns] == [Highlight, StepBadge, Rect]

    def test_multiple_step_badges_keep_their_numbers(self):
        from annotations import parse_annotations
        _, anns = parse_annotations(
            "[STEP:1,1,1:a][STEP:2,2,2:b][STEP:3,3,3:c]")
        assert [a.n for a in anns] == [1, 2, 3]

    def test_existing_shapes_still_parse(self):
        """Regression gate: the four original shapes must be untouched."""
        from annotations import Arrow, Circle, Label, Underline, parse_annotations
        _, anns = parse_annotations(
            "[CIRCLE:1,2,3:c][ARROW:1,2->3,4][UNDERLINE:5,6,7][LABEL:8,9:t]")
        assert [type(a) for a in anns] == [Circle, Arrow, Underline, Label]

    def test_rect_center_property(self):
        from annotations import Rect
        assert Rect(10, 20, 100, 40).center == (60, 40)

    def test_highlight_center_property(self):
        from annotations import Highlight
        assert Highlight(10, 20, 100, 40).center == (60, 40)


class TestNeverSpeakCoordinates:
    """The hard invariant. Every new shape must satisfy it on BOTH paths."""

    @pytest.mark.parametrize("tag", [
        "[RECT:10,20,100,40:save]",
        "[HIGHLIGHT:5,6,700,300:panel]",
        "[STEP:40,50,2:step two]",
    ])
    def test_complete_tag_stripped_from_spoken_text(self, tag):
        from annotations import parse_annotations
        spoken, _ = parse_annotations(f"click the save button {tag}")
        assert spoken == "click the save button"
        for digit in "0123456789":
            assert digit not in spoken

    @pytest.mark.parametrize("truncated", [
        "look here [RECT:10,20,100",
        "look here [HIGHLIGHT:5,6,700",
        "look here [STEP:40,50",
        "look here [rect:10,20",
    ])
    def test_unterminated_tag_fail_closed_stripped(self, truncated):
        """The path that would silently leak. A truncated stream (cancel, token limit,
        network cut) has no closing bracket, so only the fail-closed regex catches it --
        and a new keyword missing from THAT regex passes every happy-path test."""
        from annotations import parse_annotations
        spoken, _ = parse_annotations(truncated)
        assert spoken == "look here"
        assert "[" not in spoken
        for digit in "0123456789":
            assert digit not in spoken

    def test_every_shape_keyword_is_in_both_strip_regexes(self):
        """Structural guard. The keyword list is now defined once and interpolated into
        both regexes precisely so these cannot diverge; this test proves it holds."""
        from annotations import _ANY_TAG_RE, _SHAPE_KEYWORDS, _UNTERMINATED_TAG_RE
        for keyword in _SHAPE_KEYWORDS.split("|"):
            assert keyword in _ANY_TAG_RE.pattern
            assert keyword in _UNTERMINATED_TAG_RE.pattern

    def test_all_seven_shapes_are_covered(self):
        from annotations import _SHAPE_KEYWORDS
        assert set(_SHAPE_KEYWORDS.split("|")) == {
            "ARROW", "CIRCLE", "UNDERLINE", "LABEL", "RECT", "HIGHLIGHT", "STEP",
        }

    def test_unrelated_brackets_are_not_eaten(self):
        """The strip regexes are keyword-narrow so legitimate prose survives."""
        from annotations import parse_annotations
        spoken, anns = parse_annotations("the array index [0] is the first one")
        assert spoken == "the array index [0] is the first one"
        assert anns == []


class TestPhysicalMapping:
    """app._annotations_to_physical — screenshot pixels to physical desktop pixels."""

    def _cap(self, scale=2.0, left=100, top=50):
        class _Cap:
            scale_x = scale
            scale_y = scale
            monitor = {"left": left, "top": top}
            target_width = 1000
            target_height = 500
        return _Cap()

    def test_rect_is_no_longer_dropped(self):
        """THE latent bug T3-5 found: Rect existed from T1-2 but neither transform knew
        about it, so every structured box_2d rectangle vanished before rendering."""
        from annotations import Rect
        from app import _annotations_to_physical
        out = _annotations_to_physical([Rect(10, 20, 100, 40, "save")], self._cap())
        assert len(out) == 1, "Rect was silently discarded"
        assert isinstance(out[0], Rect)

    def test_rect_position_transforms_and_lengths_scale(self):
        """Invariant 4: origin is added to positions only, never to sizes."""
        from annotations import Rect
        from app import _annotations_to_physical
        out = _annotations_to_physical(
            [Rect(10, 20, 100, 40, "save")], self._cap(scale=2.0, left=100, top=50))
        rect = out[0]
        assert (rect.x, rect.y) == (120, 90)      # 10*2+100, 20*2+50
        assert (rect.w, rect.h) == (200, 80)      # scaled only
        assert rect.label == "save"

    def test_highlight_maps_like_rect(self):
        from annotations import Highlight
        from app import _annotations_to_physical
        out = _annotations_to_physical([Highlight(10, 20, 100, 40, "p")], self._cap())
        assert isinstance(out[0], Highlight)
        assert (out[0].x, out[0].y, out[0].w, out[0].h) == (120, 90, 200, 80)

    def test_highlight_does_not_become_a_rect(self):
        """type(a)(...) preserves the subclass distinction; a Highlight rendered as a Rect
        would frame the target instead of dimming everything else."""
        from annotations import Highlight, Rect
        from app import _annotations_to_physical
        out = _annotations_to_physical([Highlight(1, 2, 3, 4)], self._cap())
        assert not isinstance(out[0], Rect)

    def test_step_badge_position_transforms_and_number_survives(self):
        from annotations import StepBadge
        from app import _annotations_to_physical
        out = _annotations_to_physical(
            [StepBadge(10, 20, 3, "third")], self._cap(scale=2.0, left=100, top=50))
        assert (out[0].x, out[0].y) == (120, 90)
        assert out[0].n == 3
        assert out[0].label == "third"

    def test_existing_shapes_unchanged(self):
        from annotations import Circle, Underline
        from app import _annotations_to_physical
        out = _annotations_to_physical(
            [Circle(10, 20, 5, "c"), Underline(10, 20, 50)], self._cap())
        assert (out[0].x, out[0].y, out[0].r) == (120, 90, 10)
        assert (out[1].x, out[1].y, out[1].w) == (120, 90, 100)

    def test_empty_list_short_circuits(self):
        from app import _annotations_to_physical
        assert _annotations_to_physical([], self._cap()) == []


class TestLocalLogicalMapping:
    """overlay.annotations_to_local — physical pixels to per-screen logical DIP."""

    def _screen(self, ratio=2.0, origin=(0, 0)):
        """QScreen-like fake. ``geometry()`` must expose ``left()``/``top()`` -- matching
        tests/test_overlay.py's _MockRect, since physical_to_local_logical uses those."""
        class _Geo:
            def __init__(self, left, top):
                self._left, self._top = left, top
            def left(self):
                return self._left
            def top(self):
                return self._top

        class _Screen:
            def __init__(self, r, o):
                self._r, self._o = r, o
            def devicePixelRatio(self):
                return self._r
            def geometry(self):
                return _Geo(*self._o)
        return _Screen(ratio, origin)

    def test_rect_is_no_longer_dropped(self):
        from annotations import Rect
        from overlay import annotations_to_local
        out = annotations_to_local([Rect(200, 100, 400, 80, "x")], self._screen())
        assert len(out) == 1, "Rect discarded at the overlay boundary"

    def test_rect_lengths_only_divide_by_ratio(self):
        from annotations import Rect
        from overlay import annotations_to_local
        out = annotations_to_local(
            [Rect(200, 100, 400, 80, "x")], self._screen(ratio=2.0))
        assert (out[0].w, out[0].h) == (200, 40)

    def test_highlight_type_preserved(self):
        from annotations import Highlight
        from overlay import annotations_to_local
        out = annotations_to_local([Highlight(200, 100, 400, 80)], self._screen())
        assert isinstance(out[0], Highlight)

    def test_step_badge_number_and_label_survive(self):
        from annotations import StepBadge
        from overlay import annotations_to_local
        out = annotations_to_local([StepBadge(200, 100, 7, "seven")], self._screen())
        assert out[0].n == 7 and out[0].label == "seven"

    def test_unknown_shape_is_ignored_not_fatal(self):
        from overlay import annotations_to_local
        assert annotations_to_local([object()], self._screen()) == []


def _paint_host(annotations, width=800, height=600):
    """Minimal stand-in exposing only what ``_paint_annotations`` actually uses.

    Deliberately NOT an ``OverlayWindow``. Building one via ``__new__`` skips its
    ``__init__``, leaving Qt internals uninitialised, and the paint path then crashes the
    interpreter outright (``0xC0000409``) rather than failing as a test. Borrowing the paint
    methods onto a plain object exercises the same drawing code with no widget lifecycle.

    ``_draw_label_pill`` and ``_draw_arrowhead`` are staticmethods on the real class, so
    they must be re-wrapped or Python would pass ``self`` as the painter.
    """
    from overlay import OverlayWindow

    class _Host:
        _paint_annotations = OverlayWindow._paint_annotations
        _draw_highlight_dim = OverlayWindow._draw_highlight_dim
        _draw_step_badge = OverlayWindow._draw_step_badge
        _draw_label_pill = staticmethod(OverlayWindow._draw_label_pill)
        _draw_arrowhead = staticmethod(OverlayWindow._draw_arrowhead)

        def width(self):
            return width

        def height(self):
            return height

    host = _Host()
    host._annotations = list(annotations)
    host._annotation_started_at = None
    return host


def _render(host, width=800, height=600):
    from PyQt6.QtGui import QPainter, QPixmap

    pixmap = QPixmap(width, height)
    painter = QPainter(pixmap)
    try:
        host._paint_annotations(painter)
    finally:
        painter.end()


class TestRendering:
    """Painting must not raise. An exception on the Qt paint path spams the event loop
    rather than failing cleanly."""

    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    @pytest.mark.parametrize("build", [
        lambda m: m.Rect(20, 20, 200, 60, "save"),
        lambda m: m.Rect(20, 20, 200, 60, ""),
        lambda m: m.Highlight(40, 40, 300, 200, "panel"),
        lambda m: m.Highlight(-50, -50, 5000, 5000, ""),   # clamped off-screen
        lambda m: m.StepBadge(120, 90, 1, "first"),
        lambda m: m.StepBadge(120, 90, 12, ""),
        lambda m: m.Circle(100, 100, 30, "c"),             # existing shapes still paint
        lambda m: m.Arrow(10, 10, 200, 200),
        lambda m: m.Underline(10, 300, 120),
        lambda m: m.Label(400, 300, "hint"),
    ])
    def test_shape_renders_without_raising(self, qt_app, build):
        import annotations as ann_module
        _render(_paint_host([build(ann_module)]))

    def test_highlight_and_other_shapes_together(self, qt_app):
        """Highlight paints in a separate first pass so it cannot dim the annotations it
        exists to draw attention to. Ordered highlight LAST here on purpose -- the model
        controls list order and must not be able to break the visual."""
        import annotations as ann_module
        _render(_paint_host([
            ann_module.StepBadge(100, 100, 1, "a"),
            ann_module.Rect(200, 200, 100, 50, "b"),
            ann_module.Highlight(50, 50, 400, 300, "c"),
        ]))

    def test_highlight_off_screen_bounds_are_clamped(self, qt_app):
        """Negative or oversized rectangles must not produce inverted fill rects."""
        import annotations as ann_module
        for rect in [
            ann_module.Highlight(-200, -200, 100, 100),
            ann_module.Highlight(900, 700, 400, 400),
            ann_module.Highlight(0, 0, 0, 0),
        ]:
            _render(_paint_host([rect]))

    def test_empty_annotation_list_paints_nothing(self, qt_app):
        _render(_paint_host([]))


class TestNativeGeminiTools:
    """T3-5 x T1-2: the native path gets shapes from tools, not tags, so the new shapes
    need declarations there or they would be unavailable on the default provider."""

    def _client(self):
        from gemini_native import GeminiNativeClient
        return GeminiNativeClient(
            api_key="AQ.fake", model_id="gemini-3-flash-preview",
            client_factory=lambda api_key=None: object(),
        )

    def test_annotation_mode_declares_the_new_tools(self):
        tools = self._client()._build_tools(annotation_mode=True)
        names = {d.name for t in tools for d in t.function_declarations}
        assert {"point_at", "draw_box", "highlight_region", "mark_step"} <= names

    def test_non_annotation_mode_declares_only_point_at(self):
        """Regression gate: a normal turn must not gain annotation tools."""
        tools = self._client()._build_tools(annotation_mode=False)
        names = {d.name for t in tools for d in t.function_declarations}
        assert names == {"point_at"}

    def _harvest(self, calls, target=(1000, 500)):
        from gemini_native import _GeminiNativeStreamingResponse
        response = _GeminiNativeStreamingResponse(iter(()), target[0], target[1])
        response._calls = calls
        response._geometry_collected = True
        return response.geometry()

    def test_highlight_region_becomes_a_highlight(self):
        from annotations import Highlight
        shapes = self._harvest([
            ("highlight_region",
             {"box_2d": [100, 200, 300, 600], "label": "panel"}),
        ])
        assert len(shapes) == 1
        assert isinstance(shapes[0], Highlight)
        assert shapes[0].label == "panel"

    def test_mark_step_becomes_a_step_badge(self):
        from annotations import StepBadge
        shapes = self._harvest([
            ("mark_step", {"y": 500, "x": 500, "n": 2, "label": "second"}),
        ])
        assert shapes == [StepBadge(500, 250, 2, "second")]

    def test_step_number_zero_is_dropped(self):
        """n is 1-based because a human reads it; 0 means the model got it wrong."""
        assert self._harvest([
            ("mark_step", {"y": 500, "x": 500, "n": 0, "label": "x"})]) == []

    def test_malformed_step_number_is_dropped_not_raised(self):
        assert self._harvest([
            ("mark_step", {"y": 500, "x": 500, "n": "two", "label": "x"})]) == []

    def test_draw_box_still_works(self):
        from annotations import Rect
        shapes = self._harvest([
            ("draw_box", {"box_2d": [0, 0, 500, 500], "label": "save"})])
        assert len(shapes) == 1 and isinstance(shapes[0], Rect)

    def test_malformed_box_is_dropped(self):
        assert self._harvest([("highlight_region", {"box_2d": [1, 2], "label": "x"})]) == []
