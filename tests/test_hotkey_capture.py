"""Tests for the hotkey capture widget (T2-7).

The feature adds no validation logic -- `hotkey.parse_hotkey` already owns the grammar, the
normalised display form, and the tailored conflict messages. So the risk is concentrated in
one place: **the translation between two key-code vocabularies.** Qt's key codes and
pynput's key objects are unrelated, and a mistake there produces a chord that records
cleanly in Settings and then never fires at runtime.

`qt_key_event_to_hotkey_string` is therefore a pure function over primitives, and most of
this file exercises it directly rather than through widgets.

Two mappings were confirmed against live Qt values rather than assumed, and both would have
been silent bugs:

* Windows reports **Shift+Tab as `Key_Backtab`**, not `Key_Tab`.
* Windows reports **AltGr as Ctrl+Alt**, which happens to be correct here because
  `hotkey._is_alt` already lumps `alt_gr` with `alt`.
"""

import pytest


@pytest.fixture(scope="module")
def qt_app():
    """One QApplication for the module. Qt requires it to exist before QKeyEvent/QWidget."""
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def mods(qt_app):
    from PyQt6.QtCore import Qt
    return Qt.KeyboardModifier


@pytest.fixture
def keys(qt_app):
    from PyQt6.QtCore import Qt
    return Qt.Key


def _to_chord(key, modifiers):
    from settings_dialog import qt_key_event_to_hotkey_string
    return qt_key_event_to_hotkey_string(key, modifiers)


class TestQtKeyMapping:
    def test_captured_chord_normalises_to_parse_hotkey_format(self, keys, mods):
        """The core contract: whatever this emits, parse_hotkey must accept."""
        from hotkey import parse_hotkey
        chord = _to_chord(
            keys.Key_Space, mods.ControlModifier | mods.AltModifier)
        assert chord == "ctrl+alt+space"
        assert parse_hotkey(chord).display == "ctrl+alt+space"

    @pytest.mark.parametrize("key_name,expected", [
        ("Key_Space", "space"), ("Key_Return", "enter"), ("Key_Enter", "enter"),
        ("Key_Tab", "tab"), ("Key_A", "a"), ("Key_Z", "z"),
        ("Key_0", "0"), ("Key_9", "9"), ("Key_F1", "f1"), ("Key_F12", "f12"),
    ])
    def test_trigger_keys_map_correctly(self, keys, mods, key_name, expected):
        chord = _to_chord(getattr(keys, key_name),
                          mods.ControlModifier | mods.AltModifier)
        assert chord == f"ctrl+alt+{expected}"

    def test_shift_tab_reports_backtab_and_still_maps_to_tab(self, keys, mods):
        """Windows emits Key_Backtab for Shift+Tab. Without handling it, a shift+tab chord
        would be silently unrecordable -- the widget would just appear to ignore the key."""
        chord = _to_chord(keys.Key_Backtab,
                          mods.ControlModifier | mods.ShiftModifier)
        assert chord is not None
        assert chord.endswith("tab")

    def test_alt_gr_arrives_as_ctrl_alt_which_matches_the_listener(self, keys, mods):
        """AltGr is reported by Windows as Ctrl+Alt. That needs no correction: hotkey._is_alt
        lumps alt_gr with alt, so ctrl+alt+<key> is exactly what fires at runtime."""
        from hotkey import parse_hotkey
        chord = _to_chord(keys.Key_J, mods.ControlModifier | mods.AltModifier)
        assert chord == "ctrl+alt+j"
        combo = parse_hotkey(chord)
        assert combo.modifiers == frozenset({"ctrl", "alt"})

    @pytest.mark.parametrize("key_name", [
        "Key_Control", "Key_Alt", "Key_Shift", "Key_Meta", "Key_AltGr",
    ])
    def test_bare_modifier_returns_none_so_capture_keeps_waiting(self, keys, mods, key_name):
        """The user holds Ctrl and Alt before reaching Space; the widget must not guess."""
        assert _to_chord(getattr(keys, key_name), mods.ControlModifier) is None

    @pytest.mark.parametrize("key_name", [
        "Key_F13", "Key_unknown", "Key_Home", "Key_VolumeUp", "Key_Plus",
    ])
    def test_unbindable_keys_return_none(self, keys, mods, key_name):
        key = getattr(keys, key_name, None)
        if key is None:
            pytest.skip(f"{key_name} not present in this Qt build")
        assert _to_chord(key, mods.ControlModifier | mods.AltModifier) is None

    def test_modifier_order_is_deterministic(self, keys, mods):
        """Always ctrl, alt, shift -- matching HotkeyCombo.display, so a captured chord and
        a saved chord render identically."""
        chord = _to_chord(
            keys.Key_K,
            mods.ShiftModifier | mods.AltModifier | mods.ControlModifier)
        assert chord == "ctrl+alt+shift+k"

    def test_modifierless_key_is_returned_for_parse_hotkey_to_reject(self, keys, mods):
        """The widget does not pre-judge validity; parse_hotkey owns the "needs a modifier"
        rule and its message. Returning the bare token keeps that single source of truth."""
        from hotkey import parse_hotkey
        chord = _to_chord(keys.Key_Space, mods.NoModifier)
        assert chord == "space"
        with pytest.raises(ValueError):
            parse_hotkey(chord)

    def test_letters_are_lowercased(self, keys, mods):
        assert _to_chord(keys.Key_Q, mods.ControlModifier | mods.AltModifier) == "ctrl+alt+q"

    def test_every_f_key_in_range_maps(self, keys, mods):
        for n in range(1, 13):
            key = getattr(keys, f"Key_F{n}")
            assert _to_chord(key, mods.ControlModifier) == f"ctrl+f{n}"


class TestKnownBadChordsSurfaceParseHotkeyMessages:
    """T2-7 adds no messages of its own; it must surface the existing tailored ones."""

    @pytest.mark.parametrize("chord,fragment", [
        ("alt+space", "window menu"),
        ("ctrl+shift+space", "Excel"),
        ("ctrl+space", "IntelliSense"),
    ])
    def test_known_bad_chords_rejected_with_specific_message(self, chord, fragment):
        from hotkey import parse_hotkey
        with pytest.raises(ValueError, match=fragment):
            parse_hotkey(chord)

    def test_capture_of_a_bad_chord_produces_that_exact_string(self, keys, mods):
        """Confirms the widget hands parse_hotkey a chord it recognises as conflicting,
        rather than something parse_hotkey cannot categorise."""
        assert _to_chord(keys.Key_Space, mods.AltModifier) == "alt+space"


class TestCaptureWidget:
    def _widget(self, qt_app, initial="ctrl+alt+space"):
        from settings_dialog import HotkeyCaptureButton
        return HotkeyCaptureButton(initial)

    def _press(self, widget, key, modifiers):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        widget.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, int(key), modifiers))

    def test_shows_the_current_chord_when_idle(self, qt_app):
        assert self._widget(qt_app).text() == "ctrl+alt+space"

    def test_prompts_when_armed(self, qt_app):
        widget = self._widget(qt_app)
        widget.setChecked(True)
        widget._on_clicked()
        assert widget.capturing is True
        assert "Press a chord" in widget.text()

    def test_captured_chord_is_stored_and_emitted(self, qt_app, keys, mods):
        widget = self._widget(qt_app)
        received = []
        widget.captured.connect(received.append)
        widget.setChecked(True)
        widget._on_clicked()
        self._press(widget, keys.Key_J, mods.ControlModifier | mods.AltModifier)
        assert received == ["ctrl+alt+j"]
        assert widget.value() == "ctrl+alt+j"
        assert widget.capturing is False, "must disarm after a successful capture"

    def test_rejected_chord_shows_inline_error_and_stays_armed(self, qt_app, keys, mods):
        """Staying armed lets the user just try another chord instead of clicking again."""
        widget = self._widget(qt_app)
        received = []
        widget.captured.connect(received.append)
        widget.setChecked(True)
        widget._on_clicked()
        self._press(widget, keys.Key_Space, mods.AltModifier)  # alt+space
        assert received == []
        assert widget.value() == "ctrl+alt+space", "value must not change on rejection"
        assert "window menu" in widget.text()
        assert widget.capturing is True

    def test_escape_cancels_without_changing_the_value(self, qt_app, keys, mods):
        widget = self._widget(qt_app)
        received = []
        widget.captured.connect(received.append)
        widget.setChecked(True)
        widget._on_clicked()
        self._press(widget, keys.Key_Escape, mods.NoModifier)
        assert widget.capturing is False
        assert received == []
        assert widget.value() == "ctrl+alt+space"

    def test_bare_modifier_does_not_end_capture(self, qt_app, keys, mods):
        widget = self._widget(qt_app)
        widget.setChecked(True)
        widget._on_clicked()
        self._press(widget, keys.Key_Control, mods.ControlModifier)
        assert widget.capturing is True

    def test_keys_are_swallowed_while_armed(self, qt_app, keys, mods):
        """An unswallowed key press while armed would activate dialog buttons."""
        from PyQt6.QtGui import QKeyEvent
        widget = self._widget(qt_app)
        widget.setChecked(True)
        widget._on_clicked()
        event = QKeyEvent(QKeyEvent.Type.KeyPress, int(keys.Key_Home),
                          mods.NoModifier)
        widget.keyPressEvent(event)
        assert event.isAccepted()

    def test_tab_is_not_stolen_for_focus_navigation_while_armed(self, qt_app):
        """Qt consumes Tab before keyPressEvent unless focusNextPrevChild refuses it."""
        widget = self._widget(qt_app)
        widget.setChecked(True)
        widget._on_clicked()
        assert widget.focusNextPrevChild(True) is False

    def test_tab_navigation_still_works_when_idle(self, qt_app):
        """Blocking Tab permanently would break keyboard navigation of the dialog."""
        widget = self._widget(qt_app)
        assert widget.capturing is False
        widget.focusNextPrevChild(True)  # must not raise; delegates to Qt

    def test_set_value_updates_the_label(self, qt_app):
        widget = self._widget(qt_app)
        widget.set_value("ctrl+alt+f9")
        assert widget.text() == "ctrl+alt+f9"

    def test_empty_initial_value_shows_a_prompt(self, qt_app):
        assert self._widget(qt_app, initial="").text() == "Click to set"


class TestDialogIntegration:
    def test_text_fallback_still_accepted(self, qt_app):
        """The advanced field remains the value the save path reads, so it must still work
        on its own -- it is the only option for a chord the keyboard cannot produce."""
        import settings_dialog
        dlg = settings_dialog.SettingsDialog()
        try:
            dlg._hotkey_input.setText("CONTROL+ALT+F9")
            from hotkey import parse_hotkey
            assert parse_hotkey(dlg._hotkey_input.text()).display == "ctrl+alt+f9"
        finally:
            dlg.deleteLater()

    def test_capture_mirrors_into_the_text_field(self, qt_app):
        """Both controls must agree about what will be saved."""
        import settings_dialog
        dlg = settings_dialog.SettingsDialog()
        try:
            dlg._on_hotkey_captured("ctrl+alt+f9")
            assert dlg._hotkey_input.text() == "ctrl+alt+f9"
        finally:
            dlg.deleteLater()

    def test_both_controls_exist_and_start_in_sync(self, qt_app):
        import settings_dialog
        dlg = settings_dialog.SettingsDialog()
        try:
            assert dlg._hotkey_capture is not None
            assert dlg._hotkey_input is not None
            assert dlg._hotkey_capture.value() == dlg._hotkey_input.text()
        finally:
            dlg.deleteLater()
