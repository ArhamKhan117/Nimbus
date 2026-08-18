"""Unit tests for chat_hud.py — the floating chat panel.

Real widgets, module-scoped QApplication. The HUD is never constructed via ``__new__`` plus a
manual ``QWidget.__init__``: that skips Qt's own initialisation and takes the interpreter down
with 0xC0000409 rather than failing a test. Every HUD a test creates is kept in ``_created`` so
Python cannot collect a parent and leave Qt holding deleted children.

The capture-exclusion tests are the ones that matter. Invariant 1 is the reason this feature is
safe to ship at all, and a silent failure there is invisible until someone notices Nimbus
pointing at its own chat panel.

Imports live inside the test functions, per IMPROVEMENTS.md §1.4.
"""

import pytest


@pytest.fixture(scope="module")
def qt_app():
    """One QApplication for the module. Qt requires it before any QWidget exists."""
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def make_hud(qt_app, tmp_path):
    """Factory for HUDs that never touch the real profile or real Win32 by default.

    ``positions_path`` and any store are inside ``tmp_path``; ``exclude`` defaults to a stub so
    the ordinary tests do not depend on the host's Windows build. The tests that care about the
    real syscall opt into it explicitly.
    """
    from PyQt6.QtCore import QRect

    from chat_hud import ChatHud

    created = []

    def factory(**kwargs):
        kwargs.setdefault("exclude", lambda hwnd: True)
        kwargs.setdefault("positions_path", tmp_path / "chat_hud.json")
        kwargs.setdefault("autohide_seconds", 0)
        kwargs.setdefault("screen_geometry_fn", lambda: QRect(0, 0, 1920, 1080))
        hud = ChatHud(**kwargs)
        created.append(hud)
        return hud

    yield factory

    for hud in created:
        hud.hide()


@pytest.fixture
def store(tmp_path):
    from sessions import SessionStore
    return SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=False)


def _display_affinity(hwnd):
    import ctypes

    value = ctypes.c_uint(0)
    ctypes.windll.user32.GetWindowDisplayAffinity(
        ctypes.c_void_p(int(hwnd)), ctypes.byref(value))
    return value.value


class TestCaptureExclusion:
    def test_capture_exclusion_is_applied_on_show(self, make_hud):
        """Invariant 1. THE test for this feature."""
        calls = []
        hud = make_hud(exclude=lambda hwnd: calls.append(hwnd) or True)
        hud.show()

        assert calls, "SetWindowDisplayAffinity was never attempted"
        assert calls[0] == int(hud.winId())
        assert hud.capture_exclusion_active is True

    def test_exclusion_is_reapplied_on_every_show(self, make_hud):
        """The affinity belongs to the HWND; any path that recreates it would lose it."""
        calls = []
        hud = make_hud(exclude=lambda hwnd: calls.append(hwnd) or True)
        hud.show()
        hud.hide()
        hud.show()
        assert len(calls) == 2

    def test_falls_back_to_hiding_when_exclusion_unavailable(self, make_hud):
        """Simulate SetWindowDisplayAffinity returning 0 (pre-19041 Windows)."""
        hud = make_hud(exclude=lambda hwnd: False)
        hud.show()

        assert hud.capture_exclusion_active is False
        assert hud.needs_hide_for_capture() is True

        hud.hide_for_capture()
        assert not hud.isVisible()
        hud.show_after_capture()
        assert hud.isVisible()

    def test_hide_for_capture_is_a_noop_when_exclusion_works(self, make_hud):
        """So app.py can call it unconditionally without knowing which path is live."""
        hud = make_hud(exclude=lambda hwnd: True)
        hud.show()
        hud.hide_for_capture()

        assert hud.needs_hide_for_capture() is False
        assert hud.isVisible()

    def test_exclusion_uses_excludefromcapture_not_monitor(self):
        """WDA_MONITOR renders the region BLACK in the capture, which is worse."""
        import chat_hud

        assert chat_hud._WDA_EXCLUDEFROMCAPTURE == 0x11
        assert chat_hud._WDA_MONITOR == 0x01

    def test_the_win32_call_receives_excludefromcapture(self, mocker):
        import ctypes

        import chat_hud

        spy = mocker.patch.object(
            ctypes.windll.user32, "SetWindowDisplayAffinity", return_value=1)
        assert chat_hud.exclude_from_capture(1234) is True

        _hwnd, flag = spy.call_args[0]
        assert flag.value == chat_hud._WDA_EXCLUDEFROMCAPTURE

    def test_a_failing_syscall_degrades_instead_of_raising(self, mocker):
        import ctypes

        import chat_hud

        mocker.patch.object(
            ctypes.windll.user32, "SetWindowDisplayAffinity",
            side_effect=OSError("no such entry point"))
        assert chat_hud.exclude_from_capture(1234) is False

    def test_real_windows_agrees_with_what_the_call_reported(self, make_hud):
        """Runtime verification, not build-time.

        Whatever this machine does, the affinity Windows reports back must agree with what the
        call claimed. A disagreement means the HUD believes it is hidden from capture while it
        is not, which is the one failure mode nobody would notice until the model started
        describing Nimbus's own panel.
        """
        from chat_hud import _WDA_EXCLUDEFROMCAPTURE, exclude_from_capture

        hud = make_hud(exclude=exclude_from_capture)
        hud.show()
        reported = _display_affinity(int(hud.winId()))

        assert (reported == _WDA_EXCLUDEFROMCAPTURE) == hud.capture_exclusion_active

    def test_the_hud_is_not_layered(self, make_hud):
        """Measured: WS_EX_LAYERED makes SetWindowDisplayAffinity fail outright.

        WA_TranslucentBackground sets that bit, so the translucent body from the design brief
        and capture exclusion are mutually exclusive. Exclusion wins (§4 ⚠ VERIFY 4 named the
        opaque body as the sanctioned fallback), and this test is what stops someone restoring
        the translucency later and silently breaking Invariant 1.
        """
        import ctypes

        from PyQt6.QtCore import Qt

        hud = make_hud()
        hud.show()
        ex_style = ctypes.windll.user32.GetWindowLongW(int(hud.winId()), -20)

        assert not hud.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not ex_style & 0x00080000, "WS_EX_LAYERED breaks capture exclusion"

    def test_rounded_corners_do_not_disturb_the_affinity(self, make_hud):
        """How the HUD keeps rounded corners without layering."""
        from chat_hud import _WDA_EXCLUDEFROMCAPTURE, apply_rounded_region, exclude_from_capture

        hud = make_hud(exclude=exclude_from_capture)
        hud.show()
        before = _display_affinity(int(hud.winId()))
        apply_rounded_region(int(hud.winId()), hud.width(), hud.height())

        assert _display_affinity(int(hud.winId())) == before
        if before == _WDA_EXCLUDEFROMCAPTURE:
            assert hud.capture_exclusion_active is True

    def test_no_window_level_opacity_is_ever_set(self, make_hud):
        """setWindowOpacity(<1.0) forces Qt's layered path, which would break exclusion."""
        hud = make_hud()
        hud.show()
        hud.reveal()
        hud.dismiss()
        assert hud.windowOpacity() == 1.0


class TestWindowBehaviour:
    def test_never_takes_focus_on_appearing(self, make_hud):
        from PyQt6.QtCore import Qt

        hud = make_hud()
        flags = hud.windowFlags()

        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert hud.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert hud.focusPolicy() == Qt.FocusPolicy.NoFocus

    def test_not_in_alt_tab(self, make_hud):
        import ctypes

        from PyQt6.QtCore import Qt

        hud = make_hud()
        assert hud.windowFlags() & Qt.WindowType.Tool
        hud.show()
        ex_style = ctypes.windll.user32.GetWindowLongW(int(hud.winId()), -20)
        assert ex_style & 0x00000080, "WS_EX_TOOLWINDOW keeps it out of Alt-Tab"

    def test_stays_on_top_and_frameless(self, make_hud):
        from PyQt6.QtCore import Qt

        flags = make_hud().windowFlags()
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.FramelessWindowHint

    def test_close_button_hides_and_does_not_quit(self, make_hud):
        """Nimbus is a background tool; dismissing a panel must not stop push-to-talk."""
        hud = make_hud()
        hud.show()
        hud.close_button.click()

        assert not hud.isVisible()
        from PyQt6.QtWidgets import QApplication
        assert QApplication.instance() is not None

    def test_the_default_size_is_deliberately_small(self):
        """600x340, down from 720x420.

        The panel floats over the application the user is asking about, so every pixel it takes
        is a pixel of their actual work it hides. This holds three or four turns without
        scrolling, which is as much conversation as anyone reads back in a voice interaction.
        Asserted against the constants so the two cannot drift.
        """
        from chat_hud import HUD_HEIGHT, HUD_WIDTH, MAX_HEIGHT, MAX_WIDTH, MIN_HEIGHT, MIN_WIDTH

        assert (HUD_WIDTH, HUD_HEIGHT) == (660, 430)
        assert MIN_WIDTH <= HUD_WIDTH <= MAX_WIDTH
        assert MIN_HEIGHT <= HUD_HEIGHT <= MAX_HEIGHT

    def test_the_widget_opens_at_the_default_size(self, make_hud):
        from chat_hud import HUD_HEIGHT, HUD_WIDTH

        hud = make_hud()
        assert (hud.width(), hud.height()) == (HUD_WIDTH, HUD_HEIGHT)


class TestPositioning:
    def test_reset_position_returns_to_top_centre(self, make_hud):
        """A window dragged onto a now-unplugged monitor must be recoverable."""
        from PyQt6.QtCore import QRect

        from chat_hud import HUD_WIDTH

        hud = make_hud(screen_geometry_fn=lambda: QRect(0, 0, 1920, 1080))
        hud.move(4000, 900)
        hud.reset_position()

        assert (hud.x(), hud.y()) == ((1920 - HUD_WIDTH) // 2, 24)

    def test_top_centre_respects_a_screen_offset(self):
        from PyQt6.QtCore import QRect

        from chat_hud import top_centre_position

        assert top_centre_position(QRect(1920, 0, 1280, 720), 720) == (1920 + 280, 24)

    def test_drag_persists_position_per_monitor(self, make_hud):
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        hud._begin_drag(QPoint(hud.x() + 10, hud.y() + 10))
        hud._drag_to(QPoint(hud.x() + 210, hud.y() + 110))
        hud._end_drag()

        saved = hud.saved_positions()
        assert saved, "nothing was persisted"
        assert list(saved.values())[0] == [hud.x(), hud.y()]
        assert len(saved) == 1, "positions must be keyed per monitor"

    def test_saved_position_is_restored_by_a_new_hud(self, make_hud, tmp_path):
        from PyQt6.QtCore import QPoint

        first = make_hud()
        first.show()
        first._begin_drag(QPoint(first.x(), first.y()))
        first._drag_to(QPoint(first.x() + 120, first.y() + 60))
        first._end_drag()
        expected = (first.x(), first.y())

        second = make_hud()
        assert (second.x(), second.y()) == expected

    def test_an_offscreen_saved_position_falls_back_to_top_centre(self, make_hud, tmp_path):
        """The unplugged-monitor case: a panel the user cannot find is worse than a moved one."""
        import json

        from chat_hud import HUD_WIDTH

        path = tmp_path / "chat_hud.json"
        hud = make_hud(positions_path=path)
        path.write_text(json.dumps({hud._screen_key(): [9000, 9000]}), encoding="utf-8")

        hud.restore_position()
        assert (hud.x(), hud.y()) == ((1920 - HUD_WIDTH) // 2, 24)

    def test_unreadable_position_file_is_not_fatal(self, make_hud, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        hud = make_hud(positions_path=path)
        assert hud.saved_positions() == {}


class TestResize:
    @pytest.mark.parametrize("requested,expected", [
        ((100, 100), (460, 260)),
        ((5000, 5000), (1200, 900)),
        ((800, 500), (800, 500)),
    ])
    def test_resize_is_clamped_to_min_and_max(self, requested, expected):
        from chat_hud import clamp_size
        assert clamp_size(*requested) == expected

    def test_the_widget_honours_the_clamp(self, make_hud):
        from chat_hud import MAX_HEIGHT, MAX_WIDTH

        hud = make_hud()
        hud._resize_to(4000, 4000)
        assert (hud.width(), hud.height()) == (MAX_WIDTH, MAX_HEIGHT)

    @pytest.mark.parametrize("corner,expected", [
        ((2, 2), (True, False, True, False)),          # top-left
        ((2, 200), (True, False, False, False)),       # left
        ((300, 2), (False, False, True, False)),       # top
        ((300, 200), (False, False, False, False)),    # middle: not a resize zone
    ])
    def test_every_edge_and_corner_is_a_resize_zone(self, make_hud, corner, expected):
        """It was bottom-edge only, so making the panel *wider* was impossible."""
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        assert hud._edges_at(QPoint(*corner)) == expected

    def test_the_far_edges_are_detected_from_the_live_size(self, make_hud):
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        left, right, top, bottom = hud._edges_at(
            QPoint(hud.width() - 1, hud.height() - 1))
        assert (right, bottom) == (True, True)

    def test_dragging_the_right_edge_widens_without_moving_the_panel(self, make_hud):
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        origin = (hud.x(), hud.y())
        hud._resize_origin = (
            QPoint(500, 500), hud.geometry(), (False, True, False, False))

        hud._resize_from_origin(QPoint(600, 500))

        assert hud.width() == 660 + 100
        assert (hud.x(), hud.y()) == origin, "only the dragged edge may move"

    def test_dragging_the_left_edge_moves_the_panel_and_keeps_the_far_edge_still(
            self, make_hud):
        """The subtle one: a left-edge drag has to move the window *and* resize it, or the
        panel appears to slide away from the pointer."""
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        hud.move(400, 300)
        far_edge = hud.geometry().right()
        hud._resize_origin = (
            QPoint(400, 500), hud.geometry(), (True, False, False, False))

        hud._resize_from_origin(QPoint(340, 500))

        assert hud.width() == 660 + 60
        assert hud.geometry().right() == far_edge

    def test_a_left_drag_past_the_minimum_pins_the_edge(self, make_hud):
        """Clamping before the move, so an over-drag stops rather than sliding the panel."""
        from PyQt6.QtCore import QPoint

        from chat_hud import MIN_WIDTH

        hud = make_hud()
        hud.show()
        hud.move(400, 300)
        far_edge = hud.geometry().right()
        hud._resize_origin = (
            QPoint(400, 500), hud.geometry(), (True, False, False, False))

        hud._resize_from_origin(QPoint(4000, 500))

        assert hud.width() == MIN_WIDTH
        assert hud.geometry().right() == far_edge

    def test_the_edges_are_reachable_by_the_mouse(self, make_hud):
        """The reason resizing did not work, even though ``_edges_at`` was always correct.

        Every pixel of the panel was covered by a child widget. Mouse *move* events only reach a
        widget with mouse tracking on, and children do not forward hover to a parent -- so the
        cursor never changed near an edge and there was nothing to tell the user where to grab.
        Insetting the body leaves a ring of bare ``ChatHud``, which is what ``mouseMoveEvent``
        needs. ``childAt`` returning ``None`` on an edge is the whole fix.
        """
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()

        assert hud.hasMouseTracking(), "no move events means no resize cursor"
        for name, point in (
            ("left", (2, hud.height() // 2)),
            ("right", (hud.width() - 2, hud.height() // 2)),
            ("top", (hud.width() // 2, 2)),
            ("bottom", (hud.width() // 2, hud.height() - 2)),
            ("corner", (hud.width() - 2, hud.height() - 2)),
        ):
            assert hud.childAt(QPoint(*point)) is None, (
                f"a child covers the {name} edge, so the HUD never sees the mouse there")
        # And the middle is emphatically not a resize zone.
        assert hud.childAt(QPoint(hud.width() // 2, hud.height() // 2)) is not None

    def test_the_gutter_matches_the_resize_margin(self, make_hud):
        """If the inset and the hit-test disagree, there is a dead ring that looks grabbable."""
        from chat_hud import RESIZE_MARGIN

        hud = make_hud()
        margins = hud.layout().contentsMargins()

        assert margins.left() == margins.right() == RESIZE_MARGIN
        assert margins.top() == margins.bottom() == RESIZE_MARGIN

    def test_a_collapsed_panel_offers_no_resize(self, make_hud):
        """Its height is fixed, so a vertical drag would fight the constraint and do nothing."""
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        hud.set_collapsed(True)

        assert hud._edges_at(QPoint(2, 2)) == (False, False, False, False)
        assert hud._in_resize_zone(QPoint(2, 2)) is False

    def test_the_bottom_edge_is_the_resize_zone(self, make_hud):
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        assert hud._in_resize_zone(QPoint(300, hud.height() - 1))
        assert not hud._in_resize_zone(QPoint(300, 40))

    def test_every_corner_offers_a_diagonal_cursor(self, make_hud):
        """"The diagonal resize is not showing up" -- and the arithmetic says why.

        With one 5px margin on both axes, a corner is only the 5x5 square where the two strips
        cross. 25 pixels, and one pixel outside it the user silently gets a single-axis resize
        instead of the diagonal they aimed for. ``CORNER_MARGIN`` widens the hit-test only, not the
        bezel: widening ``RESIZE_MARGIN`` would have put a 16px inset around the whole panel to fix
        a cursor.
        """
        from PyQt6.QtCore import Qt, QPoint

        from chat_hud import CORNER_MARGIN, RESIZE_MARGIN

        assert CORNER_MARGIN > RESIZE_MARGIN

        hud = make_hud()
        hud.show()
        probe = CORNER_MARGIN - 2
        for name, point, expected in (
            ("top-left", (probe, probe), Qt.CursorShape.SizeFDiagCursor),
            ("top-right", (hud.width() - probe, probe), Qt.CursorShape.SizeBDiagCursor),
            ("bottom-left", (probe, hud.height() - probe), Qt.CursorShape.SizeBDiagCursor),
            ("bottom-right", (hud.width() - probe, hud.height() - probe),
             Qt.CursorShape.SizeFDiagCursor),
        ):
            edges = hud._edges_at(QPoint(*point))
            assert sum(edges) == 2, f"{name} at {point} resolved to {edges}, not a corner"
            assert hud._EDGE_CURSORS.get(edges) == expected, name

    def test_the_edges_stay_thin_away_from_the_corners(self, make_hud):
        """The wider tolerance is for corners only.

        A 16px grab strip down the whole side would swallow clicks meant for the transcript, and
        the visible bezel is still 5px -- a hit zone three times wider than the thing it looks
        like would feel like misaimed clicks.
        """
        from PyQt6.QtCore import QPoint

        from chat_hud import CORNER_MARGIN, RESIZE_MARGIN

        hud = make_hud()
        hud.show()
        middle_y = hud.height() // 2
        middle_x = hud.width() // 2

        assert hud._edges_at(QPoint(RESIZE_MARGIN - 1, middle_y)) == (
            True, False, False, False)
        assert hud._edges_at(QPoint(RESIZE_MARGIN + 3, middle_y)) == (
            False, False, False, False)
        assert hud._edges_at(QPoint(CORNER_MARGIN - 2, middle_y)) == (
            False, False, False, False), "the side is a thin strip, not a corner-width one"
        assert hud._edges_at(QPoint(middle_x, CORNER_MARGIN - 2)) == (
            False, False, False, False)


class TestState:
    @pytest.mark.parametrize("state,colour_name", [
        ("listening", "SUCCESS"),
        ("thinking", "WARNING"),
        ("speaking", "ACCENT"),
    ])
    def test_state_strip_colour_matches_the_interaction_state(
            self, make_hud, state, colour_name):
        import theme

        hud = make_hud()
        hud.set_state(state)
        assert hud.state_strip_colour() == getattr(theme, colour_name)

    def test_idle_strip_is_invisible(self, make_hud):
        hud = make_hud()
        hud.set_state("idle")
        assert hud.state_strip_colour() == "transparent"

    def test_an_unknown_state_leaves_the_last_one_alone(self, make_hud):
        """A typo upstream must not tell the user Nimbus stopped listening."""
        hud = make_hud()
        hud.set_state("listening")
        hud.set_state("nonsense")
        assert hud.state == "listening"

    def test_state_change_reveals_a_hidden_hud(self, make_hud):
        hud = make_hud()
        hud.hide()
        hud.set_state("listening")
        assert hud.isVisible()

    def test_the_status_line_is_just_the_state(self, make_hud):
        """The hotkey moved off this line.

        It used to read ``⏻ idle · ctrl+alt+space``, which at the panel's width collided with
        the pills beside it and elided mid-word into something that looked broken rather than
        shortened. The chord is still available -- in the empty state, where it teaches the
        interaction at the moment it is relevant, and in this label's tooltip.
        """
        hud = make_hud(hotkey="ctrl+shift+f9")
        hud.set_state("thinking")

        assert "thinking" in hud.status_text()
        assert "ctrl+shift+f9" not in hud.status_text()
        assert "ctrl+shift+f9" in hud._status_label.toolTip().lower()

    def test_the_status_line_never_elides(self, make_hud):
        """A status that shortens itself into nonsense is worse than a shorter status."""
        hud = make_hud()
        hud.show()

        for state in ("listening", "thinking", "speaking", "idle"):
            hud.set_state(state)
            label = hud._status_label
            needed = label.fontMetrics().horizontalAdvance(label.text())
            assert label.width() >= needed, f"{state!r} does not fit its label"

    def test_the_pill_and_minimise_are_gone(self, make_hud):
        """Two controls that both meant "make this smaller", differing in a way nobody could
        predict before clicking. Collapse is the one that survived; this pins the removal."""
        import chat_hud

        hud = make_hud()

        assert not hasattr(hud, "set_minimised")
        assert not hasattr(hud, "minimise_button")
        assert not hasattr(hud, "pill_text")
        assert not hasattr(chat_hud, "PILL_WIDTH")


class TestMessages:
    def test_append_renders_a_row(self, make_hud):
        from sessions import ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(role="user", text="where is the export button?"))

        assert hud.row_count() == 1
        assert hud.message_texts() == ["where is the export button?"]

    def test_messages_arrive_via_signal_not_direct_call(self, make_hud):
        """Invariant 4 — the HUD is fed from three non-Qt threads."""
        from sessions import ChatMessage

        hud = make_hud()
        hud.sig_message.emit(ChatMessage(role="user", text="from another thread"))

        assert hud.message_texts() == ["from another thread"]

    def test_state_and_delta_also_have_signal_entry_points(self, make_hud):
        hud = make_hud()
        hud.sig_state.emit("thinking")
        hud.sig_delta.emit("streamed")

        assert hud.state == "thinking"
        assert hud.message_texts() == ["streamed"]

    def test_system_message_rendered_for_privacy_skip(self, make_hud):
        from sessions import ROLE_SYSTEM, ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(
            role=ROLE_SYSTEM, text="Screenshot skipped — a password manager was open"))

        assert "password manager" in hud.message_texts()[0]
        assert hud.rows()[0].message.role == ROLE_SYSTEM

    def test_empty_state_hides_once_a_message_arrives(self, make_hud):
        from sessions import ChatMessage

        hud = make_hud()
        assert hud._empty_state.isVisibleTo(hud)
        hud.append(ChatMessage(role="user", text="hi"))
        assert not hud._empty_state.isVisibleTo(hud)

    def test_empty_state_shows_the_configured_hotkey_not_a_hardcoded_one(
            self, make_hud, mocker):
        """A user who remapped the hotkey must not be told the wrong chord."""
        import config

        mocker.patch.object(config, "resolve_setting", lambda name, default: "ctrl+shift+f9")
        hud = make_hud(hotkey=None)

        assert "Ctrl+Shift+F9" in hud.empty_state_text()
        assert "Alt+Space" not in hud.empty_state_text()

    def test_hud_exception_does_not_break_the_pipeline(self, make_hud, mocker):
        """Invariant 10 — a HUD failure degrades to 'no chat panel', never 'no answer'."""
        from sessions import ChatMessage

        hud = make_hud()
        mocker.patch.object(
            hud, "_insert_row", side_effect=RuntimeError("widget already deleted"))

        assert hud.append(ChatMessage(role="user", text="q")) is None
        assert hud.stream_delta("delta") is None
        assert hud.row_count() == 0

    def test_a_malformed_message_does_not_raise(self, make_hud):
        hud = make_hud()
        assert hud.append(object()) is None


class TestStreaming:
    def test_streaming_deltas_append_to_the_open_message(self, make_hud):
        """A second delta must extend the current turn, not create a new one."""
        from sessions import ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(role="nimbus", text="it's "))
        hud.stream_delta("top right, ")
        hud.stream_delta("next to Share.")

        assert hud.row_count() == 1
        assert hud.message_texts() == ["it's top right, next to Share."]

    def test_a_delta_with_no_open_turn_opens_one(self, make_hud):
        hud = make_hud()
        hud.stream_delta("first words")

        assert hud.row_count() == 1
        assert hud.rows()[0].message.role == "nimbus"

    def test_a_user_message_closes_the_open_turn(self, make_hud):
        from sessions import ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(role="nimbus", text="answer"))
        hud.append(ChatMessage(role="user", text="follow-up"))
        hud.stream_delta("new answer")

        assert hud.row_count() == 3
        assert hud.message_texts()[-1] == "new answer"

    def test_an_empty_delta_is_ignored(self, make_hud):
        hud = make_hud()
        hud.stream_delta("")
        assert hud.row_count() == 0

    def test_deltas_are_persisted_when_a_store_is_present(self, make_hud, store):
        from sessions import ChatMessage

        session_id = store.new_session("excel.exe")
        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(role="nimbus", text="it's "))
        hud.stream_delta("top right.")

        assert store.messages(session_id)[-1].text == "it's top right."


class TestHoverActions:
    def _turn(self, hud, coordinate=(400, 120)):
        from sessions import ChatMessage

        hud.append(ChatMessage(role="user", text="where is the export button"))
        hud.append(ChatMessage(role="nimbus", text="top right, next to Share",
                               coordinate=coordinate))
        return hud.rows()[-1]

    def test_replay_calls_tts_with_the_stored_text(self, make_hud, mocker):
        hud = make_hud()
        row = self._turn(hud)
        speak = mocker.Mock()
        hud.sig_replay.connect(speak)

        row.replay_button.click()
        speak.assert_called_once_with("top right, next to Share")

    def test_repoint_emits_the_stored_coordinate_without_a_model_call(
            self, make_hud, mocker):
        """The whole value of re-point: no round trip, no tokens."""
        import ai

        create = mocker.patch.object(ai, "create_ai_client")
        hud = make_hud()
        row = self._turn(hud, coordinate=(640, 200))
        seen = mocker.Mock()
        hud.sig_repoint.connect(seen)

        row.repoint_button.click()

        seen.assert_called_once_with(640, 200)
        create.assert_not_called()

    def test_repoint_is_absent_when_the_turn_had_no_coordinate(self, make_hud):
        """A dead button that looks live is worse than an absent one."""
        hud = make_hud()
        row = self._turn(hud, coordinate=None)
        assert row.repoint_button is None
        assert row.replay_button is not None

    def test_user_turns_have_no_replay_or_flag(self, make_hud):
        from sessions import ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(role="user", text="a question"))
        row = hud.rows()[0]

        assert row.replay_button is None
        assert row.wrong_button is None
        assert row.copy_button is not None

    def test_copy_puts_the_turn_on_the_clipboard(self, make_hud, qt_app):
        hud = make_hud()
        row = self._turn(hud)
        row.copy_button.click()

        from PyQt6.QtWidgets import QApplication
        assert QApplication.clipboard().text() == "top right, next to Share"

    def test_controls_appear_on_hover_only(self, make_hud):
        """Four glyphs against every turn turns a transcript into a toolbar."""
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QEnterEvent

        hud = make_hud()
        row = self._turn(hud)
        assert not row._controls.isVisibleTo(row)

        row.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
        assert row._controls.isVisibleTo(row)

        row.leaveEvent(QEvent(QEvent.Type.Leave))
        assert not row._controls.isVisibleTo(row)

    def test_retry_reuses_the_transcript_without_re_recording(self, make_hud, mocker):
        import stt
        from sessions import ChatMessage

        create_stt = mocker.patch.object(stt, "create_stt_client")
        hud = make_hud()
        hud.append(ChatMessage(role="user", text="where is the export button"))
        hud.append(ChatMessage(
            role="system", text="", error="Nimbus couldn't complete that request."))
        retried = mocker.Mock()
        hud.sig_retry.connect(retried)

        hud.rows()[-1]._retry_button.click()

        retried.assert_called_once_with("where is the export button")
        create_stt.assert_not_called()

    def test_retry_with_no_preceding_user_turn_emits_empty(self, make_hud):
        from sessions import ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(role="system", text="", error="failed before you spoke"))
        assert hud.transcript_before(hud.rows()[0]) == ""

    def test_wrong_flag_suppresses_the_review_item_and_notes_it(self, make_hud, tmp_path):
        """T3-3 interaction, driven from the button the user actually clicks."""
        from datetime import date, timedelta

        from review import ReviewQueue
        from sessions import ChatMessage, SessionStore

        db = tmp_path / "index.db"
        queue = ReviewQueue(index_db_path=db)
        queue.add("excel.exe", "where is export", "top right")
        store = SessionStore(index_db_path=db, store_screenshots=False)
        session_id = store.new_session("excel.exe")

        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(role="user", text="where is export"))
        hud.append(ChatMessage(role="nimbus", text="top right"))
        hud.rows()[-1].wrong_button.click()

        assert queue.due(today=date.today() + timedelta(days=2)) == []
        assert hud.rows()[-1].message.role == "system"

    def test_flagging_without_a_store_still_marks_the_row(self, make_hud):
        hud = make_hud()
        row = self._turn(hud)
        row.wrong_button.click()

        assert row.wrong_button.isEnabled() is False


class TestScreenshotDisclosure:
    def test_a_screenshot_row_is_collapsed_by_default(self, make_hud, tmp_path):
        from PIL import Image

        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(
            role="user", text="what is this",
            image=Image.new("RGB", (400, 300), (30, 40, 50))))
        row = hud.rows()[0]

        assert row.message.screenshot
        assert row._disclosure.text().startswith("\u25b8")
        assert not row._thumbnail.isVisibleTo(row)

    def test_expanding_loads_the_thumbnail(self, make_hud, tmp_path):
        from PIL import Image

        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(
            role="user", text="what is this",
            image=Image.new("RGB", (400, 300), (30, 40, 50))))
        row = hud.rows()[0]

        row.toggle_screenshot()

        assert row._thumbnail.isVisibleTo(row)
        assert not row._thumbnail.pixmap().isNull()

    def test_no_screenshot_row_when_storage_is_off(self, make_hud, store):
        from PIL import Image

        from sessions import ChatMessage

        session_id = store.new_session()
        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(
            role="user", text="what is this",
            image=Image.new("RGB", (400, 300), (30, 40, 50))))

        assert hud.rows()[0].message.screenshot == ""
        assert not hasattr(hud.rows()[0], "_disclosure")

    def test_a_privacy_skipped_turn_shows_no_thumbnail(self, make_hud, tmp_path):
        """Invariant 6, seen from the UI: nothing to expand because nothing was written."""
        from PIL import Image

        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(
            role="user", text="what is this",
            image=Image.new("RGB", (400, 300), (30, 40, 50)),
            privacy_skipped=True))

        assert hud.rows()[0].message.screenshot == ""
        assert list(store.chats_dir.glob("**/*.jpg")) == []


class TestSessions:
    def test_set_session_updates_the_header_label(self, make_hud):
        hud = make_hud()
        hud.set_session(7, "pivot tables")

        assert hud.session_id == 7
        assert hud.session_label() == "pivot tables"

    def test_an_untitled_session_reads_as_new_chat(self, make_hud):
        hud = make_hud()
        hud.set_session(3, "")
        assert hud.session_label() == "new chat"

    def test_switching_session_replaces_the_transcript(self, make_hud, store):
        from sessions import ChatMessage

        first = store.new_session("excel.exe")
        store.add_message(first, ChatMessage(role="user", text="about excel"))
        second = store.new_session("photoshop.exe")
        store.add_message(second, ChatMessage(role="user", text="about photoshop"))

        hud = make_hud(store=store)
        hud.set_session(first, "excel")
        assert hud.message_texts() == ["about excel"]

        hud.set_session(second, "photoshop")
        assert hud.message_texts() == ["about photoshop"]

    def test_new_session_clears_the_view(self, make_hud, store):
        from sessions import ChatMessage, start_new_session

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="old context"))
        hud = make_hud(store=store)
        hud.set_session(session_id, "old")

        history = [{"role": "user", "content": []}]
        fresh = start_new_session(store, "excel.exe", history)
        hud.set_session(fresh, "")

        assert hud.row_count() == 0
        assert history == [], "the view is not enough; _history must clear too"

    def test_append_persists_when_a_store_is_present(self, make_hud, store):
        from sessions import ChatMessage

        session_id = store.new_session("excel.exe")
        hud = make_hud(store=store)
        hud.set_session(session_id, "")
        hud.append(ChatMessage(role="user", text="where is export"))

        assert [m.text for m in store.messages(session_id)] == ["where is export"]

    def test_append_persists_nothing_without_a_store(self, make_hud):
        """Constructing a HUD in a test must not write to the developer's real database."""
        from sessions import ChatMessage

        hud = make_hud()
        hud.append(ChatMessage(role="user", text="not stored"))
        assert hud.row_count() == 1

    def test_new_chat_button_emits_the_signal(self, make_hud, mocker):
        hud = make_hud()
        seen = mocker.Mock()
        hud.sig_new_session.connect(seen)
        hud.new_chat_button.click()
        seen.assert_called_once()


class TestSessionPicker:
    def test_the_picker_lists_recent_sessions(self, make_hud, store):
        store.new_session("excel.exe", title="pivot tables")
        store.new_session("photoshop.exe", title="layer masks")

        hud = make_hud(store=store)
        hud.open_picker()

        assert hud.picker().row_count() == 2

    def test_the_picker_search_filters(self, make_hud, store):
        """Sessions accumulate silently; a flat list stops being navigable well before that."""
        store.new_session("excel.exe", title="pivot tables")
        store.new_session("photoshop.exe", title="layer masks")

        hud = make_hud(store=store)
        hud.open_picker()
        hud.picker().set_search("pivot")

        assert hud.picker().row_count() == 1

    def test_choosing_a_session_emits_its_id(self, make_hud, store, mocker):
        session_id = store.new_session("excel.exe", title="pivot tables")
        hud = make_hud(store=store)
        seen = mocker.Mock()
        hud.sig_open_session.connect(seen)
        hud.open_picker()

        # The whole row is the click target now, not a button inside it -- see `_SessionRow`.
        hud.picker()._rows.itemAt(0).widget().clicked.emit()
        seen.assert_called_once_with(session_id)

    def test_deleting_from_the_picker_removes_the_session(self, make_hud, store):
        store.new_session("excel.exe", title="doomed")
        hud = make_hud(store=store)
        hud.open_picker()

        hud.picker()._rows.itemAt(0).widget().delete_button.click()

        assert store.sessions() == []
        assert hud.picker().row_count() == 0

    def test_the_picker_is_empty_without_a_store(self, make_hud):
        hud = make_hud()
        hud.open_picker()
        assert hud.picker().row_count() == 0

    def test_new_chat_in_the_picker_emits_the_signal(self, make_hud, store, mocker):
        hud = make_hud(store=store)
        seen = mocker.Mock()
        hud.sig_new_session.connect(seen)
        hud.open_picker()
        hud.picker()._new_chat()
        seen.assert_called_once()


class TestAutoHide:
    def test_auto_hide_fires_after_idle_and_cancels_on_activity(self, make_hud):
        from PyQt6.QtTest import QTest
        from sessions import ChatMessage

        import theme

        hud = make_hud(autohide_seconds=30)
        hud.show()
        hud.append(ChatMessage(role="user", text="a question"))
        assert hud.idle_timer_running()

        hud._on_idle_timeout()
        QTest.qWait(theme.DUR_EXIT + 200)
        assert not hud.isVisible()

        hud.set_state("listening")
        assert hud.isVisible()
        assert hud.idle_timer_running()

    def test_pin_defeats_auto_hide(self, make_hud):
        hud = make_hud(autohide_seconds=30)
        hud.show()
        hud.set_pinned(True)

        assert not hud.idle_timer_running()
        hud.note_activity()
        assert not hud.idle_timer_running()

        hud._on_idle_timeout()
        assert hud.isVisible()

    def test_unpinning_restarts_the_countdown(self, make_hud):
        hud = make_hud(autohide_seconds=30)
        hud.set_pinned(True)
        hud.set_pinned(False)
        assert hud.idle_timer_running()

    def test_zero_seconds_means_never(self, make_hud):
        hud = make_hud(autohide_seconds=0)
        hud.note_activity()
        assert not hud.idle_timer_running()

    def test_the_pin_button_drives_the_pin(self, make_hud):
        hud = make_hud(autohide_seconds=30)
        hud.pin_button.setChecked(True)
        assert hud.pinned is True

    def test_autohide_default_comes_from_the_setting(self, mocker, qt_app, tmp_path):
        import config

        from chat_hud import ChatHud

        mocker.patch.object(
            config, "resolve_bounded_int_setting",
            lambda name, default, minimum, maximum: 12)
        hud = ChatHud(exclude=lambda hwnd: True,
                      positions_path=tmp_path / "p.json")
        try:
            assert hud.autohide_seconds() == 12
        finally:
            hud.hide()


class TestCaptionCoexistence:
    def test_the_caption_defers_while_the_transcript_is_visible(self, make_hud):
        """§6.1 — two copies of the same words on one screen is noise."""
        hud = make_hud()
        hud.show()
        assert hud.is_showing_transcript() is True

        # A collapsed panel shows no transcript, so the caption goes back to doing its job.
        hud.set_collapsed(True)
        assert hud.is_showing_transcript() is False

        hud.set_collapsed(False)
        hud.hide()
        assert hud.is_showing_transcript() is False


class TestCollapse:
    """The third state between open and gone, and the one people actually asked for."""

    def test_collapsing_keeps_the_bar_and_drops_the_body(self, make_hud):
        hud = make_hud()
        hud.show()
        full_width, full_height = hud.width(), hud.height()

        hud.set_collapsed(True)

        assert hud.collapsed is True
        assert hud.width() == full_width, "collapsing must not move or narrow the panel"
        assert hud.height() < full_height
        assert not hud._scroll.isVisibleTo(hud)
        assert not hud._footer.isVisibleTo(hud)
        # Two stray hairlines under a collapsed bar read as a rendering fault.
        assert all(not line.isVisibleTo(hud) for line in hud._hairlines)

    def test_expanding_restores_the_previous_height(self, make_hud):
        hud = make_hud()
        hud.show()
        hud._resize_to(700, 500)
        expanded = hud.height()

        hud.set_collapsed(True)
        hud.set_collapsed(False)

        assert hud.collapsed is False
        assert hud.height() == expanded
        assert hud._scroll.isVisibleTo(hud)
        assert hud._footer.isVisibleTo(hud)

    def test_the_collapsed_bar_fits_its_own_header(self, make_hud):
        """The collapsed height is derived from the live margins, not a literal.

        It was ``STATE_STRIP_HEIGHT + header + 2``, where the ``2`` stood for the body's 1px
        margins. Adding the 5px resize gutter put another 10px between the window edge and the
        header, so the window came out 10px short: the body could not fit the header, and the
        header spilled past the body's bottom edge and clipped the four buttons in it.
        """
        hud = make_hud()
        hud.show()
        hud.set_collapsed(True)

        assert hud.height() == hud._bar_height()
        header = hud._header.geometry()
        assert header.bottom() <= hud._body.geometry().bottom(), (
            "the header overflows the body, which is what clips the buttons")
        for name, button in (("pin", hud.pin_button), ("up", hud.up_button),
                             ("down", hud.down_button), ("close", hud.close_button)):
            assert button.geometry().bottom() <= header.height(), (
                f"the {name} button is clipped by the collapsed bar")

    def test_the_bar_height_follows_the_margins(self, make_hud):
        """A margin change must not be able to reintroduce the clipping."""
        from chat_hud import RESIZE_MARGIN, STATE_STRIP_HEIGHT

        hud = make_hud()
        hud.show()
        body_margins = hud._body.layout().contentsMargins()

        assert hud._bar_height() == (
            RESIZE_MARGIN * 2
            + body_margins.top() + body_margins.bottom()
            + STATE_STRIP_HEIGHT
            + hud._header.height()
        )

    def test_the_glyph_says_what_the_button_will_do_next(self, make_hud):
        hud = make_hud()

        hud.set_collapsed(True)
        assert "Expand" in hud.collapse_button.toolTip()
        hud.set_collapsed(False)
        assert "Collapse" in hud.collapse_button.toolTip()


class TestDirectionButtons:
    """Two arrows, not one toggle.

    A toggle whose glyph flipped with the panel's position meant the *direction was chosen for
    you*: a panel low on the screen could only ever open upwards. These say what they do.
    """

    def _hud(self, make_hud):
        from PyQt6.QtCore import QRect

        hud = make_hud(screen_geometry_fn=lambda: QRect(0, 0, 1920, 1080))
        hud.show()
        hud.move(300, 500)
        return hud

    def test_there_are_four_visible_header_controls(self, make_hud):
        hud = self._hud(make_hud)

        visible = [b for b in (hud.pin_button, hud.up_button, hud.down_button,
                               hud.close_button) if b.isVisibleTo(hud)]
        assert len(visible) == 4
        # The old single toggle survives as the programmatic entry point, but not on screen.
        assert not hud.collapse_button.isVisibleTo(hud)

    def test_the_arrows_point_the_way_they_open(self, make_hud):
        hud = self._hud(make_hud)
        assert hud.up_button.text() == "\u2303"
        assert hud.down_button.text() == "\u2304"

    def test_up_opens_upwards_from_collapsed(self, make_hud):
        hud = self._hud(make_hud)
        hud.set_collapsed(True)

        hud.up_button.click()

        assert hud.collapsed is False
        assert hud.expand_upwards is True

    def test_down_opens_downwards_from_collapsed(self, make_hud):
        hud = self._hud(make_hud)
        hud.set_collapsed(True)

        hud.down_button.click()

        assert hud.collapsed is False
        assert hud.expand_upwards is False

    def test_either_arrow_opens_regardless_of_where_the_panel_sits(self, make_hud):
        """The point of two buttons: position no longer dictates the only available direction."""
        low = self._hud(make_hud)
        low.move(300, 950)
        low.set_collapsed(True)
        low.down_button.click()
        assert low.expand_upwards is False

        high = self._hud(make_hud)
        high.move(300, 20)
        high.set_collapsed(True)
        high.up_button.click()
        assert high.expand_upwards is True

    def test_pressing_the_same_arrow_again_collapses(self, make_hud):
        """What a user reaches for after reading the answer."""
        hud = self._hud(make_hud)
        hud.set_collapsed(True)
        hud.up_button.click()
        assert hud.collapsed is False

        hud.up_button.click()

        assert hud.collapsed is True

    def test_pressing_the_other_arrow_moves_the_transcript_rather_than_hiding_it(
            self, make_hud):
        """Plainly a request to move it, not to put it away."""
        hud = self._hud(make_hud)
        hud.set_collapsed(True)
        hud.up_button.click()
        assert (hud.collapsed, hud.expand_upwards) == (False, True)

        hud.down_button.click()

        assert hud.collapsed is False
        assert hud.expand_upwards is False

    def test_the_active_arrow_offers_to_collapse(self, make_hud):
        hud = self._hud(make_hud)
        hud.set_collapsed(True)
        hud.down_button.click()

        assert hud.down_button.toolTip() == "Collapse to the bar"
        assert hud.up_button.toolTip() == "Open the transcript upwards"


class TestFooterDrag:
    def test_the_footer_is_a_drag_handle_too(self, make_hud):
        """With the panel opening upwards the bar can be at the bottom of the screen, so the
        header is no longer always the nearest grab point."""
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        start = (hud.x(), hud.y())

        # The footer's handlers are the header's, so exercising them proves the wiring.
        assert hud._footer.mousePressEvent == hud._header_press
        assert hud._footer.mouseMoveEvent == hud._header_move
        assert hud._footer.mouseReleaseEvent == hud._header_release

        hud._begin_drag(QPoint(hud.x() + 30, hud.y() + hud.height() - 10))
        hud._drag_to(QPoint(hud.x() + 130, hud.y() + hud.height() + 40))
        hud._end_drag()

        assert (hud.x(), hud.y()) != start

    def test_the_status_label_does_not_punch_a_hole_in_the_drag_strip(self, make_hud):
        """Same reason the brand mark is mouse-transparent: a label that eats events puts a
        dead spot in the middle of a handle."""
        from PyQt6.QtCore import Qt

        hud = make_hud()
        assert hud._status_label.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def test_the_footer_shows_a_move_cursor(self, make_hud):
        from PyQt6.QtCore import Qt

        hud = make_hud()
        assert hud._footer.cursor().shape() == Qt.CursorShape.SizeAllCursor


class TestExpandDirection:
    """The panel opens downwards near the top of the screen and upwards near the bottom.

    Menu behaviour, and the arrow says which. Before this, a panel dragged low expanded straight
    off the bottom edge and Qt's clamping made it look like the whole thing teleported.
    """

    def _hud_at(self, make_hud, y):
        from PyQt6.QtCore import QRect

        hud = make_hud(screen_geometry_fn=lambda: QRect(0, 0, 1920, 1080))
        hud.show()
        hud.move(200, y)
        return hud

    def test_a_panel_near_the_top_opens_downwards(self, make_hud):
        hud = self._hud_at(make_hud, 40)
        assert hud._should_expand_upwards() is False

    def test_a_panel_near_the_bottom_opens_upwards(self, make_hud):
        hud = self._hud_at(make_hud, 900)
        assert hud._should_expand_upwards() is True

    def test_the_arrow_points_where_the_body_will_go(self, make_hud):
        """Collapsed, the glyph is an arrow towards the transcript, not a state indicator."""
        low = self._hud_at(make_hud, 900)
        low.set_collapsed(True)
        assert low.expand_upwards is True
        assert low.collapse_button.text() == "\u2303"       # up
        assert low.collapse_button.toolTip() == "Expand upwards"

        high = self._hud_at(make_hud, 40)
        high.set_collapsed(True)
        assert high.expand_upwards is False
        assert high.collapse_button.text() == "\u2304"      # down
        assert high.collapse_button.toolTip() == "Expand downwards"

    def test_collapsing_never_moves_the_bar(self, make_hud):
        """It has to stay under the pointer that clicked it, in both directions."""
        for y in (40, 900):
            hud = self._hud_at(make_hud, y)
            before = (hud.x(), hud.y())
            hud.set_collapsed(True)
            assert (hud.x(), hud.y()) == before, f"the bar moved when collapsed at y={y}"

    def test_expanding_upwards_keeps_the_bar_where_it_is(self, make_hud):
        """The bar's bottom edge is the fixed point, so the body appears above it."""
        hud = self._hud_at(make_hud, 900)
        hud.set_collapsed(True)
        bar_bottom = hud.y() + hud.height()

        hud.set_collapsed(False)

        assert hud.y() < 900, "the window's top edge must move up"
        assert abs((hud.y() + hud.height()) - bar_bottom) <= 2

    def test_expanding_downwards_keeps_the_top_edge_where_it_is(self, make_hud):
        hud = self._hud_at(make_hud, 40)
        hud.set_collapsed(True)
        top = hud.y()

        hud.set_collapsed(False)

        assert hud.y() == top

    def test_the_direction_is_decided_once_and_remembered(self, make_hud):
        """Deciding again on expand would let a panel dragged near an edge while collapsed
        expand the other way and jump."""
        hud = self._hud_at(make_hud, 900)
        hud.set_collapsed(True)
        assert hud.expand_upwards is True

        hud.move(200, 40)  # dragged to the top while collapsed
        assert hud.expand_upwards is True, "the recorded direction must not be recomputed"

    def test_no_screen_falls_back_to_downwards(self, make_hud):
        """The safe direction: a panel clipped at the bottom is still usable, one that opens
        off the top of the screen is not."""
        hud = make_hud(screen_geometry_fn=lambda: None)
        assert hud._should_expand_upwards() is False

    def test_a_broken_screen_lookup_falls_back_to_downwards(self, make_hud):
        def boom():
            raise RuntimeError("monitor unplugged")

        hud = make_hud(screen_geometry_fn=boom)
        assert hud._should_expand_upwards() is False

    def test_collapsing_keeps_the_position_the_user_chose(self, make_hud):
        """The removed minimise re-centred the panel at the top of the screen, which read as a
        rendering fault rather than a deliberate collapse. Collapse leaves it alone."""
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        hud._begin_drag(QPoint(hud.x() + 10, hud.y() + 10))
        hud._drag_to(QPoint(hud.x() + 160, hud.y() + 90))
        hud._end_drag()
        moved = (hud.x(), hud.y())

        hud.set_collapsed(True)

        assert (hud.x(), hud.y()) == moved

    def test_the_button_and_the_method_stay_in_step(self, make_hud):
        hud = make_hud()
        hud.collapse_button.setChecked(True)
        assert hud.collapsed is True
        hud.set_collapsed(False)
        assert hud.collapse_button.isChecked() is False


class TestHeaderControls:
    def test_the_middle_of_the_header_is_draggable(self, make_hud):
        """The session button used to take `stretch=1` and span the whole header.

        A QPushButton swallows mouse events, so the entire area a user would naturally grab was
        covered by a button and dragging did not work anywhere sensible. The button is gone from
        the header entirely now, and the stretch belongs to empty space -- which is what a title
        bar is.
        """
        hud = make_hud()
        hud.show()

        assert not hud._session_label.isVisibleTo(hud), (
            "the session name is on the right-click menu now, not in the bar")
        # Somewhere left of the window controls there must be bare header, not a child widget.
        header = hud._header
        midpoint = header.width() // 2
        hit = header.childAt(midpoint, header.height() // 2)
        assert hit is None or not hit.isEnabled(), (
            f"{type(hit).__name__} covers the drag area")

    def test_the_session_picker_is_still_reachable(self, make_hud):
        """Removing the label must not remove the capability."""
        hud = make_hud()

        # The footer's pill for a new one...
        assert hud.new_chat_button.isVisibleTo(hud)
        # ...and the right-click menu for switching to an existing one.
        source = __import__("inspect").getsource(type(hud).contextMenuEvent)
        assert "Switch session" in source
        assert "self.open_picker" in source

    def test_dragging_moves_the_panel(self, make_hud):
        from PyQt6.QtCore import QPoint

        hud = make_hud()
        hud.show()
        start = (hud.x(), hud.y())

        hud._begin_drag(QPoint(hud.x() + 20, hud.y() + 10))
        hud._drag_to(QPoint(hud.x() + 140, hud.y() + 70))
        hud._end_drag()

        assert (hud.x(), hud.y()) != start

    def test_every_window_control_is_visible_at_rest(self, make_hud):
        """They were transparent with TEXT_SECONDARY glyphs -- users could not find them."""
        import theme

        hud = make_hud()
        for button in (hud.pin_button, hud.collapse_button, hud.close_button):
            assert button.text().strip(), "a control with no glyph is invisible"
            style = button.styleSheet()
            # Opaque, not a translucent wash: a translucent fill composites over whatever the
            # parent painted, which on an unstyled Qt container is the near-white palette
            # default -- the bug that made the session list unreadable.
            assert theme.PANEL_RAISED in style, "no resting background"
            assert "rgba(" not in style, "translucent fills are not safe here"
            assert theme.TEXT_PRIMARY in style, "glyph too dim to read"

    def test_close_gets_a_red_hover_of_its_own(self, make_hud):
        import theme

        hud = make_hud()
        assert theme.DANGER in hud.close_button.styleSheet()
        assert theme.DANGER not in hud.collapse_button.styleSheet()

    def test_the_autohide_pill_says_what_it_will_do(self, make_hud):
        """Two words, and both of them are the action.

        It used to read "hides after 45s · keep open" -- three facts and a separator crammed
        into a pill: a duration nobody can act on, the current behaviour, and the thing the
        button does. The duration moved to the tooltip and to Settings, where it is changeable.
        """
        hud = make_hud(autohide_seconds=45)

        assert hud._autohide_label.text() == "Keep open"
        assert "45 seconds" in hud._autohide_label.toolTip()

        hud.set_pinned(True)
        assert hud._autohide_label.text() == "Staying open"
        assert "stays until you hide it" in hud._autohide_label.toolTip()

        hud.set_pinned(False)
        assert hud._autohide_label.text() == "Keep open"

    def test_the_pill_is_a_capsule_not_a_rounded_rectangle(self, make_hud):
        """``RADIUS_PILL`` is 999 and Qt clamps it to half the *shorter* side, which only gives
        a capsule when the height is known. Left to the layout the two pills came out different
        heights and read as rectangles."""
        from chat_hud import PILL_HEIGHT

        hud = make_hud()

        for pill in (hud._autohide_label, hud.new_chat_button):
            assert pill.height() == PILL_HEIGHT
            assert f"border-radius: {PILL_HEIGHT // 2}px" in pill.styleSheet()

    def test_the_primary_action_is_the_one_that_stands_out(self, make_hud):
        """Two identical pills side by side make the user read both to find the one they want."""
        import theme

        hud = make_hud()

        assert theme.ACCENT in hud.new_chat_button.styleSheet()
        assert theme.PANEL_HOVER in hud.new_chat_button.styleSheet()
        assert theme.PANEL_RAISED in hud._autohide_label.styleSheet()
        assert theme.PANEL_HOVER not in hud._autohide_label.styleSheet()

    def test_a_never_hiding_panel_says_so_in_the_tooltip(self, make_hud):
        hud = make_hud(autohide_seconds=0)
        assert "already stays" in hud._autohide_label.toolTip()

    def test_clicking_the_footer_label_toggles_the_pin(self, make_hud):
        hud = make_hud(autohide_seconds=45)
        assert hud.pinned is False

        hud._autohide_label.click()

        assert hud.pinned is True
        assert hud.pin_button.isChecked() is True


class TestPickerPresentation:
    """The picker was sized for a 300px column hanging off a header label that no longer exists.

    At that width a row's second line -- the app name and timestamp -- was clipped, so the list
    was there but unreadable.
    """

    def _hud_with_sessions(self, make_hud, tmp_path, count=4):
        import sessions

        store = sessions.SessionStore(index_db_path=tmp_path / "index.db")
        for index in range(count):
            session_id = store.new_session(app_name=f"app{index}.exe")
            store.add_message(session_id, sessions.ChatMessage(
                role="user", text=f"a question about app{index}"))
        hud = make_hud(store=store)
        hud.show()
        return hud

    def test_both_lines_of_every_row_fit_inside_it(self, make_hud, tmp_path):
        """Asserted per label, in pixels, because three button-based attempts each clipped the
        text for a different reason.

        A ``QPushButton``'s height is governed by the application stylesheet's ``min-height``
        (which overrides ``setMinimumHeight``); its own stylesheet ``min-height`` applies to the
        content box rather than the widget; and a styled ``QPushButton`` ignores a child layout
        entirely when computing its size hint, squeezing two labels to 5px each. Every attempt got
        closer and still cut the descenders. The row is a ``QFrame`` now, whose layout's size hint
        *is* its size hint.
        """
        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        picker = hud.picker()

        assert picker.row_count() == 4
        for index in range(picker.row_count()):
            row = picker._rows.itemAt(index).widget()
            for name, label in (("title", row.title_label),
                                ("subtitle", row.subtitle_label)):
                needed = label.fontMetrics().height()
                assert label.height() >= needed, (
                    f"the {name} label has {label.height()}px for {needed}px of text")
                assert label.y() >= 0, f"the {name} label starts above the row"
                assert label.y() + label.height() <= row.height(), (
                    f"the {name} label extends past the bottom of the row")

    def test_a_row_reports_the_height_it_needs(self, make_hud, tmp_path):
        """And the picker measures a real row rather than calculating one -- two rounds of
        arithmetic against Qt's stylesheet box model both disagreed with the layout."""
        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        picker = hud.picker()
        row = picker._rows.itemAt(0).widget()

        assert row.height() == row.sizeHint().height()
        assert picker.row_height() == row.sizeHint().height()

    def test_the_whole_row_is_one_click_target(self, make_hud, tmp_path):
        """Including the text: the labels are mouse-transparent, so clicking a title opens the
        session rather than doing nothing."""
        from PyQt6.QtCore import Qt

        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        row = hud.picker()._rows.itemAt(0).widget()

        for label in (row.title_label, row.subtitle_label):
            assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # And the frame carries WA_Hover, without which a QSS :hover rule never fires on it.
        assert row.testAttribute(Qt.WidgetAttribute.WA_Hover)

    def test_clicking_a_row_opens_that_session(self, make_hud, tmp_path):
        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        opened = []
        hud.sig_open_session.connect(opened.append)
        row = hud.picker()._rows.itemAt(0).widget()

        row.clicked.emit()

        assert opened == [row.session_id]

    def test_the_picker_is_wide_enough_to_read(self, make_hud, tmp_path):
        from chat_hud import PICKER_WIDTH

        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()

        assert PICKER_WIDTH == 380
        assert hud.picker().width() == PICKER_WIDTH

    def test_the_picker_fits_inside_the_panel(self, make_hud, tmp_path):
        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        picker = hud.picker()

        assert picker.x() + picker.width() <= hud.width()
        assert picker.y() + picker.height() <= hud.height()

    def test_the_picker_is_centred_not_pinned_left(self, make_hud, tmp_path):
        """It used to hang off a header label at the left edge. A 380px popover pinned left in a
        660px panel looks like it has come unmoored."""
        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        picker = hud.picker()

        expected = (hud.width() - picker.width()) // 2
        assert abs(picker.x() - expected) <= 1

    def test_a_long_list_scrolls_rather_than_growing_forever(self, make_hud, tmp_path):
        from chat_hud import PICKER_MAX_ROWS

        hud = self._hud_with_sessions(make_hud, tmp_path, count=PICKER_MAX_ROWS + 6)
        hud.open_picker()
        picker = hud.picker()

        # Row pitch, not row height: the spacing between rows counts, or the last visible row is
        # half-shown and reads as a rendering fault rather than as "scroll for more".
        pitch = picker.row_height() + picker._rows.spacing()
        cap = PICKER_MAX_ROWS * pitch

        # A **maximum**, not a fixed height. A fixed one could not give space back when
        # `_position_picker` clamped the popover to the room below the header, so the layout
        # overflowed its own frame and pushed the New chat row out through the bottom -- which is
        # what "the new chat is overlapping the text behind it" was.
        assert picker._rows_scroll.maximumHeight() == cap
        assert picker._rows_scroll.height() <= cap
        assert picker.height() <= hud.height()

    def test_the_action_row_stays_inside_the_popover(self, make_hud, tmp_path):
        """The overlap, asserted as geometry rather than described.

        With a fixed-height list the New chat button was laid out past the popover's bottom edge and
        drew over the session rows -- and it had a transparent background, so their text showed
        through it.
        """
        from chat_hud import PICKER_MAX_ROWS

        hud = self._hud_with_sessions(make_hud, tmp_path, count=PICKER_MAX_ROWS + 6)
        hud.open_picker()
        picker = hud.picker()

        new_chat = next(
            button for button in picker.findChildren(type(picker.close_button))
            if "New chat" in button.text())
        assert new_chat.geometry().bottom() <= picker.rect().bottom()
        assert not new_chat.geometry().intersects(picker._rows_scroll.geometry())
        # And it is opaque, so nothing can show through it wherever it ends up.
        assert "background: transparent" not in new_chat.styleSheet()

    def test_the_picker_can_be_closed_without_changing_session(self, make_hud, tmp_path):
        """Picking a session or starting a new one both change what you are looking at. Opening the
        switcher to check what else is there and then staying put had no way to be expressed."""
        hud = self._hud_with_sessions(make_hud, tmp_path, count=3)
        hud.open_picker()
        picker = hud.picker()
        assert picker.isVisible()

        opened, created = [], []
        hud.sig_open_session.connect(opened.append)
        hud.sig_new_session.connect(lambda: created.append(1))

        picker.close_button.click()

        assert not picker.isVisible()
        assert opened == [] and created == []

    def test_an_empty_list_says_so_instead_of_showing_a_gap(self, make_hud):
        hud = make_hud()  # no store
        hud.show()
        hud.open_picker()
        picker = hud.picker()

        assert picker.row_count() == 0
        assert picker._empty.isVisibleTo(picker)
        assert not picker._rows_scroll.isVisibleTo(picker)

    def test_the_rows_actually_render_dark(self, make_hud, tmp_path):
        """A **pixel** test, because this class of bug has now slipped past twice.

        Asserting the stylesheet says "dark" is not enough: Qt composites a translucent
        background over whatever the parent painted, and an unstyled ``QScrollArea`` *viewport*
        paints the palette's default -- near-white on Windows. The stylesheet was correct both
        times and the rows still came out white. Rendering and sampling is the only check that
        would have caught it.
        """
        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        picker = hud.picker()

        image = picker.grab().toImage()
        # Down the middle of the list region, skipping the heading and the search field.
        top = picker._rows_scroll.y()
        for offset in (8, 30, 60):
            y = top + offset
            if y >= image.height():
                continue
            colour = image.pixelColor(image.width() // 2, y)
            brightness = (colour.red() + colour.green() + colour.blue()) / 3
            assert brightness < 90, (
                f"the session list renders light at y={y} "
                f"(#{colour.red():02X}{colour.green():02X}{colour.blue():02X}) -- "
                "unreadable against TEXT_PRIMARY")

    def test_no_surface_in_the_list_is_translucent(self, make_hud, tmp_path):
        """The rule that follows from the above: on this panel, fills are opaque.

        A translucent fill is only safe when you know what is behind it, and inside a scroll
        area you do not.
        """
        from PyQt6.QtWidgets import QPushButton

        hud = self._hud_with_sessions(make_hud, tmp_path)
        hud.open_picker()
        picker = hud.picker()

        for widget in (picker._rows_scroll, picker._rows_host):
            assert "rgba(" not in widget.styleSheet()
        for index in range(picker.row_count()):
            host = picker._rows.itemAt(index).widget()
            for button in host.findChildren(QPushButton):
                assert "rgba(" not in button.styleSheet(), (
                    "a row control uses a translucent fill")

    def test_the_picker_is_warm_like_the_panel_it_sits_on(self, make_hud):
        """It was ``BG_RAISED``, part of the cool ramp -- a blue-black popover on a warm-black
        panel."""
        import theme

        hud = make_hud()
        hud.open_picker()

        style = hud.picker().styleSheet()
        assert theme.panel_gradient() in style
        assert theme.BG_RAISED not in style

    def test_menus_are_warm_too(self):
        """A context menu opened on the panel sits directly on top of it."""
        import theme

        qss = theme.build_qss()
        menu_rule = qss[qss.index("QMenu {"):]
        assert theme.panel_gradient() in menu_rule
        assert theme.raised_gradient() not in menu_rule


class TestThemeDiscipline:
    def test_the_body_is_shaded_rather_than_flat(self, make_hud):
        """A floating panel over someone else's application has to read as an object above it.
        A flat fill reads as a hole."""
        import theme

        hud = make_hud()
        # `panel_gradient`, not `surface_gradient`: the panel is warm all the way through.
        # The cool ramp is right for cards inside a large neutral window, but next to this
        # panel's warm bezel a cool interior reads as blue-black -- two different blacks a
        # centimetre apart, which is worse than either choice alone.
        assert theme.panel_gradient() in hud._body.styleSheet()
        assert theme.surface_gradient() not in hud._body.styleSheet()
        assert theme.SHEEN in hud._body.styleSheet()
        # The header gets the shell title bar's warm-left-edge treatment, so the two chromes
        # are recognisably the same material rather than two dark greys.
        assert theme.chrome_tint() in hud._header.styleSheet()
        assert theme.chrome_gradient() not in hud._header.styleSheet()

    def test_the_frame_carries_a_warm_edge(self, make_hud):
        """The resize gutter is not a black gap -- it is the panel's bezel, and it is tinted."""
        import theme

        hud = make_hud()
        assert theme.TINT_EDGE in hud.styleSheet()

    def test_there_is_exactly_one_visible_frame(self, make_hud):
        """The panel had two: the outer bezel and the body's own border, reading as a box inside
        a box. The border belongs on the outer edge, where a window's border goes; the body is
        separated from the bezel by tone alone."""
        import theme

        hud = make_hud()

        assert f"border: 1px solid {theme.BORDER_STRONG}" in hud.styleSheet()
        assert "border: none" in hud._body.styleSheet()
        # The lit top edge survives -- that is a highlight, not a frame.
        assert theme.SHEEN in hud._body.styleSheet()

    def test_the_painted_corner_matches_the_clipped_corner(self, make_hud):
        """``SetWindowRgn`` clips the window to a round-rect. A square border under a rounded
        region gets its corners sliced off."""
        import theme

        hud = make_hud()
        assert f"border-radius: {theme.RADIUS_CARD}px" in hud.styleSheet()

    def test_the_hairlines_lead_with_the_accent(self, make_hud):
        """Same divider treatment as under the shell's title bar."""
        import theme

        hud = make_hud()
        for line in hud._hairlines:
            assert theme.accent_rule() in line.styleSheet()

    def test_the_module_contains_no_literal_colours(self):
        """§9.1 rule 3. A literal #1a1a1a next to BG_ELEVATED is invisible in review."""
        import re
        from pathlib import Path

        import chat_hud

        source = Path(chat_hud.__file__).read_text(encoding="utf-8")
        assert re.findall(r"#[0-9A-Fa-f]{6}\b", source) == []
        # A bare `rgba(` with a number in it is a literal; `theme.rgba(theme.X, 0.6)` is the
        # sanctioned way to get an opacity variant of a palette colour. The original guard
        # banned the substring outright, which also banned the correct call.
        assert re.findall(r"(?<!theme\.)rgba\(\s*\d", source) == []

    def test_the_module_contains_no_literal_durations(self):
        """Every animation goes through theme.duration so reduced motion is honoured."""
        import re
        from pathlib import Path

        import chat_hud

        source = Path(chat_hud.__file__).read_text(encoding="utf-8")
        assert re.findall(r"setDuration\(\s*\d", source) == []

    def test_state_colours_come_from_the_theme(self):
        import chat_hud
        import theme

        assert set(chat_hud._STATE_COLOURS.values()) == {
            theme.SUCCESS, theme.WARNING, theme.ACCENT, "transparent"}


class TestHelpers:
    @pytest.mark.parametrize("chord,expected", [
        ("ctrl+alt+space", "Ctrl+Alt+Space"),
        ("ctrl+shift+f9", "Ctrl+Shift+F9"),
        ("", ""),
    ])
    def test_format_hotkey_is_presentation_only(self, chord, expected):
        from chat_hud import format_hotkey
        assert format_hotkey(chord) == expected

    def test_configured_hotkey_reads_the_setting(self, mocker):
        import chat_hud
        import config

        mocker.patch.object(config, "resolve_setting", lambda name, default: "ctrl+alt+q")
        assert chat_hud.configured_hotkey() == "ctrl+alt+q"

    def test_configured_hotkey_survives_a_broken_config(self, mocker):
        import chat_hud
        import config

        mocker.patch.object(config, "resolve_setting", side_effect=RuntimeError("keyring"))
        assert chat_hud.configured_hotkey() == "ctrl+alt+space"

    @pytest.mark.parametrize("seconds,expected", [
        (5, "just now"), (120, "2m ago"), (7200, "2h ago"), (172800, "2d ago"),
    ])
    def test_relative_time(self, seconds, expected):
        from datetime import datetime, timedelta

        from chat_hud import relative_time

        now = datetime(2025, 6, 1, 12, 0, 0)
        stamp = (now - timedelta(seconds=seconds)).isoformat()
        assert relative_time(stamp, now) == expected

    def test_relative_time_tolerates_rubbish(self):
        from chat_hud import relative_time
        assert relative_time("not a date") == ""

    def test_the_hud_never_imports_app(self):
        """§9.1: if the HUD needs NimbusApp, the seam is wrong -- and it becomes untestable."""
        import re
        from pathlib import Path

        import chat_hud
        import sessions

        for module in (chat_hud, sessions):
            source = Path(module.__file__).read_text(encoding="utf-8")
            code = "\n".join(
                line for line in source.splitlines()
                if not line.lstrip().startswith(("#", '"', "'"))
            )
            assert not re.search(r"^\s*(?:from|import)\s+app\b", code, re.MULTILINE)
