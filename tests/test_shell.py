"""Tests for the application shell (SHELL_AND_CHAT.md §3, §8).

Follows IMPROVEMENTS.md §1.4: imports inside tests, one file per module, and every widget
built against a shared ``QApplication``.

Three things here are worth knowing before editing:

1. **``MainWindow`` is built with a stub settings form.** The real ``SettingsForm`` reads the
   keyring on construction, and a shell test has no business doing that. The one test that
   genuinely needs the real form -- ``test_settings_form_is_shared_with_the_dialog``, which is
   the whole acceptance criterion for `S-4` -- asks for it explicitly.
2. **``kb_dir`` is always a ``tmp_path``.** ``KnowledgePage`` defaults to the real
   ``config.KB_DIR``, and a test that scanned the developer's own knowledge folder would pass
   or fail depending on whose machine it ran on.
3. **The power tests use a real ``PushToTalkHotkey``** with an injected fake listener, because
   `S-3`'s claim is about the real object's ``enabled`` property, and a mock would let the
   window's own copy of the state pass unnoticed -- which is exactly the bug being excluded.
"""

import re

import pytest


@pytest.fixture(scope="module")
def qt_app():
    """One QApplication for the module. Qt requires it before any QWidget exists."""
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


_STUB_FORM_CLASS = None


def stub_form_class():
    """A stand-in for ``SettingsForm`` honouring the host contract, and nothing else.

    The contract is three signals and two accessors (see ``SettingsForm``'s docstring). Built
    once and cached: defining a ``QObject`` subclass per test is legal but wasteful, and a
    fresh class per test would make ``isinstance`` checks across tests confusing.
    """
    global _STUB_FORM_CLASS
    if _STUB_FORM_CLASS is not None:
        return _STUB_FORM_CLASS

    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    class StubForm(QWidget):
        sig_validity_changed = pyqtSignal(bool)
        sig_local_data_cleared = pyqtSignal()
        sig_saved = pyqtSignal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.saves = 0
            self.save_result = True
            self._cleared = False
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("stub settings"))

        def is_valid(self):
            return True

        def save(self):
            self.saves += 1
            if self.save_result:
                self.sig_saved.emit()
            return self.save_result

        @property
        def local_data_cleared(self):
            return self._cleared

        def wipe(self):
            """Stand in for the user confirming "clear local data"."""
            self._cleared = True
            self.sig_local_data_cleared.emit()

    _STUB_FORM_CLASS = StubForm
    return StubForm


@pytest.fixture
def make_window(qt_app, tmp_path):
    """Build ``MainWindow`` instances that touch neither the keyring nor the real KB folder."""
    built = []

    def build(**kwargs):
        from shell.window import MainWindow

        kwargs.setdefault("settings_form_factory", stub_form_class())
        kwargs.setdefault("kb_dir", tmp_path / "kb")
        window = MainWindow(**kwargs)
        built.append(window)
        return window

    yield build

    for window in built:
        window.hide()
        window.deleteLater()


def fake_hotkey():
    """A real ``PushToTalkHotkey`` with a mock listener, plus the mock, plus a press helper.

    Real object on purpose: ``enabled`` is a property guarded by a lock, and ``set_enabled``'s
    promise is that it gates callbacks *without* uninstalling the hook. Substituting a mock
    would test the substitute.
    """
    from unittest.mock import MagicMock

    import hotkey

    listener = MagicMock()
    presses = []
    hk = hotkey.PushToTalkHotkey(
        on_press=lambda: presses.append(1),
        on_release=lambda: None,
        listener_class=lambda **kwargs: listener,
    )
    hk.start()
    return hk, listener, presses


def code_only(source: str) -> str:
    """``source`` with docstrings and comments blanked out, line positions preserved.

    The drift guards below read source, and the shell's modules explain themselves at length --
    ``titlebar.py`` discusses ``parent().close()`` and ``pages/settings.py`` mentions keyring
    persistence, both of which a naive substring guard reports as violations. Stripping prose
    is what makes the guards about the code.

    String *literals* are deliberately kept: the QSS lives in f-strings, so a hard-coded colour
    would hide there, and that is precisely what
    ``test_no_shell_module_contains_a_literal_hex_colour`` is looking for. Blanking with
    equal-length spaces keeps every token position valid for the second pass.
    """
    import ast
    import io
    import tokenize

    lines = source.splitlines()

    def blank(start_row, start_col, end_row, end_col):
        for row in range(start_row, end_row + 1):
            line = lines[row - 1]
            begin = start_col if row == start_row else 0
            finish = end_col if row == end_row else len(line)
            lines[row - 1] = line[:begin] + " " * (finish - begin) + line[finish:]

    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, documented) and ast.get_docstring(node, clean=False) is not None:
            doc = node.body[0]
            blank(doc.lineno, doc.col_offset, doc.end_lineno, doc.end_col_offset)

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            blank(token.start[0], token.start[1], token.end[0], token.end[1])

    return "\n".join(lines)


def shell_sources():
    """Every ``.py`` file in the shell package as ``(posix name, code without prose)``."""
    from pathlib import Path

    import shell

    root = Path(shell.__file__).resolve().parent
    return [
        (path.relative_to(root).as_posix(), code_only(path.read_text(encoding="utf-8")))
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


# --- Geometry (§8) -----------------------------------------------------------


class TestWindowGeometry:
    def test_window_minimum_fits_1366x768(self, make_window):
        """The floor has to fit the laptop that caught out the Settings dialog.

        768 tall minus a ~40px taskbar leaves about 728 usable, so the height has to clear that.
        Lowered from 1040x680 once the pages became scrollable -- see ``MIN_WIDTH``'s docstring
        for the measurement that showed the old floor was 230px wider than the layout needed.
        """
        from shell.window import MIN_HEIGHT, MIN_WIDTH

        window = make_window()
        assert (MIN_WIDTH, MIN_HEIGHT) == (760, 480)
        assert MIN_WIDTH <= 1366
        assert MIN_HEIGHT <= 728
        assert window.minimumWidth() == MIN_WIDTH
        assert window.minimumHeight() == MIN_HEIGHT

    def test_the_layout_can_actually_reach_the_floor(self, make_window):
        """The constant is only a floor if nothing underneath it is taller.

        This is the check the previous minimum did not have, and the reason the user could not
        shrink the window: ``setMinimumSize`` said 1040x680 while ``layout().minimumSize()``
        needed only 810x646, so the *constant* was the constraint. Qt takes the larger of the
        two, so a page whose content cannot render small enough silently raises the real floor
        and the declared number becomes fiction.
        """
        window = make_window()
        layout_minimum = window.layout().minimumSize()
        assert layout_minimum.width() <= window.minimumWidth()
        assert layout_minimum.height() <= window.minimumHeight()

    def test_every_page_but_settings_scrolls(self, make_window):
        """What makes the low floor safe: too small becomes a scrollbar, not clipped content.

        Settings is excluded on purpose -- it owns its own scroll area with Save pinned outside
        it, and wrapping it again would nest two scroll regions and put Save back below the fold
        on the 1366x768 laptop that motivated it in the first place.
        """
        from PyQt6.QtWidgets import QScrollArea

        window = make_window()
        for name, host in window.page_hosts.items():
            if name == "settings":
                assert not isinstance(host, QScrollArea)
            else:
                assert isinstance(host, QScrollArea), name
                assert host.widgetResizable()

    def test_the_minimum_never_exceeds_the_screen(self, qt_app, make_window):
        """A floor bigger than the display traps the user with no way out.

        Real hardware this matters on: a 1920x1080 panel at 250% scaling reports 768x432 logical
        pixels, which is below ``MIN_HEIGHT``.
        """
        window = make_window()
        available = (window.screen() or qt_app.primaryScreen()).availableGeometry()
        assert window.minimumWidth() <= available.width()
        assert window.minimumHeight() <= available.height()

        width, height = window.minimum_for_screen()
        assert width <= available.width()
        assert height <= available.height()

    def test_opens_within_screen_cap(self, qt_app, make_window):
        """Opens at 1240x780, clamped to 88% of the available screen, floored by the minimum.

        Asserted against the live screen rather than a hard-coded size so this means the same
        thing on a 4K desktop and a 1366x768 laptop.
        """
        from shell.window import (
            MIN_HEIGHT,
            MIN_WIDTH,
            OPEN_HEIGHT,
            OPEN_WIDTH,
            SCREEN_FRACTION,
        )

        window = make_window()
        available = (window.screen() or qt_app.primaryScreen()).availableGeometry()

        expected_width = max(
            MIN_WIDTH, min(OPEN_WIDTH, int(available.width() * SCREEN_FRACTION)))
        expected_height = max(
            MIN_HEIGHT, min(OPEN_HEIGHT, int(available.height() * SCREEN_FRACTION)))
        assert window.width() == expected_width
        assert window.height() == expected_height
        assert window.width() <= OPEN_WIDTH
        assert window.height() <= OPEN_HEIGHT

    def test_resize_to_screen_survives_no_screen(self, make_window, mocker):
        """A window with no screen must still get a size, not divide by a ``None``."""
        window = make_window()
        mocker.patch.object(type(window), "screen", return_value=None)
        mocker.patch("PyQt6.QtWidgets.QApplication.primaryScreen", return_value=None)
        window.resize_to_screen()
        assert window.width() >= 760


# --- Closing (Invariant 5) ---------------------------------------------------


class TestClosing:
    def test_close_hides_and_does_not_quit(self, make_window):
        """Closing the window must not stop push-to-talk (Invariant 5).

        Nimbus is a background tool. If closing quit it, the hotkey would die with the window
        and the user would have to relaunch to ask a question.
        """
        window = make_window()
        quits, hidden, listening = [], [], []
        window.sig_quit.connect(lambda: quits.append(1))
        window.sig_hidden_to_tray.connect(lambda: hidden.append(1))
        window.sig_set_listening.connect(listening.append)

        window.close()

        assert window.isHidden()
        assert not window.isVisible()
        assert quits == []
        assert listening == []
        assert hidden == [1]

    def test_quit_is_a_signal_and_only_the_account_page_raises_it(self, make_window):
        """The only in-window way out is Account's Quit, and it goes to one shutdown path."""
        window = make_window()
        quits = []
        window.sig_quit.connect(lambda: quits.append(1))

        window.account.sig_quit.emit()

        assert quits == [1]


# --- Navigation (§8) ---------------------------------------------------------


class TestNavigation:
    def test_every_nav_item_maps_to_a_page(self, make_window):
        """One list drives both the sidebar and the stack, so neither can gain an orphan."""
        from shell.nav import NAV_ITEMS

        window = make_window()
        names = [name for name, _label in NAV_ITEMS]

        assert set(window.pages) == set(names)
        assert set(window.sidebar.items) == set(names)
        assert window.stack.count() == len(names)

        for name in names:
            window.show_page(name)
            assert window.sidebar.selected == name
            # Through `page_hosts`, not `page.parentWidget()`. Every page except Settings now
            # sits inside a `QScrollArea`, so a page's parent is the scroll area's viewport
            # child rather than the widget the stack holds.
            assert window.stack.currentWidget() is window.page_hosts[name]

    def test_nav_side_constant_moves_the_sidebar(self, make_window):
        """§0.3: reversing the rail is one value, and the layout order proves it moved."""
        left = make_window(nav_side_override="left")
        right = make_window(nav_side_override="right")

        assert left.nav_side == "left"
        assert right.nav_side == "right"
        assert left.body_layout.indexOf(left.sidebar) < left.body_layout.indexOf(left.stack)
        assert right.body_layout.indexOf(right.sidebar) > right.body_layout.indexOf(right.stack)
        assert left.sidebar.side == "left"
        assert right.sidebar.side == "right"

    @pytest.mark.parametrize(
        "value,expected",
        [("left", "left"), ("right", "right"), ("RIGHT", "right"),
         (" right ", "right"), ("sideways", "left"), ("", "left")],
    )
    def test_nav_side_reads_the_setting_and_never_invents_a_third_layout(
            self, monkeypatch, value, expected):
        import shell.window as window_module

        monkeypatch.setattr("config.resolve_setting", lambda name, default=None: value)
        assert window_module.nav_side() == expected

    def test_nav_side_falls_back_when_config_is_unreadable(self, monkeypatch):
        import shell.window as window_module

        def boom(*args, **kwargs):
            raise RuntimeError("keyring locked")

        monkeypatch.setattr("config.resolve_setting", boom)
        assert window_module.nav_side() == "left"

    def test_unknown_page_is_ignored_rather_than_raising(self, make_window):
        """``show_page`` is reachable from a signal; a typo must not take the window down."""
        window = make_window()
        window.show_page("home")
        window.show_page("nope")
        assert window.sidebar.selected == "home"

    def test_programmatic_selection_does_not_echo_back(self, make_window):
        """``Sidebar.select`` is silent, so a page change cannot cause another page change."""
        window = make_window()
        requests = []
        window.sidebar.sig_page_requested.connect(requests.append)

        window.sidebar.select("journal")

        assert requests == []
        assert window.sidebar.selected == "journal"

    def test_a_page_that_fails_to_refresh_does_not_block_navigation(self, make_window, mocker):
        window = make_window()
        mocker.patch.object(
            window.journal, "refresh", side_effect=RuntimeError("db locked"))
        window.show_page("journal")
        assert window.sidebar.selected == "journal"


# --- Settings sharing (`S-4`, §8) -------------------------------------------


class TestSettingsSharing:
    def test_settings_form_is_shared_with_the_dialog(self, qt_app):
        """`S-4`'s acceptance criterion: one implementation, two hosts.

        Uses the real form deliberately -- this is the test that would catch the shell growing
        its own settings UI, which is the failure mode where the 40+ dialog tests keep passing
        against a dialog nobody opens any more.
        """
        from settings_dialog import SettingsDialog, SettingsForm
        from shell.pages.settings import SettingsPage

        page = SettingsPage()
        dialog = SettingsDialog()
        try:
            assert isinstance(page.form, SettingsForm)
            assert isinstance(dialog._form, SettingsForm)
            # Aliases, not copies: the dialog's historical attribute names reach the form's
            # live widgets, which is why the existing tests needed no changes.
            assert dialog._key_inputs is dialog._form._key_inputs
            assert dialog._model_combos is dialog._form._model_combos
            assert dialog._page is dialog._form
            # Same widget names on both hosts, so neither can drift.
            assert set(page.form._key_inputs) == set(dialog._key_inputs)
        finally:
            page.deleteLater()
            dialog.deleteLater()

    def test_the_shell_page_defines_no_settings_of_its_own(self):
        """A drift guard for `S-4`: the page hosts, it does not reimplement."""
        source = dict(shell_sources())["pages/settings.py"]
        assert "from settings_dialog import SettingsForm" in source
        for forbidden in ("keyring", "QLineEdit", "QComboBox", "resolve_api_key"):
            assert forbidden not in source, f"{forbidden} suggests a second settings UI"

    def test_settings_page_has_exactly_one_scroll_area(self, qt_app):
        """The form owns no scroll area; the host owns exactly one, with Save outside it.

        Nesting a second scroll region is the failure mode: the inner one takes the wheel and
        the Save button ends up unreachable, which is the bug the dialog already fixed.
        """
        from PyQt6.QtWidgets import QScrollArea

        from shell.pages.settings import SettingsPage

        page = SettingsPage(form_factory=stub_form_class())
        try:
            scrolls = page.findChildren(QScrollArea)
            assert len(scrolls) == 1
            assert page.form.findChildren(QScrollArea) == []
            # Save lives outside the scroll area, so it cannot fall below the fold.
            assert page.save_button not in scrolls[0].findChildren(type(page.save_button))
        finally:
            page.deleteLater()

    def test_the_shell_reacts_to_local_data_cleared(self, make_window):
        """§3's ⚠ VERIFY: the wipe path must work from the page, not just the dialog.

        Reacting, not merely recording: the banner appears and the form is disabled, because
        every field in it now describes settings that have just been deleted.
        """
        window = make_window()
        cleared = []
        window.sig_local_data_cleared.connect(lambda: cleared.append(1))
        page = window.settings

        page.form.wipe()

        assert cleared == [1]
        assert page.local_data_cleared is True
        assert page.restart_banner.isVisibleTo(page)
        assert "reopen" in page.restart_banner.text().lower()
        assert not page.form.isEnabled()
        assert not page.save_button.isEnabled()

    def test_a_refused_save_is_reported_rather_than_assumed(self, make_window):
        """The form refuses on an invalid hotkey or a cancelled Ollama warning.

        The dialog could assume success because it closed; a page that stays open cannot.
        """
        window = make_window()
        page = window.settings
        page.form.save_result = False

        assert page.save() is False
        assert page.status.text() == "Not saved."

        page.form.save_result = True
        assert page.save() is True
        assert "Saved" in page.status.text()
        assert page.form.saves == 2

    def test_save_button_follows_form_validity(self, make_window):
        window = make_window()
        page = window.settings

        page.form.sig_validity_changed.emit(False)
        assert not page.save_button.isEnabled()
        page.form.sig_validity_changed.emit(True)
        assert page.save_button.isEnabled()


# --- Power, and the single source of truth (`S-3`, §8) ----------------------


class TestPower:
    def test_power_toggle_reflects_hotkey_enabled(self, make_window):
        """The toggle is a view of ``hotkey.enabled`` and a request to change it.

        Verified while building the shell: ``set_enabled`` gates callbacks without touching
        ``self._listener`` -- ``listener.stop()`` is never called -- so the toggle is instant
        and needs no restart. That is asserted here, because if it stopped being true the
        power control would silently require one.
        """
        hk, listener, _presses = fake_hotkey()
        window = make_window(listening_provider=lambda: hk.enabled)
        window.sig_set_listening.connect(hk.set_enabled)

        assert hk.enabled is True
        assert window.is_listening is True
        assert window.home.toggle.isChecked() is True

        window.home.toggle.click()
        assert hk.enabled is False
        assert window.is_listening is False
        assert window.home.toggle.isChecked() is False
        assert window.home.power_state.text() == "Nimbus is paused"
        # The hint says what to do about it, which "PAUSED" on its own never did.
        assert "paused" in window.home.power_hint.text().lower()

        window.home.toggle.click()
        assert hk.enabled is True
        assert window.home.power_state.text() == "Nimbus is listening"
        assert "hold" in window.home.power_hint.text().lower()

        # The hook stayed installed the whole time. This is the "no restart" claim.
        assert listener.stop.called is False
        assert hk._listener is not None

    def test_power_state_is_not_duplicated(self, make_window):
        """No component keeps its own copy, so the window and the tray cannot disagree.

        Proved by making the source of truth *refuse*: with nothing wired to
        ``sig_set_listening`` the real state never changes, so a toggle that held its own
        boolean would show "on" while Nimbus was paused. It snaps back instead.
        """
        from PyQt6.QtGui import QAction

        state = {"on": False}
        window = make_window(listening_provider=lambda: state["on"])

        window.home.toggle.click()
        assert window.is_listening is False
        assert window.home.toggle.isChecked() is False

        # Nor can a caller push a value the provider disagrees with.
        window.set_listening(True)
        assert window.is_listening is False
        assert window.home.toggle.isChecked() is False

        # A tray-style checkable action driven off the same provider converges with it.
        tray_action = QAction("Pause", window)
        tray_action.setCheckable(True)

        def sync():
            window.set_listening(state["on"])
            tray_action.setChecked(not state["on"])

        state["on"] = True
        sync()
        assert window.home.toggle.isChecked() is True
        assert tray_action.isChecked() is False

        state["on"] = False
        sync()
        assert window.home.toggle.isChecked() is False
        assert tray_action.isChecked() is True

    def test_no_shell_module_keeps_its_own_listening_flag(self):
        """A source-level guard, because a cached boolean passes every behavioural test.

        The functional tests above catch a copy that is *read*; this catches one that is only
        written, which is how the two drift apart in the first place.
        """
        forbidden = re.compile(
            r"self\._(listening|is_listening|paused|enabled)(?![\w])\s*=")
        for name, source in shell_sources():
            assert not forbidden.search(source), (
                f"{name} keeps its own push-to-talk state; read hotkey.enabled instead")

    def test_the_toggle_does_not_emit_when_state_is_pushed_in(self, qt_app):
        """``set_on`` reflects; it does not request. Otherwise the two loop forever."""
        from shell.widgets import PowerToggle

        toggle = PowerToggle()
        try:
            seen = []
            toggle.toggled.connect(seen.append)
            toggle.set_on(True)
            assert toggle.isChecked() is True
            assert seen == []
        finally:
            toggle.deleteLater()

    def test_home_falls_back_to_the_view_with_no_provider(self, qt_app):
        """With nothing injected the page is the only state there is, and says so honestly."""
        from shell.pages.home import HomePage

        page = HomePage()
        try:
            assert page.is_listening is False
            page.set_listening(True)
            assert page.is_listening is True
        finally:
            page.deleteLater()

    def test_a_provider_that_raises_reads_as_paused(self, qt_app):
        """A status page must degrade to "unknown", never take the window down."""
        from shell.pages.home import HomePage

        def boom():
            raise RuntimeError("hotkey gone")

        page = HomePage(listening_provider=boom)
        try:
            assert page.is_listening is False
        finally:
            page.deleteLater()


# --- Home's injected numbers ------------------------------------------------


class TestHomeNumbers:
    def test_unmeasured_numbers_render_as_an_em_dash_not_zero(self, qt_app):
        """A measured zero and an unmeasured one are different claims.

        Claiming the Privacy Guard has suppressed nothing when nobody counted would undercut
        the most trust-building item on the page.
        """
        from shell.pages.home import NO_NUMBER, HomePage

        page = HomePage()
        try:
            assert page.week_count.text() == NO_NUMBER
            assert NO_NUMBER in page.privacy_count.text()
        finally:
            page.deleteLater()

    def test_injected_numbers_are_shown(self, qt_app):
        from shell.pages.home import HomePage

        page = HomePage(usage_provider=lambda: 12, privacy_provider=lambda: 3)
        try:
            assert page.week_count.text() == "12"
            assert "3" in page.privacy_count.text()
        finally:
            page.deleteLater()

    def test_recent_table_is_capped_and_hidden_when_empty(self, qt_app):
        from datetime import datetime, timedelta

        from shell.pages.home import RECENT_ROWS, HomePage

        rows = [
            {"question": f"q{index}", "app": "excel.exe",
             "when": datetime.now() - timedelta(minutes=index), "target": "Ribbon"}
            for index in range(RECENT_ROWS + 4)
        ]
        page = HomePage(recent_provider=lambda: rows)
        try:
            assert page.recent.rowCount() == RECENT_ROWS
            # Sentence-cased for display: speech-to-text returns lower-case starts often
            # enough that a column of them reads as a log rather than a list of questions.
            assert page.recent.item(0, 0).text() == "Q0"
            assert page.recent.item(0, 1).text() == "excel.exe"
            assert not page.recent_empty.isVisibleTo(page)
        finally:
            page.deleteLater()

        empty = HomePage(recent_provider=lambda: [])
        try:
            assert empty.recent.rowCount() == 0
            assert empty.recent_empty.isVisibleTo(empty)
        finally:
            empty.deleteLater()

    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "just now"), (59, "just now"), (60, "1m ago"), (3599, "59m ago"),
         (3600, "1h ago"), (86400, "1d ago"), (172800, "2d ago")],
    )
    def test_relative_time(self, seconds, expected):
        from datetime import datetime, timedelta

        from shell.pages.home import relative_time

        now = datetime(2026, 8, 15, 12, 0, 0)
        assert relative_time(now - timedelta(seconds=seconds), now=now) == expected

    def test_relative_time_leaves_a_preformatted_string_alone(self):
        """Reformatting someone else's display string is how "2m ago ago" happens."""
        from shell.pages.home import NO_NUMBER, relative_time

        assert relative_time("yesterday") == "yesterday"
        assert relative_time(None) == NO_NUMBER

    def test_provider_name_and_local_mode_reach_the_sidebar(self, make_window):
        import theme

        window = make_window()

        window.set_provider("gemini", "gemini-2.5-flash")
        assert window.home.provider_name.text() == "gemini"
        assert window.home.provider_model.text() == "gemini-2.5-flash"
        assert window.sidebar.provider_status.colour == theme.ACCENT

        window.set_provider("ollama", "qwen2.5vl")
        assert window.sidebar.provider_status.colour == theme.SUCCESS

        # The dot carries the state; the label never says "on" or "off".
        #
        # Red rather than amber when off, on request: this one is a privacy setting, and the
        # colour people already read as "not protected" is red. The label stays "Privacy Guard"
        # either way -- a coloured dot plus the word "off" says the same thing twice, and the
        # dot is the part you can see from across the room.
        window.set_privacy_guard(True)
        assert window.sidebar.privacy_status.colour == theme.SUCCESS
        assert window.sidebar.privacy_status.text == "Privacy Guard"

        window.set_privacy_guard(False)
        assert window.sidebar.privacy_status.colour == theme.DANGER
        assert window.sidebar.privacy_status.text == "Privacy Guard"
        assert "off" in window.sidebar.privacy_status.toolTip().lower()


# --- Knowledge page, against real kb.py ------------------------------------


class TestKnowledgePage:
    @pytest.fixture
    def kb_folder(self, tmp_path):
        """A knowledge folder covering both layouts ``kb.py`` supports, plus noise."""
        import kb

        root = tmp_path / "knowledge"
        root.mkdir()
        (root / kb.GUIDE_FILENAME).write_text("# guide", encoding="utf-8")
        (root / "excel.exe.md").write_text("Ribbon notes", encoding="utf-8")
        folder = root / "orionflow.exe"
        folder.mkdir()
        (folder / "a.md").write_text("alpha", encoding="utf-8")
        (folder / "nested").mkdir()
        (folder / "nested" / "b.txt").write_text("beta", encoding="utf-8")
        (folder / "sheet.xlsx").write_bytes(b"not readable")
        (root / "empty.exe").mkdir()
        return root

    def test_scan_reads_both_layouts_and_excludes_the_guide(self, kb_folder):
        """Counts come from ``kb.iter_kb_files``, so the page cannot claim files ``recall``
        will not read -- including the recursive walk and the suffix filter."""
        from shell.pages.knowledge import scan_kb

        entries = {entry.app_name: entry for entry in scan_kb(kb_folder)}

        assert set(entries) == {"excel.exe", "orionflow.exe"}
        assert entries["excel.exe"].kind == "file"
        assert entries["excel.exe"].file_count == 1
        assert entries["orionflow.exe"].kind == "folder"
        # a.md and nested/b.txt, but not sheet.xlsx.
        assert entries["orionflow.exe"].file_count == 2

    def test_an_app_with_both_layouts_is_reported_as_both(self, kb_folder):
        from shell.pages.knowledge import scan_kb

        (kb_folder / "orionflow.exe.md").write_text("extra", encoding="utf-8")
        entry = {e.app_name: e for e in scan_kb(kb_folder)}["orionflow.exe"]

        assert set(entry.kind.split(" + ")) == {"file", "folder"}
        assert entry.file_count == 3

    def test_scan_of_a_missing_folder_is_empty_not_an_error(self, tmp_path):
        from shell.pages.knowledge import scan_kb

        assert scan_kb(tmp_path / "nope") == []

    def test_page_lists_entries_and_shows_the_folder(self, qt_app, kb_folder):
        from shell.pages.knowledge import KnowledgePage

        page = KnowledgePage(kb_dir=kb_folder, open_folder=lambda folder: True)
        try:
            assert page.table.rowCount() == 2
            assert page.table.item(0, 0).text() == "excel.exe"
            assert not page.empty.isVisibleTo(page)
            assert str(kb_folder) == page.path_label.text()
            # Instructions, not a dump of README.md. The panel used to show the seeded file's
            # first 40 lines with the Markdown syntax still in it, which asked the reader to
            # parse `#` headings before finding the one fact they needed.
            rendered = page.guide.toPlainText()
            assert "orionflow.exe.md" in rendered
            assert "#" not in rendered, "raw Markdown syntax must not reach the reader"
            assert str(kb_folder) in rendered
        finally:
            page.deleteLater()

    def test_the_guide_copy_names_the_convention_and_the_limits(self):
        """Pure, because the copy *is* the feature here -- this panel is the only place the
        naming convention gets explained at the moment it matters."""
        from shell.pages.knowledge import guide_html

        html = guide_html("C:/Users/x/Documents/Nimbus Wiki")

        assert "orionflow.exe.md" in html
        assert ".pdf" in html and ".docx" in html
        assert "40 files" in html, "the cap is real and silently truncates; say so"
        assert "no restart" in html.lower()
        assert "C:/Users/x/Documents/Nimbus Wiki" in html

    @pytest.mark.parametrize(
        "kind,expected",
        [("file", "Single file"), ("folder", "Folder"),
         ("file + folder", "File and folder"), ("folder + file", "File and folder")],
    )
    def test_layout_labels_are_human(self, kind, expected):
        """``folder + file`` and ``file + folder`` are the same thing in different iteration
        orders. Showing both raw made one state look like two."""
        from shell.pages.knowledge import _kind_label

        assert _kind_label(kind) == expected

    def test_empty_folder_shows_the_discoverability_copy(self, qt_app, tmp_path):
        from shell.pages.knowledge import KnowledgePage

        folder = tmp_path / "blank"
        folder.mkdir()
        page = KnowledgePage(kb_dir=folder, open_folder=lambda f: True)
        try:
            assert page.table.rowCount() == 0
            assert page.empty.isVisibleTo(page)
            assert not page.table.isVisibleTo(page)
        finally:
            page.deleteLater()

    def test_dropped_files_are_copied_never_moved(self, qt_app, tmp_path):
        """A user dragging their only copy of a document out of their own folders and having
        it disappear would be indefensible."""
        from shell.pages.knowledge import KnowledgePage

        source = tmp_path / "src"
        source.mkdir()
        note = source / "orionflow.exe.md"
        note.write_text("how the render queue works", encoding="utf-8")
        target = tmp_path / "kb"
        target.mkdir()

        page = KnowledgePage(kb_dir=target, open_folder=lambda f: True)
        try:
            added, skipped = page.add_paths([note])
            assert (added, skipped) == (1, 0)
            assert note.exists(), "the source file must be left alone"
            assert (target / "orionflow.exe.md").read_text(encoding="utf-8") == (
                "how the render queue works")
        finally:
            page.deleteLater()

    def test_a_name_clash_is_suffixed_rather_than_overwritten(self, qt_app, tmp_path):
        from shell.pages.knowledge import KnowledgePage

        source = tmp_path / "src"
        source.mkdir()
        incoming = source / "notes.md"
        incoming.write_text("new", encoding="utf-8")
        target = tmp_path / "kb"
        target.mkdir()
        (target / "notes.md").write_text("existing", encoding="utf-8")

        page = KnowledgePage(kb_dir=target, open_folder=lambda f: True)
        try:
            page.add_paths([incoming])
            assert (target / "notes.md").read_text(encoding="utf-8") == "existing"
            assert (target / "notes (2).md").read_text(encoding="utf-8") == "new"
        finally:
            page.deleteLater()

    def test_unsupported_files_are_reported_not_silently_dropped(self, qt_app, tmp_path):
        """A dropped .xlsx that vanishes looks like a bug in Nimbus."""
        from shell.pages.knowledge import KnowledgePage

        source = tmp_path / "src"
        source.mkdir()
        sheet = source / "budget.xlsx"
        sheet.write_bytes(b"binary")
        target = tmp_path / "kb"
        target.mkdir()

        page = KnowledgePage(kb_dir=target, open_folder=lambda f: True)
        try:
            assert page.add_paths([sheet]) == (0, 1)
            assert not (target / "budget.xlsx").exists()
        finally:
            page.deleteLater()

    def test_open_folder_failure_is_surfaced(self, qt_app, tmp_path, mocker):
        from shell.pages.knowledge import KnowledgePage

        folder = tmp_path / "kb"
        folder.mkdir()
        page = KnowledgePage(kb_dir=folder, open_folder=lambda f: False)
        try:
            mocker.patch("kb.ensure_guide", return_value=None)
            page.open_kb_folder()
            assert "Could not open" in page.status.text()
        finally:
            page.deleteLater()

    @pytest.mark.parametrize(
        "size,expected",
        [(0, "0 B"), (512, "512 B"), (1024, "1.0 KB"), (1536, "1.5 KB"),
         (1048576, "1.0 MB")],
    )
    def test_human_size(self, size, expected):
        from shell.pages.knowledge import human_size

        assert human_size(size) == expected


# --- Journal page, against real review.py ----------------------------------


class TestJournalPage:
    @pytest.fixture
    def queue(self, tmp_path):
        """A real ``ReviewQueue`` in ``tmp_path``, never the developer's own index.db."""
        import review

        return review.ReviewQueue(tmp_path / "index.db")

    def test_page_reads_a_real_review_queue(self, qt_app, queue):
        from datetime import date, timedelta

        from shell.pages.journal import JournalPage

        today = date(2026, 8, 15)
        queue.add("orionflow.exe", "Where is the render queue?", "View menu",
                  target_label="Render queue", today=today - timedelta(days=3))

        page = JournalPage(queue_provider=lambda: queue, today_provider=lambda: today)
        try:
            assert page.due_count.text() == "1"
            assert page.items.rowCount() == 1
            assert page.items.item(0, 0).text() == "Where is the render queue?"
            assert page.items.item(0, 1).text() == "orionflow.exe"
            assert page.week_count.text() == "1"
            assert page.accuracy.text() == "not yet reviewed"
            assert page.quiz_button.isEnabled()
            assert not page.empty.isVisibleTo(page)
        finally:
            page.deleteLater()

    def test_nothing_due_is_the_system_working_not_an_error(self, qt_app, queue):
        from datetime import date

        from shell.pages.journal import JournalPage

        today = date(2026, 8, 15)
        queue.add("excel.exe", "What is a pivot table?", "A summary", today=today)

        page = JournalPage(queue_provider=lambda: queue, today_provider=lambda: today)
        try:
            assert page.due_count.text() == "0"
            assert page.items.rowCount() == 0
            assert page.empty.isVisibleTo(page)
            assert "working" in page.empty.text()
            assert not page.quiz_button.isEnabled()
        finally:
            page.deleteLater()

    def test_no_queue_means_no_database_is_touched(self, qt_app, mocker):
        """Constructing a ``ReviewQueue`` by default would have every shell test write to the
        developer's real ``~/.nimbus/index.db``, which the test conventions forbid."""
        import review

        from shell.pages.journal import NO_NUMBER, JournalPage

        spy = mocker.spy(review, "ReviewQueue")
        page = JournalPage()
        try:
            assert spy.call_count == 0
            assert page.due_count.text() == NO_NUMBER
            assert page.accuracy.text() == NO_NUMBER
            assert not page.quiz_button.isEnabled()
        finally:
            page.deleteLater()

    def test_quiz_me_only_emits(self, qt_app, queue, make_window):
        """Grading needs a spoken answer and a live grounding call: the pipeline's job."""
        from datetime import date, timedelta

        window = make_window(review_queue_provider=lambda: queue)
        asked = []
        window.sig_quiz_me.connect(lambda: asked.append(1))

        queue.add("excel.exe", "q", "a", today=date.today() - timedelta(days=3))
        window.journal.refresh()
        window.journal.quiz_button.click()

        assert asked == [1]

    def test_insights_are_written_only_when_asked(self, qt_app, queue, tmp_path):
        """Writing a file as a side effect of *looking* at a page is the kind of surprise that
        erodes trust in a local-first app."""
        from datetime import date

        from shell.pages.journal import JournalPage

        insights = tmp_path / "insights.md"
        page = JournalPage(
            queue_provider=lambda: queue,
            insights_path_provider=lambda: insights,
            today_provider=lambda: date(2026, 8, 15),
        )
        try:
            assert not insights.exists()
            page.write_insights()
            assert insights.exists()
            assert str(insights) in page.status.text()
        finally:
            page.deleteLater()

    def test_a_failing_write_is_reported_not_swallowed(self, qt_app, queue, mocker):
        from datetime import date

        from shell.pages.journal import JournalPage

        mocker.patch("review.write_insights", side_effect=OSError("read-only volume"))
        page = JournalPage(
            queue_provider=lambda: queue, today_provider=lambda: date(2026, 8, 15))
        try:
            page.write_insights()
            assert "Could not write" in page.status.text()
        finally:
            page.deleteLater()

    @pytest.mark.parametrize(
        "stats,expected",
        [({"correct": 0, "wrong": 0}, "not yet reviewed"),
         ({"correct": 4, "wrong": 1}, "80%"),
         ({"correct": 1, "wrong": 0}, "100%"),
         ({}, "not yet reviewed")],
    )
    def test_accuracy_text(self, stats, expected):
        from shell.pages.journal import accuracy_text

        assert accuracy_text(stats) == expected


# --- Account page (§5, phase 4 is not here yet) ----------------------------


class TestAccountPage:
    def test_un_activated_is_the_honest_default(self, qt_app):
        """``licensing.py`` does not exist, and this page says so rather than pretending."""
        from shell.pages.account import NOT_ACTIVATED, UNKNOWN, AccountPage

        page = AccountPage()
        try:
            assert page.licence() is None
            assert page.is_activated is False
            assert page.status.text() == NOT_ACTIVATED
            assert page.plan.text() == UNKNOWN
            assert page.seats.text() == UNKNOWN
            assert "not set up" in page.detail.text()
            assert not page.deactivate_button.isEnabled()
            assert not page.sign_out_button.isEnabled()
        finally:
            page.deleteLater()

    def test_an_injected_licence_is_rendered(self, qt_app):
        from shell.pages.account import AccountPage, LicenceState

        state = LicenceState(
            activated=True, plan="Nimbus Pro", seats_used=1, seats_total=3,
            expires="2027-01-01", detail="Thanks for supporting Nimbus.")
        page = AccountPage(licence_provider=lambda: state)
        try:
            assert page.is_activated is True
            assert page.status.text() == "Active"
            assert page.plan.text() == "Nimbus Pro"
            assert page.seats.text() == "1 of 3"
            assert page.expires.text() == "2027-01-01"
            assert page.deactivate_button.isEnabled()
            assert page.sign_out_button.isEnabled()
        finally:
            page.deleteLater()

    def test_a_licence_lookup_that_throws_reads_as_not_activated(self, qt_app):
        """"We do not know" must not lock the user out of their own window."""
        from shell.pages.account import NOT_ACTIVATED, AccountPage

        def boom():
            raise RuntimeError("network down")

        page = AccountPage(licence_provider=boom)
        try:
            assert page.is_activated is False
            assert page.status.text() == NOT_ACTIVATED
        finally:
            page.deleteLater()

    def test_the_shell_stubs_no_licence_check(self):
        """A placeholder that *looks* like enforcement is worse than none (§0.1).

        It would read as real to the next person, get wired to something, and be trusted.
        """
        for name, source in shell_sources():
            for forbidden in ("import licensing", "hashlib", "hmac", "signature"):
                assert forbidden not in source, (
                    f"{name} looks like it is enforcing a licence; §5 owns that")

    def test_device_name_never_raises(self, mocker):
        from shell.pages import account

        mocker.patch("platform.node", side_effect=OSError("no hostname"))
        assert account.device_name() == account.UNKNOWN


# --- Design-system drift guards (§2, §7) -----------------------------------


class TestDesignSystemDrift:
    def test_the_prose_stripper_keeps_the_qss_it_has_to_search(self):
        """A drift guard that cannot fail is worse than no guard, so prove this one can.

        ``code_only`` drops docstrings and comments; if it also dropped string literals every
        guard below would pass vacuously, because the shell's QSS lives in f-strings.
        """
        sample = (
            '"""A docstring mentioning #ff0000 and keyring."""\n'
            "# a comment mentioning #00ff00\n"
            "def qss():\n"
            '    return "QFrame#Card { background: #123456; }"\n'
        )
        stripped = code_only(sample)

        assert "#ff0000" not in stripped
        assert "#00ff00" not in stripped
        assert "keyring" not in stripped
        assert "#123456" in stripped, "string literals must survive; the QSS lives in them"
        assert "#Card" in stripped
        assert stripped.count("\n") == sample.count("\n") - 1

        hex_colour = re.compile(
            r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})"
            r"(?![0-9a-zA-Z_])")
        assert hex_colour.findall(stripped) == ["#123456"]
        # And the object-name selectors the shell really uses are not false positives.
        for selector in ("#Card", "#Root", "#NavItem", "#TitleBar", "#Sidebar", "#Danger",
                         "#Primary", "#Mono", "#Muted", "#Display", "#RestartBanner"):
            assert not hex_colour.search(selector + " {")

    def test_no_shell_module_contains_a_literal_hex_colour(self):
        """Every colour comes from ``theme``, so one palette change moves the whole app.

        The pattern deliberately skips QSS object-name selectors like ``#Card`` -- it requires
        the whole token to be hex digits and to end there.
        """
        hex_colour = re.compile(
            r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})"
            r"(?![0-9a-zA-Z_])")
        for name, source in shell_sources():
            found = hex_colour.findall(source)
            assert not found, f"{name} hard-codes colour(s) {found}; import from theme"

    def test_no_shell_module_contains_a_literal_rgba_colour(self):
        """``theme.rgba(...)`` exists so opacity variants stay tied to the palette."""
        literal_rgba = re.compile(r"rgba\(\s*\d")
        for name, source in shell_sources():
            assert not literal_rgba.search(source), f"{name} hard-codes an rgba() colour"

    def test_every_duration_passes_through_theme_duration(self):
        """Reduced motion is only honoured if every duration goes through the one funnel."""
        bare_duration = re.compile(r"setDuration\(\s*(?!theme\.duration)")
        for name, source in shell_sources():
            assert not bare_duration.search(source), (
                f"{name} sets an animation duration without theme.duration(); "
                "reduced motion would be ignored")

    def test_every_curve_passes_through_theme_easing(self):
        bare_curve = re.compile(r"setEasingCurve\(\s*(?!theme\.easing)")
        for name, source in shell_sources():
            assert not bare_curve.search(source), (
                f"{name} sets an easing curve without theme.easing()")

    def test_the_shell_never_imports_app(self):
        """§0.2's seam: the window is a view, and the pipeline must not acquire a UI import."""
        forbidden = re.compile(r"^\s*(?:import app\b|from app import)", re.MULTILINE)
        for name, source in shell_sources():
            assert not forbidden.search(source), f"{name} imports app; use an injected callable"

    def test_reduced_motion_collapses_every_animation(self, qt_app, make_window,
                                                      monkeypatch):
        """One funnel, so one setting silences all of it."""
        import theme

        monkeypatch.setattr(theme, "_animations_enabled", False)
        assert theme.duration(theme.DUR_STANDARD) == 0
        assert theme.duration(theme.DUR_ENTRANCE) == 0

        window = make_window()
        window.show_page("journal")
        window.home.toggle.click()
        qt_app.processEvents()

        # The knob slide and the click ripple, both of which ran. The sidebar marker is not
        # asserted here: it only sets a duration when it actually animates, and under pytest
        # the window is never shown so it takes its documented jump-instead-of-slide path.
        assert window.home.toggle._animation.duration() == 0
        assert window.home.toggle._ripple_animation.duration() == 0

    def test_there_is_no_graphics_effect_on_the_page_stack(self, make_window):
        """The page crossfade was removed after seeing it on real hardware.

        A ``QGraphicsEffect`` renders its target offscreen, and the pages contain exactly the
        widgets that go wrong there -- scroll areas and tables with transparent viewports. The
        fade left stale pixels from the previous page visible inside the new one, worst on
        Knowledge where the table fills the card. This pins the removal, because "the spec says
        crossfade" is otherwise a reasonable thing to re-add.
        """
        window = make_window()

        assert window.stack.graphicsEffect() is None
        for page in window.pages.values():
            assert page.graphicsEffect() is None

    def test_surfaces_are_shaded_rather_than_flat(self, make_window):
        """§2.5. A flat fill and a gradient fill are the difference between a panel and a
        surface, and the window is where that shows."""
        import theme

        window = make_window()
        qss = window.styleSheet()

        assert theme.surface_gradient() in qss, "cards must be shaded"
        assert theme.accent_gradient() in qss, "primary actions carry the metallic accent"

    def test_the_grain_covers_the_content_only_not_the_chrome(self, qt_app, make_window):
        """The grain exists to stop gradients banding, and the chrome is flat now.

        A 4% noise tile over flat black is visible *as noise* rather than as texture, which is
        why the title bar and rail still read as grainy after they were made flat.
        """
        window = make_window()
        window.show()
        qt_app.processEvents()

        assert window.grain.geometry() == window.stack.geometry()
        assert not window.grain.geometry().intersects(window.titlebar.geometry())
        assert not window.grain.geometry().intersects(window.sidebar.geometry())

        window.hide()

    def test_a_selected_table_row_is_orange_not_the_system_highlight(self, make_window):
        """Measured, because the stylesheet alone was not enough.

        Qt paints the palette's ``Highlight`` role *underneath* a stylesheet ``background``, and
        on Windows that role is the system blue when focused and a near-white when not -- so a
        translucent orange wash over it came out a pale blue-white. The colour is now
        pre-blended and the palette role is overwritten in ``style_table``.
        """
        from PyQt6.QtGui import QPalette

        import theme

        window = make_window()
        table = window.home.recent
        palette = table.palette()

        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Disabled):
            assert palette.color(group, QPalette.ColorRole.Highlight).name().upper() == (
                theme.SELECTION_ROW.upper()), f"{group} still uses the system highlight"

        assert f"selection-background-color: {theme.SELECTION_ROW}" in window.styleSheet()
        # Readable: this is the whole reason a translucent wash over white was a bug.
        assert theme.contrast_ratio(theme.TEXT_PRIMARY, theme.SELECTION_ROW) >= 4.5

    def test_the_wordmark_and_the_mark_are_optically_aligned(self, make_window):
        """A QLabel centres its *line box*, so all-caps text reads low against a pixmap of the
        same nominal height. The nudge aligns cap heights, which is what the eye compares."""
        window = make_window()
        margins = window.titlebar._title.contentsMargins()

        assert margins.bottom() > 0, "the wordmark needs a descent-sized nudge"
        assert margins.bottom() == window.titlebar._title.fontMetrics().descent()

    def test_the_window_chrome_is_black_with_a_warm_left_edge(self, make_window):
        """The chrome does *not* carry the cards' diagonal wash down its face.

        On a card, inside the content area, that reads as lit. On the window's own frame it read
        as a smear -- the frame is meant to recede so the content comes forward. But entirely
        neutral black beside an orange accent reads as absence rather than as a decision, so the
        chrome gets a horizontal tint that fades out inside the first ~38%: warmth behind the
        logo and the nav items, black everywhere else.
        """
        import theme

        window = make_window()
        qss = window.styleSheet()

        assert f"QFrame#Sidebar {{ background: {theme.chrome_tint()}; }}" in qss
        assert theme.chrome_gradient() not in qss, "no wash down the chrome's face"
        # Still essentially black: the tint must not lift the chrome above the content.
        assert theme.relative_luminance(theme.TINT_CHROME) < theme.relative_luminance(
            theme.BG_ELEVATED), "the frame must stay behind the content"
        assert theme.contrast_ratio(theme.TINT_CHROME, theme.BG_BASE) < 1.25, (
            "chrome and content should differ by tone, not by a visible step")

    def test_the_titlebar_divider_leads_with_the_accent(self, make_window):
        """A full-width accent line would be a stripe. Fading it inside the first quarter makes
        it read as the brand touching the edge of the chrome and then getting out of the way.

        A real 1px widget rather than a ``border-bottom``: Qt cannot put a gradient on a single
        border edge, and ``border-image`` on one side does not render reliably across styles --
        which is why the first attempt at this was invisible.
        """
        import theme

        window = make_window()

        assert window.titlebar_rule.objectName() == "AccentRule"
        assert window.titlebar_rule.height() == 1
        assert theme.accent_rule() in window.styleSheet()
        assert theme.ACCENT_HAIR in theme.accent_rule()
        # Directly under the title bar, above the content.
        root = window.layout()
        assert root.indexOf(window.titlebar_rule) == root.indexOf(window.titlebar) + 1

    def test_the_shell_and_the_chat_panel_use_the_same_divider(self, qt_app, make_window,
                                                               tmp_path):
        """One definition, styled by object name, so the two chromes cannot drift apart."""
        import theme
        from chat_hud import ChatHud

        window = make_window()
        hud = ChatHud(positions_path=tmp_path / "positions.json")
        try:
            assert window.titlebar_rule.objectName() == "AccentRule"
            for line in hud._hairlines:
                assert line.objectName() == "AccentRule"
                assert theme.accent_rule() in line.styleSheet()
        finally:
            hud.hide()
            hud.deleteLater()


# --- Window chrome ---------------------------------------------------------


class TestChrome:
    def test_the_grain_overlay_does_not_swallow_clicks(self, qt_app, make_window):
        """Measured with ``QWidget.childAt``: without the transparency flag a click at a
        button's centre resolves to the grain widget, with it to the button underneath."""
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtWidgets import QPushButton, QWidget

        from shell.widgets import GrainOverlay

        window = make_window()
        assert window.grain.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # And the behaviour that flag buys, reproduced in isolation.
        root = QWidget()
        try:
            root.resize(400, 300)
            button = QPushButton("hit me", root)
            button.setGeometry(50, 50, 120, 40)
            grain = GrainOverlay(root)
            grain.setGeometry(root.rect())
            grain.raise_()
            assert isinstance(root.childAt(QPoint(60, 60)), QPushButton)
        finally:
            root.deleteLater()

    def test_only_one_grain_overlay_per_window(self, make_window):
        """Per-widget grain tiles inconsistently across boundaries and costs a composite on
        every repaint."""
        from shell.widgets import GrainOverlay

        window = make_window()
        assert len(window.findChildren(GrainOverlay)) == 1

    def test_the_grain_tile_is_shared_across_instances(self, qt_app):
        from shell.widgets import GrainOverlay

        assert GrainOverlay.tile() is GrainOverlay.tile()

    def test_titlebar_emits_intent_rather_than_closing_the_window_itself(self, qt_app):
        """A title bar that reached past the window to kill it would bypass ``closeEvent``,
        which is what keeps closing from stopping push-to-talk."""
        from shell.titlebar import TitleBar

        bar = TitleBar()
        try:
            seen = []
            bar.sig_minimise.connect(lambda: seen.append("min"))
            bar.sig_maximise_toggled.connect(lambda: seen.append("max"))
            bar.sig_close.connect(lambda: seen.append("close"))
            bar._minimise.click()
            bar._maximise.click()
            bar._close.click()
            assert seen == ["min", "max", "close"]

            source = dict(shell_sources())["titlebar.py"]
            assert "QApplication.quit" not in source
            assert ".close()" not in source
        finally:
            bar.deleteLater()

    def test_maximise_glyph_describes_what_it_will_do_next(self, qt_app):
        from shell.titlebar import TitleBar

        bar = TitleBar()
        try:
            bar.set_maximised(True)
            assert bar._maximise.toolTip() == "Restore"
            bar.set_maximised(False)
            assert bar._maximise.toolTip() == "Maximise"
        finally:
            bar.deleteLater()

    def test_the_titlebar_says_nimbus_and_nothing_else(self, make_window):
        """It used to append the page name, which the nav rail already shows a few pixels
        below in larger type. A title bar restating what is next to it is noise."""
        window = make_window()
        window.show_page("knowledge")

        assert window.titlebar._title.text() == "Nimbus"
        assert window.titlebar._title.objectName() == "WordMark"
        assert not hasattr(window.titlebar, "_subtitle")
        # Still callable, because show_page calls it on every navigation.
        window.titlebar.set_subtitle("anything")

    def test_the_window_buttons_are_visible_controls(self, make_window):
        """They were TEXT_SECONDARY glyphs on a transparent fill against a near-black bar --
        the first thing every reviewer said was that they could not see them."""
        import theme
        from shell.titlebar import BUTTON_HEIGHT, BUTTON_WIDTH, GlyphButton

        window = make_window()
        qss = window.styleSheet()

        for button in (window.titlebar._minimise, window.titlebar._maximise,
                       window.titlebar._close):
            assert button.size().width() == BUTTON_WIDTH
            assert button.size().height() == BUTTON_HEIGHT
            # The glyph is painted, not typed -- see GlyphButton. `\u2b1c`, the old maximise
            # character, renders in Segoe UI as a solid white block, which is what it looked
            # like. Every text substitute has the same class of problem on some fallback font.
            assert isinstance(button, GlyphButton)
            assert button.text() == "", "text glyphs are what this class exists to avoid"
            assert button._glyph, "a button with no glyph is invisible"
        # A resting background, so the chip reads as a control before it is hovered.
        assert "QPushButton#WindowButton" in qss
        assert theme.rgba(theme.BG_HOVER, 0.55) in qss
        # And the close button's hover is DANGER itself, not a near-miss red.
        assert f"background: {theme.DANGER}" in qss

    def test_resizing_needs_no_dpi_maths_anywhere(self):
        """The OS owns the resize once ``startSystemResize`` is called, so nothing in the shell
        converts a coordinate or caches a device-pixel ratio -- which is what makes dragging between
        monitors at different scaling a non-event.

        Asserted against the source rather than by simulating a drag, because the invariant is an
        *absence*. The geometry of the gutter itself is covered by ``TestResizeGrips``.
        """
        import inspect

        from shell.window import MainWindow, _ResizeGrip

        for target in (MainWindow, _ResizeGrip):
            source = inspect.getsource(target)
            for banned in ("devicePixelRatio", "logicalDotsPerInch", "physicalDotsPerInch"):
                assert banned not in source, f"{target.__name__} touches {banned}"
        assert "startSystemResize" in inspect.getsource(_ResizeGrip)

    def test_a_size_grip_is_present_as_the_visible_fallback(self, make_window):
        """Frameless windows lose the native border; the grip is the affordance that stays
        when there is no native window handle at all."""
        from PyQt6.QtWidgets import QSizeGrip

        window = make_window()
        assert len(window.findChildren(QSizeGrip)) == 1

    def test_the_window_is_frameless_and_styled_from_theme(self, make_window):
        from PyQt6.QtCore import Qt

        window = make_window()
        assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert window.styleSheet(), "the window carries the generated stylesheet"


# --- Settings that gate the shell (§10.1) ----------------------------------


class TestShellSettings:
    def test_the_window_opens_on_startup_by_default(self, first_run_config, monkeypatch):
        """Launching Nimbus shows the window.

        This asserted the opposite, on the reasoning that "a window appearing uninvited on every
        login is how a utility becomes something the user disables". True in general, and not true
        here: nothing starts Nimbus at login -- ``installer/nimbus.iss`` writes no ``Run`` key and no
        Startup shortcut -- so every launch is a deliberate double-click, and starting invisibly
        meant the app never appeared without the user going to hunt for a tray icon.

        The guard is kept and pointed at the new invariant rather than deleted: the thing worth
        protecting is still "the default is a deliberate choice", only the choice changed.
        """
        import shell.window as window_module

        monkeypatch.setattr(
            "config.resolve_setting", lambda name, default=None: default)
        assert window_module.should_open_on_startup() is True

    @pytest.mark.parametrize(
        "value,expected",
        [("on", True), ("ON", True), ("1", True), ("true", True), ("yes", True),
         ("off", False), ("", False), ("maybe", False)],
    )
    def test_shell_on_startup_values(self, monkeypatch, value, expected):
        import shell.window as window_module

        monkeypatch.setattr("config.resolve_setting", lambda name, default=None: value)
        assert window_module.should_open_on_startup() is expected

    def test_startup_falls_back_to_showing_the_window_when_config_is_unreadable(self, monkeypatch):
        """A locked keyring must not make Nimbus invisible.

        The fallback flipped with the default, and deliberately in the same direction: failing
        towards hidden turns a config hiccup into "I clicked Nimbus and nothing happened", which is
        indistinguishable from a crash and is the exact complaint the new default removes.
        """
        import shell.window as window_module

        def boom(*args, **kwargs):
            raise RuntimeError("keyring locked")

        monkeypatch.setattr("config.resolve_setting", boom)
        assert window_module.should_open_on_startup() is True

    def test_the_new_settings_are_read_through_resolve_setting(self):
        """``resolve_setting`` works before a setting is declared in ``config.py``, which is
        what lets this workstream land without editing it (§9.1)."""
        from pathlib import Path

        import shell.window as window_module

        source = Path(window_module.__file__).read_text(encoding="utf-8")
        for setting in ("SHELL_ON_STARTUP", "NAV_SIDE"):
            assert f'resolve_setting("{setting}"' in source

    def test_the_integration_surface_is_documented_in_the_docstring(self):
        """§9.1: this workstream documents what it needs instead of editing ``app.py``."""
        import shell.window as window_module

        doc = window_module.__doc__ or ""
        assert "## INTEGRATION REQUIRED" in doc
        for expected in ("SHELL_ON_STARTUP", "NAV_SIDE", "REDUCE_MOTION",
                         "sig_set_listening", "sig_quit", "nimbus.spec", "hiddenimports"):
            assert expected in doc


# --- The §9.1 integration surface, by name ---------------------------------


class TestIntegrationSurface:
    def test_the_agreed_names_exist_with_the_agreed_shapes(self, make_window):
        """Agent A and the integration pass were promised these exact names."""
        from PyQt6.QtCore import pyqtBoundSignal

        window = make_window()

        assert callable(window.show_page)
        assert callable(window.set_listening)
        assert callable(window.set_provider)
        assert isinstance(window.sig_set_listening, pyqtBoundSignal)
        assert isinstance(window.sig_quit, pyqtBoundSignal)

    def test_the_window_is_constructible_with_no_arguments(self, qt_app, tmp_path):
        """No ``NimbusApp``, no providers, no keyring: it renders honest empty states.

        The real settings form is skipped here for the same reason it is skipped everywhere
        else -- it reads the keyring -- but nothing else is stubbed.
        """
        from shell.window import MainWindow

        window = MainWindow(
            settings_form_factory=stub_form_class(), kb_dir=tmp_path / "kb")
        try:
            assert window.sidebar.selected == "home"
            assert window.is_listening is False
            assert window.account.is_activated is False
        finally:
            window.hide()
            window.deleteLater()

    def test_refresh_re_reads_every_page(self, make_window):
        counts = {"usage": 0}

        def usage():
            counts["usage"] += 1
            return counts["usage"]

        window = make_window(usage_provider=usage)
        before = counts["usage"]
        window.refresh()
        assert counts["usage"] > before
        assert window.home.week_count.text() == str(counts["usage"])


# --- Aero Snap ---------------------------------------------------------------


class TestAeroSnap:
    """Dragging to a screen edge did nothing, and the styles are why.

    Snap is the OS's, not the app's, and the OS only offers it to a window that says it is
    sizable. Measured before the fix: ``GWL_STYLE`` was ``0x96000000`` -- ``WS_POPUP``,
    ``WS_VISIBLE`` and the two clip bits -- against ``0x96CF0000`` for an ordinary Qt window.
    ``FramelessWindowHint`` had stripped ``WS_THICKFRAME`` and ``WS_MAXIMIZEBOX``, so there was
    nothing to snap and no way to maximise from the top edge.

    These tests are Windows-only and skip elsewhere, because there is nothing to assert about a
    Win32 style bit on a platform that has none.
    """

    @pytest.fixture(autouse=True)
    def _windows_only(self):
        import sys

        if not sys.platform.startswith("win"):
            pytest.skip("Win32 window styles")

    @staticmethod
    def _style(window):
        import ctypes
        from ctypes import wintypes

        from shell.window import GWL_STYLE

        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = wintypes.DWORD
        return int(user32.GetWindowLongW(wintypes.HWND(int(window.winId())), GWL_STYLE))

    def test_the_sizable_and_maximise_bits_are_restored(self, qt_app, make_window):
        from shell.window import SNAP_STYLES, WS_MAXIMIZEBOX, WS_THICKFRAME

        window = make_window()
        window.show()
        qt_app.processEvents()

        assert window._snap_enabled is True
        style = self._style(window)
        assert style & WS_THICKFRAME, f"not sizable: 0x{style:08X}"
        assert style & WS_MAXIMIZEBOX, f"cannot maximise from the top edge: 0x{style:08X}"
        assert style & SNAP_STYLES == SNAP_STYLES

    def test_the_frame_does_not_come_back_with_them(self, qt_app, make_window):
        """The measurement that made a ``WM_NCCALCSIZE`` handler unnecessary.

        ``WS_THICKFRAME`` normally means Windows reserves a non-client sizing border, and the
        textbook fix is to intercept ``WM_NCCALCSIZE`` and leave the client rect equal to the
        window rect. Qt's frameless handling already does that, so the code does not. Pinned here
        because the day that stops being true, the symptom is a visible frame around the window
        and nothing in the source says why.

        Also, ``WS_CAPTION`` is asserted absent: adding it would genuinely restore a title bar,
        which is what the custom one exists to replace.
        """
        import ctypes

        from shell.window import WS_THICKFRAME

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        window = make_window()
        window.show()
        qt_app.processEvents()

        handle = ctypes.c_void_p(int(window.winId()))
        window_rect, client_rect = RECT(), RECT()
        ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(window_rect))
        ctypes.windll.user32.GetClientRect(handle, ctypes.byref(client_rect))

        assert self._style(window) & WS_THICKFRAME
        assert (window_rect.right - window_rect.left
                == client_rect.right - client_rect.left)
        assert (window_rect.bottom - window_rect.top
                == client_rect.bottom - client_rect.top)
        assert not self._style(window) & 0x00C00000, "WS_CAPTION would restore a title bar"

    def test_maximising_still_stops_at_the_taskbar(self, qt_app, make_window):
        """The failure a naive frameless-plus-thickframe window has: covering the taskbar."""
        window = make_window()
        window.show()
        qt_app.processEvents()
        window.showMaximized()
        qt_app.processEvents()

        available = (window.screen() or qt_app.primaryScreen()).availableGeometry()
        assert window.geometry().height() <= available.height()
        assert window.geometry().width() <= available.width()

    def test_a_failed_style_change_degrades_to_no_snap(self, make_window, mocker):
        """A Win32 failure must cost the user snapping, never the window."""
        mocker.patch("shell.window.enable_snap_styles", return_value=False)
        window = make_window()
        window.show()
        assert window._snap_enabled is False
        assert window.isVisible()

    def test_enable_snap_styles_tolerates_a_bogus_handle(self):
        from shell.window import enable_snap_styles

        assert enable_snap_styles(0) is False


# --- The chat panel switch on Home -------------------------------------------


class TestChatVisibilitySwitch:
    """"When I ask it something the chat opens again" -- a live show/hide, no restart.

    **Lives in the nav rail**, above the Privacy Guard chip. It started as a ``QCheckBox`` inside
    Home's push-to-talk card, which put a setting about the transcript inside the card that answers
    "is Nimbus listening" -- and a system checkbox was the one control here that looked like it came
    from another decade. The rail's two permanent facts about the session now sit together.

    The switch holds no state, exactly like the power switch: it emits and re-reads the provider.
    Three things move the panel (this switch, Ctrl+Alt+H, the 45s auto-hide), so a cached boolean
    would go stale the first time the panel hid itself.
    """

    def test_the_switch_lives_above_the_privacy_chip(self, make_window):
        """Placement is the point of the change, so it is asserted rather than assumed."""
        window = make_window()
        layout = window.sidebar.layout()
        assert (layout.indexOf(window.sidebar.chat_switch)
                < layout.indexOf(window.sidebar.privacy_status))

    def test_the_switch_emits_and_does_not_hold_state(self, qt_app, make_window):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest

        window = make_window(chat_visible_provider=lambda: False)
        window.show()
        qt_app.processEvents()
        seen = []
        window.sig_set_chat_visible.connect(seen.append)

        switch = window.sidebar.chat_switch
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton,
                         pos=QPoint(switch.width() // 2, switch.height() // 2))
        qt_app.processEvents()

        assert seen == [True]
        # The provider still says hidden, so the view snaps back rather than lying.
        assert switch.is_on() is False

    def test_the_whole_chip_is_the_click_target(self, qt_app, make_window):
        """A 40x22 knob at the bottom of a 216px rail is a small thing to aim at, and the caption
        beside it is dead space otherwise."""
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest

        window = make_window(chat_visible_provider=lambda: False)
        window.show()
        qt_app.processEvents()
        seen = []
        window.sig_set_chat_visible.connect(seen.append)

        switch = window.sidebar.chat_switch
        # The far left of the chip, over the caption rather than the toggle.
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton,
                         pos=QPoint(6, switch.height() // 2))
        qt_app.processEvents()

        assert seen == [True]

    def test_an_external_change_updates_the_switch_without_re_emitting(self, make_window):
        state = {"visible": False}
        window = make_window(chat_visible_provider=lambda: state["visible"])
        seen = []
        window.sig_set_chat_visible.connect(seen.append)

        state["visible"] = True
        window.set_chat_visible(True)

        assert window.sidebar.chat_switch.is_on() is True
        assert seen == [], "reflecting external state must not ask for another change"

    def test_a_provider_that_raises_reads_as_hidden(self, make_window):
        def boom():
            raise RuntimeError("no hud")

        window = make_window(chat_visible_provider=boom)
        assert window.is_chat_visible is False

    def test_refresh_re_reads_the_provider(self, make_window):
        state = {"visible": True}
        window = make_window(chat_visible_provider=lambda: state["visible"])
        window.refresh()
        assert window.sidebar.chat_switch.is_on() is True

        # Ctrl+Alt+H or auto-hide closed it behind the window's back.
        state["visible"] = False
        window.refresh()
        assert window.sidebar.chat_switch.is_on() is False

    def test_changing_page_re_syncs_the_switch(self, make_window):
        """The rail is visible on every page, so it cannot only be right on Home."""
        state = {"visible": True}
        window = make_window(chat_visible_provider=lambda: state["visible"])
        window.show_page("knowledge")
        assert window.sidebar.chat_switch.is_on() is True

        state["visible"] = False
        window.show_page("journal")
        assert window.sidebar.chat_switch.is_on() is False

    def test_the_shortcut_is_in_the_tooltip_not_a_second_line(self, make_window):
        """One line of text, and the shortcut on hover.

        A sub-caption briefly described the state. At 216px minus the toggle that leaves about 130px,
        and "Hidden · Ctrl+Alt+H to show" was elided mid-word -- a caption that cannot finish its
        sentence is worse than none. The state is already legible from the knob and the chip colour;
        the shortcut is the part that needed a home, and hover is it.
        """
        window = make_window(chat_visible_provider=lambda: False)
        window.refresh_chat()
        switch = window.sidebar.chat_switch

        assert not hasattr(switch, "_detail")
        assert "Ctrl+Alt+H" in switch.toolTip()

    def test_the_label_never_changes_with_the_state(self, make_window):
        """A control whose text changes also changes width, and a rail that reflows every time a
        setting moves is its own small distraction. Same rule as the Privacy Guard chip."""
        state = {"visible": False}
        window = make_window(chat_visible_provider=lambda: state["visible"])
        window.refresh_chat()
        before = window.sidebar.chat_switch._label.text()

        state["visible"] = True
        window.refresh_chat()

        assert window.sidebar.chat_switch._label.text() == before
        assert window.sidebar.chat_switch.is_on() is True


# --- The Settings page's white background ------------------------------------


class TestSettingsPagePaintsDark:
    """The third time this exact bug shipped, so this time it is a pixel test.

    "The settings page has this white bg type colour all over it", with a screenshot of
    white-on-white labels. Measured, before the fix: the page rendered ``rgb(240,240,240)`` edge
    to edge -- the Windows default window colour -- and ``page.form.autoFillBackground()``
    reported ``True`` even though nothing in the stylesheet asked for it.

    The cause is documented next to the fix in ``theme.build_qss``: styling a
    ``QAbstractScrollArea`` makes Qt set ``autoFillBackground`` on the widget inside its viewport,
    and that fill comes from the **palette**, not the stylesheet. The
    ``QAbstractScrollArea::viewport`` rule that looked like it covered this never applied at all,
    because ``viewport`` is not a real Qt sub-control.

    Asserting on the stylesheet text would have passed against the broken UI all three times.
    """

    def _rendered(self, qt_app, make_window):
        window = make_window()
        window.show_page("settings")
        window.resize(900, 620)
        window.show()
        qt_app.processEvents()
        return window, window.grab().toImage()

    def test_the_page_renders_dark(self, qt_app, make_window):
        window, image = self._rendered(qt_app, make_window)
        host = window.page_hosts["settings"]
        origin = host.mapTo(window, host.rect().topLeft())

        samples = [
            (origin.x() + 6, origin.y() + 6),
            (origin.x() + host.width() // 2, origin.y() + host.height() // 2),
            (origin.x() + host.width() - 8, origin.y() + host.height() // 2),
        ]
        for x, y in samples:
            if not (0 <= x < image.width() and 0 <= y < image.height()):
                continue
            colour = image.pixelColor(x, y)
            brightness = (colour.red() + colour.green() + colour.blue()) / 3
            assert brightness < 90, (
                f"Settings renders light at ({x},{y}): "
                f"#{colour.red():02X}{colour.green():02X}{colour.blue():02X}")

    def test_qt_does_not_palette_fill_the_scrolled_form(self, qt_app, make_window):
        """The specific mechanism, named so a regression points straight at the cause."""
        window, _ = self._rendered(qt_app, make_window)
        page = window.pages["settings"]
        assert page.form.autoFillBackground() is False
        assert page.scroll.viewport().autoFillBackground() is False

    def test_the_generated_stylesheet_names_the_scrolled_widget(self):
        """A drift guard on the rule itself, since it is one line and easy to tidy away."""
        import theme

        assert "QScrollArea > QWidget > QWidget" in theme.build_qss()


# --- Knowledge page spacing --------------------------------------------------


class TestKnowledgeCardSpacing:
    """"Too much gap above and below the PER APPLICATION heading."

    Not padding. A ``QLabel`` defaults to a *Preferred* vertical policy, and the Knowledge page
    gives its list card ``stretch=1``, so ``QVBoxLayout`` handed the surplus height to everything
    that could grow -- including the heading, which then centred its text inside a band several
    times its own height.
    """

    def test_the_card_header_cannot_absorb_spare_height(self, qt_app):
        from PyQt6.QtWidgets import QSizePolicy

        from shell.widgets import Card

        card = Card("Per application")
        assert card.header is not None
        assert card.header.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed

    def test_the_heading_stays_its_own_height_in_a_stretched_card(self, qt_app, make_window):
        window = make_window()
        window.show_page("knowledge")
        window.resize(1100, 900)
        window.show()
        qt_app.processEvents()

        page = window.pages["knowledge"]
        card = page.hint.parentWidget()
        assert card.header is not None
        # The heading is one line of FONT_SMALL text. Anything approaching double that means the
        # layout is padding it out again.
        assert card.header.height() <= card.header.sizeHint().height() + 4, (
            f"heading is {card.header.height()}px for "
            f"{card.header.sizeHint().height()}px of text")

    def test_the_empty_card_does_not_claim_the_spare_height(self, qt_app, make_window,
                                                            tmp_path):
        """Where the slack goes, and why it moved.

        The first fix put a filler *inside* the card. That stopped the heading ballooning but parked
        the empty space between the intro text and the folder path -- a tall gap in the middle of a
        card holding four lines, which is what the user photographed next. The card now declines the
        stretch while the list is empty and a tail below the cards takes it instead.
        """
        window = make_window(kb_dir=tmp_path / "empty-kb")
        window.show_page("knowledge")
        window.resize(1100, 900)
        window.show()
        qt_app.processEvents()
        page = window.pages["knowledge"]

        assert page.entries() == []
        assert not page.table.isVisibleTo(page)
        assert page._tail.isVisibleTo(page)
        assert page._outer.stretch(page._outer.indexOf(page._list_card)) == 0
        # The card is close to its content height, not stretched down the page.
        assert page._list_card.height() <= page._list_card.sizeHint().height() + 8

        target = tmp_path / "empty-kb"
        target.mkdir(parents=True, exist_ok=True)
        (target / "orionflow.exe.md").write_text("notes", encoding="utf-8")
        page.refresh()
        qt_app.processEvents()

        assert len(page.entries()) == 1
        assert page.table.isVisibleTo(page)
        assert not page._tail.isVisibleTo(page)
        assert page._outer.stretch(page._outer.indexOf(page._list_card)) == 1

    def test_the_intro_is_not_said_three_times(self, qt_app, make_window):
        """The list card explained the naming, the empty state explained it again, and the card
        below explains it in three numbered steps. One short hint is enough."""
        window = make_window()
        page = window.pages["knowledge"]
        assert len(page.hint.text()) < 100
        # The empty state carries the "why", so it stays -- but only while the list is empty.
        assert page.empty.isVisibleTo(page) == (page.entries() == [])


# --- No emoji in the interface -----------------------------------------------


class TestNoEmojiInTheUi:
    """A house rule, asserted rather than remembered.

    Three had accumulated in the Settings form -- a padlock on the "stored locally" line, a pencil
    on the draw-on-screen checkbox, a lightning bolt on the realtime note. They are also a
    rendering risk: Segoe UI Emoji is a separate font with its own metrics, so a glyph in a label
    changes that label's line height, and a variation selector (``U+FE0F``) renders as a blank box
    where the font is missing.

    Scoped to the modules that build widgets, and to string literals only, so the prose in
    docstrings and comments -- which legitimately uses ``§`` and ``⚠`` -- is untouched.
    """

    UI_MODULES = (
        "settings_dialog.py", "chat_hud.py", "onboarding.py", "tray.py", "overlay.py",
        "shell/window.py", "shell/nav.py", "shell/titlebar.py", "shell/widgets.py",
        "shell/pages/home.py", "shell/pages/knowledge.py", "shell/pages/journal.py",
        "shell/pages/settings.py", "shell/pages/account.py",
    )

    EMOJI_RANGES = (
        (0x1F000, 0x1FAFF),   # the pictograph planes: everything here is colour by default
        (0xFE0F, 0xFE0F),     # variation selector-16 -- an explicit "render this as emoji"
        # Miscellaneous Symbols and Dingbats, but only the characters whose *default*
        # presentation is emoji. See the note below on why this is not the whole block.
        (0x2614, 0x2615), (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693),
        (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE), (0x26C4, 0x26C5),
        (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3),
        (0x26F5, 0x26F5), (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705),
        (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E),
        (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0),
        (0x27BF, 0x27BF),
    )
    """What "emoji" means here, and why it is not simply ``0x2600``--``0x27BF``.

    The first version of this banned that whole block and immediately flagged five characters that
    are the interface working as designed: ``\u2715`` on the chat panel's close button, ``\u2691``
    on its pin, and ``\u2713`` / ``\u2717`` in the Journal's recall column. Those have *text*
    presentation by default -- monochrome, drawn from the UI font, inheriting the label's colour
    and metrics -- which is exactly what a painted glyph in a dark theme needs.

    So the guard was narrowed rather than the code changed. The invariant it protects is "nothing
    in the interface renders from the colour emoji font", because that font has its own metrics
    (a glyph in a label silently changes that label's line height) and its own palette, which
    ignores the theme entirely. ``Emoji_Presentation=Yes`` is the property that means exactly
    that, so this is the list of those characters in the dingbats block; it still catches
    ``\u26a1``, one of the three that were removed."""

    def _strings(self, source: str):
        """Every string literal in ``source``, with its line number. AST, not regex.

        A regex over the file cannot tell a string from a comment, and the comments in this
        codebase are full of the characters this test is looking for.
        """
        import ast

        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None and node.body:
                    first = node.body[0]
                    if isinstance(first, ast.Expr):
                        docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                yield node.lineno, node.value

    def _is_emoji(self, character: str) -> bool:
        point = ord(character)
        return any(low <= point <= high for low, high in self.EMOJI_RANGES)

    def test_no_ui_string_contains_an_emoji(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        offences = []
        for relative in self.UI_MODULES:
            path = root / relative
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            for lineno, text in self._strings(source):
                for character in text:
                    if self._is_emoji(character):
                        offences.append(
                            f"{relative}:{lineno} contains U+{ord(character):04X}")
        assert not offences, "emoji in UI strings:\n  " + "\n  ".join(offences)

    def test_the_guard_would_actually_catch_one(self):
        """A guard nobody has seen fail is a guard nobody knows works."""
        # The three that were actually removed from the Settings form.
        assert self._is_emoji("\U0001f512")   # padlock
        assert self._is_emoji("\u26a1")       # high voltage
        assert self._is_emoji("\ufe0f")       # the variation selector on the pencil
        # And the glyphs the interface deliberately paints are not flagged, because they are
        # monochrome text-presentation characters, not colour emoji.
        assert not self._is_emoji("\u2715")   # the chat panel's close
        assert not self._is_emoji("\u2691")   # its pin
        assert not self._is_emoji("\u2713")   # the Journal's recalled tick
        assert not self._is_emoji("\u00a7")   # section sign, used throughout the docstrings
        assert not self._is_emoji("\u2014")   # em dash
        assert not self._is_emoji("\u2192")   # the restart marker
        assert not self._is_emoji("\u21bb")   # and the circular arrow it replaced


# --- The dotted white focus rectangle ----------------------------------------


class TestFocusVisibleOnly:
    """"When I click any option there is a white dotted lining across it."

    It is ``QStyle::PE_FrameFocusRect``, drawn by the ``windowsvista`` style on whatever holds
    focus, and Qt's default ``StrongFocus`` means a *click* gives a button focus -- so every click
    parked that frame somewhere until the next one.

    **It only reproduces when Windows has keyboard cues on.** Measured with
    ``SPI_GETKEYBOARDCUES``: 0 on the development machine, so nothing appeared here until the flag
    was forced on, at which point one click on a nav item changed 186 bright pixels; with the fix,
    0. Windows enables cues for the session as soon as anyone presses Alt or Tab, and some
    accessibility settings leave them on permanently, so "it does not happen for me" proves
    nothing -- which is why the invariant asserted below is behavioural, not visual.

    The invariant: **nothing in the window can be focused by a mouse click**. A focus frame cannot
    be drawn on a widget that does not have focus, so this holds whatever the platform style does.
    """

    def test_no_button_can_be_focused_by_a_click(self, make_window):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QAbstractButton

        window = make_window()
        offenders = [
            f"{type(b).__name__}:{b.objectName() or b.text()[:20]}"
            for b in window.findChildren(QAbstractButton)
            if b.focusPolicy() & Qt.FocusPolicy.ClickFocus
        ]
        assert not offenders, (
            "these take focus on click, so a click leaves a focus frame on them: "
            + ", ".join(offenders))

    def test_a_real_click_leaves_no_focus_behind(self, qt_app, make_window):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = make_window()
        window.show()
        qt_app.processEvents()

        for target in (window.sidebar.items["knowledge"],
                       window.home.export_button,
                       window.home.memory_button):
            window.setFocus()
            qt_app.processEvents()
            QTest.mouseClick(target, Qt.MouseButton.LeftButton)
            qt_app.processEvents()
            assert not target.hasFocus(), f"{target.text()!r} took focus from a click"

    def test_focus_visible_only_converts_and_counts(self, qt_app):
        """Returns how many it changed, so a caller can assert it did something."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget

        from shell.widgets import focus_visible_only

        host = QWidget()
        layout = QVBoxLayout(host)
        strong, already_none = QPushButton("a"), QPushButton("b")
        already_none.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(strong)
        layout.addWidget(already_none)

        assert focus_visible_only(host) == 1
        assert strong.focusPolicy() == Qt.FocusPolicy.TabFocus
        # Widgets that were deliberately taken off the keyboard stay that way.
        assert already_none.focusPolicy() == Qt.FocusPolicy.NoFocus
        # Idempotent -- it is called once at construction, but a second call must be harmless.
        assert focus_visible_only(host) == 0

    def test_the_keyboard_can_still_reach_the_nav_rail(self, qt_app, make_window):
        """``TabFocus`` rather than ``NoFocus``, and this is the difference.

        ``NoFocus`` would have fixed the pixels by taking the nav rail away from keyboard users.
        The rail is a set of ``autoExclusive`` checkable buttons, which Qt treats as a radio group:
        one tab stop for the group, arrow keys to move inside it. Measured -- Qt drops the unchecked
        items to ``NoFocus`` itself on the first page change to implement exactly that, and Down
        still walks Home -> Knowledge -> Journal.
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = make_window()
        window.show()
        window.activateWindow()
        for _ in range(10):
            qt_app.processEvents()

        window.sidebar.items["home"].setFocus(Qt.FocusReason.TabFocusReason)
        qt_app.processEvents()
        assert window.sidebar.items["home"].hasFocus()

        reached = []
        for _ in range(2):
            QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_Down)
            qt_app.processEvents()
            reached.append(qt_app.focusWidget())

        assert reached[0] is window.sidebar.items["knowledge"]
        assert reached[1] is window.sidebar.items["journal"]

    def test_the_page_scroll_areas_are_not_tab_stops(self, make_window):
        """A page-sized container is nothing to focus, and with cues on it would draw a frame
        around the whole page. Measured before this: Tab from the nav rail landed on a
        ``QScrollArea``."""
        from PyQt6.QtCore import Qt

        window = make_window()
        for name, host in window.page_hosts.items():
            if name == "settings":
                continue
            assert host.focusPolicy() == Qt.FocusPolicy.NoFocus, name

    def test_the_stylesheet_suppresses_the_native_outline(self):
        import theme

        qss = theme.build_qss()
        assert "outline: none" in qss
        # And keyboard focus is still shown, or the fix would be an accessibility regression.
        assert "QPushButton:focus" in qss

    def test_the_first_launch_dialog_gets_the_same_treatment(self, qt_app, mocker, tmp_path):
        """The modal is the other host for the same form, and it had the same frames.

        Easy to miss, because the shell's copy is fixed by ``MainWindow`` walking its own children
        and the dialog is not one of them. Save has to stay in the tab order -- this dialog is modal
        at first launch, so a Save nobody can reach is an unusable app.
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QAbstractButton, QDialogButtonBox

        import settings_dialog as module

        # The real dialog, like ``test_kb_expansion``'s does. Stubbing the form out would test the
        # stub: what needs checking is that the *form's* checkboxes and signup buttons were caught
        # as well as the dialog's own button box.
        dialog = module.SettingsDialog()
        try:
            clickable = [
                b for b in dialog.findChildren(QAbstractButton)
                if b.focusPolicy() & Qt.FocusPolicy.ClickFocus
            ]
            assert not clickable

            box = dialog.findChild(QDialogButtonBox)
            save = box.button(QDialogButtonBox.StandardButton.Save)
            assert save is not None
            assert save.focusPolicy() & Qt.FocusPolicy.TabFocus
        finally:
            dialog.deleteLater()

    def test_text_entry_keeps_click_focus(self, qt_app):
        """Deliberately untouched: a field you cannot click into is not a field.

        Those are fully styled by the stylesheet, so they get the theme's focus border rather than
        a dotted frame -- which is why they did not need the treatment.
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QComboBox, QLineEdit, QVBoxLayout, QWidget

        from shell.widgets import focus_visible_only

        host = QWidget()
        layout = QVBoxLayout(host)
        line, combo = QLineEdit(), QComboBox()
        layout.addWidget(line)
        layout.addWidget(combo)

        focus_visible_only(host)

        assert line.focusPolicy() & Qt.FocusPolicy.ClickFocus
        assert combo.focusPolicy() & Qt.FocusPolicy.ClickFocus


# --- The push-to-talk chord must not press buttons ---------------------------


class TestHotkeyChordGuard:
    """"When I press Ctrl+Alt+Space the push-to-talk listens and then pauses."

    Two correct decisions meeting badly. The global hook is deliberately ``suppress=False``
    (pynput's suppress flag is all-or-nothing and would block every key on the system), so the
    chord reaches the focused widget as well as Nimbus -- and ``QAbstractButton::keyPressEvent``
    activates on ``Key_Space`` **without looking at modifiers**, so a focused button treats
    Ctrl+Alt+Space as a click.

    Measured before the fix, all three real: with the power switch focused the chord emitted
    ``sig_set_listening(False)``, pausing Nimbus at the moment the user asked it to listen; with
    "Open memory folder" focused it opened Explorer; with a nav item focused it changed page. And
    ``focusWidget()`` on activation was the ``PowerSwitch``, so it fired on the first question after
    the window was opened.
    """

    def _shown(self, qt_app, make_window, **kwargs):
        window = make_window(**kwargs)
        window.show()
        window.activateWindow()
        for _ in range(10):
            qt_app.processEvents()
        return window

    def test_the_chord_does_not_toggle_the_power_switch(self, qt_app, make_window):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = self._shown(qt_app, make_window, listening_provider=lambda: True)
        asked = []
        window.sig_set_listening.connect(asked.append)

        window.home.toggle.setFocus(Qt.FocusReason.TabFocusReason)
        qt_app.processEvents()
        QTest.keyClick(
            qt_app.focusWidget() or window, Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        qt_app.processEvents()

        assert asked == [], "the push-to-talk chord paused push-to-talk"

    def test_the_chord_does_not_fire_an_action_button(self, qt_app, make_window):
        """Worse than the switch: this one opens Explorer on every question."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = self._shown(qt_app, make_window)
        fired = []
        window.sig_open_memory_folder.connect(lambda: fired.append(1))

        window.home.memory_button.setFocus(Qt.FocusReason.TabFocusReason)
        qt_app.processEvents()
        QTest.keyClick(
            qt_app.focusWidget() or window, Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        qt_app.processEvents()

        assert fired == []

    def test_the_chord_does_not_change_page(self, qt_app, make_window):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = self._shown(qt_app, make_window)
        requested = []
        window.sidebar.sig_page_requested.connect(requested.append)

        window.sidebar.items["journal"].setFocus(Qt.FocusReason.TabFocusReason)
        qt_app.processEvents()
        QTest.keyClick(
            qt_app.focusWidget() or window, Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        qt_app.processEvents()

        assert requested == []

    def test_plain_space_still_activates(self, qt_app, make_window):
        """The guard must not cost keyboard accessibility -- only the chord is swallowed."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = self._shown(qt_app, make_window, listening_provider=lambda: True)
        asked = []
        window.sig_set_listening.connect(asked.append)

        window.home.toggle.setFocus(Qt.FocusReason.TabFocusReason)
        qt_app.processEvents()
        QTest.keyClick(qt_app.focusWidget() or window, Qt.Key.Key_Space)
        qt_app.processEvents()

        assert asked == [False]

    def test_the_guard_follows_a_remapped_hotkey(self, qt_app, make_window):
        """Built from the configured chord, not a literal.

        A user on Ctrl+Shift+F9 must be protected from *their* chord, and Ctrl+Alt+Space should go
        back to being an ordinary key combination in their window.
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        window = self._shown(
            qt_app, make_window,
            hotkey_provider=lambda: "ctrl+shift+f9",
            listening_provider=lambda: True)
        asked = []
        window.sig_set_listening.connect(asked.append)

        window.home.toggle.setFocus(Qt.FocusReason.TabFocusReason)
        qt_app.processEvents()
        QTest.keyClick(
            qt_app.focusWidget() or window, Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        qt_app.processEvents()

        assert asked == [False], "an unrelated chord should not be swallowed"

    def test_opening_the_window_arms_nothing(self, qt_app, make_window):
        """``focusWidget()`` on activation used to be the power switch.

        The window claims focus itself, so nothing is armed until the user presses Tab. Worth its
        own test because it is the reason the chord bug fired on the very first question.
        """
        window = self._shown(qt_app, make_window)
        focused = qt_app.focusWidget()
        assert focused is window or focused is None, (
            f"{type(focused).__name__} is armed as soon as the window opens")

    def test_the_guard_lifts_while_settings_records_a_hotkey(self, qt_app, make_window):
        """Otherwise the capture button silently ignores the chord already in use."""
        window = make_window()

        assert window._hotkey_guard is not None
        assert window._hotkey_guard.isEnabled()

        window.settings.sig_hotkey_capture_changed.emit(True)
        assert not window._hotkey_guard.isEnabled()

        window.settings.sig_hotkey_capture_changed.emit(False)
        assert window._hotkey_guard.isEnabled()

    def test_an_unparseable_hotkey_still_guards_the_default(self, qt_app, make_window):
        """A corrupt setting must not leave the window unguarded and pressing its own buttons."""
        window = make_window(hotkey_provider=lambda: "not a chord")
        assert window._hotkey_guard is not None
        assert window._hotkey_guard.key().toString() == "Ctrl+Alt+Space"


# --- Card spacing, generically -----------------------------------------------


class TestNoCardBalloonsItsHeading:
    """One guard for the whole class of bug, because fixing it page by page did not hold.

    Knowledge was fixed first. Home and Journal then showed the same gap, for the same reason and in
    the same shape -- a card given ``stretch=1`` whose only expanding child is a table that is
    hidden while empty, so ``QVBoxLayout`` shares the surplus across the heading and the labels
    instead. Three ad-hoc fixes and no guard is how the fourth one ships.

    This walks **every card on every page** in both the empty and populated states and asserts no
    heading is taller than the text in it.
    """

    SLACK = 6
    """Pixels of tolerance over ``sizeHint``. Enough for a font-metrics rounding difference, far
    less than the tens of pixels a ballooned heading gains."""

    def _headings(self, page):
        from shell.widgets import Card

        return [card for card in page.findChildren(Card) if card.header is not None]

    def _check(self, window, qt_app, name):
        window.show_page(name)
        qt_app.processEvents()
        page = window.pages[name]
        offenders = []
        for card in self._headings(page):
            wanted = card.header.sizeHint().height()
            actual = card.header.height()
            if actual > wanted + self.SLACK:
                offenders.append(
                    f"{card.header.text()!r} is {actual}px for {wanted}px of text")
        return offenders

    def test_no_heading_balloons_with_empty_data(self, qt_app, make_window, tmp_path):
        """The state that produced every instance of this: nothing to show yet."""
        window = make_window(
            kb_dir=tmp_path / "empty-kb",
            recent_provider=lambda: (),
            review_queue_provider=None,
        )
        window.resize(1200, 900)
        window.show()
        qt_app.processEvents()

        for name in ("home", "knowledge", "journal", "account"):
            assert not self._check(window, qt_app, name), \
                f"{name}: " + "; ".join(self._check(window, qt_app, name))

    def test_no_heading_balloons_with_data(self, qt_app, make_window, tmp_path):
        kb = tmp_path / "full-kb"
        kb.mkdir(parents=True, exist_ok=True)
        (kb / "orionflow.exe.md").write_text("notes", encoding="utf-8")

        window = make_window(
            kb_dir=kb,
            recent_provider=lambda: [
                {"question": "what is this", "app": "orionflow.exe",
                 "when": "2m ago", "target": "1200,800"},
            ],
        )
        window.resize(1200, 900)
        window.show()
        qt_app.processEvents()

        for name in ("home", "knowledge", "journal"):
            assert not self._check(window, qt_app, name), \
                f"{name}: " + "; ".join(self._check(window, qt_app, name))

    def test_the_spare_height_goes_below_the_cards_when_empty(self, qt_app, make_window,
                                                              tmp_path):
        """The structural version, on all three pages that have a table.

        Each pairs its table with a page-level tail and moves the stretch between them, so a card
        with nothing to list hugs its content and the slack lands under the cards. A page that adds
        a table without doing this fails here rather than waiting for someone to photograph a gap.
        """
        window = make_window(kb_dir=tmp_path / "empty-kb", recent_provider=lambda: ())
        window.resize(1100, 900)
        window.show()
        qt_app.processEvents()

        for page_name, table_attr, card_attr in (
            ("home", "recent", "_recent_card"),
            ("knowledge", "table", "_list_card"),
            ("journal", "items", "_items_card"),
        ):
            window.show_page(page_name)
            qt_app.processEvents()
            page = window.pages[page_name]
            table = getattr(page, table_attr)
            card = getattr(page, card_attr)

            assert not table.isVisibleTo(page), f"{page_name}: table shown with no rows"
            assert page._tail.isVisibleTo(page), f"{page_name}: no tail to take the slack"
            assert page._outer.stretch(page._outer.indexOf(card)) == 0, page_name
            assert card.height() <= card.sizeHint().height() + 8, (
                f"{page_name}: card is {card.height()}px for "
                f"{card.sizeHint().height()}px of content")


# --- The resize cursor that would not go away --------------------------------


class TestResizeGrips:
    """"When I click anywhere in Nimbus my cursor becomes the resize cursor and stays."

    The window used to set the resize cursor **on itself** from ``mouseMoveEvent``. ``setCursor`` on
    a parent applies to every child that has not set its own, so the whole content area inherited
    it -- and clearing it needed another move event over the *window*, which never arrives once the
    pointer is over a card. One brush past an edge and every page had a resize cursor until the
    pointer happened to cross the gutter again.

    Eight small children with their own cursors is deterministic: Qt sets the cursor on enter and
    restores it on leave, per widget, with no state of ours involved.
    """

    def test_the_window_never_sets_a_cursor_on_itself(self, make_window):
        """The whole bug in one assertion. A cursor on the window is inherited by every child."""
        from PyQt6.QtCore import Qt

        window = make_window()
        window.resize(1000, 700)
        assert not window.testAttribute(Qt.WidgetAttribute.WA_SetCursor), (
            "the window owns a cursor again, which every child without one will inherit")

    def test_main_window_has_no_set_cursor_call(self):
        """A drift guard, because re-adding ``self.setCursor`` is a one-line regression.

        Scoped to ``MainWindow`` with ``inspect``, not to the file: ``_ResizeGrip`` sets a cursor on
        *itself* and must keep doing so -- that is the fix. A file-wide grep would ban the cure
        along with the disease.
        """
        import inspect

        from shell.window import MainWindow

        source = inspect.getsource(MainWindow)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "self.setCursor(" not in stripped, stripped

    def test_there_is_a_grip_for_every_edge_and_corner(self, make_window):
        window = make_window()
        assert len(window._grips) == 8
        assert len({grip.edges for grip in window._grips}) == 8

    def test_each_grip_carries_the_right_cursor(self, make_window):
        from PyQt6.QtCore import Qt

        window = make_window()
        wanted = {
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        }
        for grip in window._grips:
            assert grip.cursor().shape() == wanted[grip.edges], grip.edges

    def test_the_grips_line_the_frame_and_leave_the_content_alone(self, qt_app, make_window):
        from PyQt6.QtCore import QPoint

        window = make_window()
        window.resize(1000, 700)
        window.show()
        qt_app.processEvents()

        for grip in window._grips:
            box = grip.geometry()
            assert box.width() > 0 and box.height() > 0
            # Every grip touches an edge of the window.
            assert (box.left() == 0 or box.top() == 0
                    or box.right() >= window.width() - 1
                    or box.bottom() >= window.height() - 1), box

        # And nothing covers the middle, where the content is.
        centre = QPoint(window.width() // 2, window.height() // 2)
        assert not any(grip.geometry().contains(centre) for grip in window._grips)

    def test_the_corners_are_bigger_than_the_edges(self, qt_app, make_window):
        """Two 4px strips crossing leave a 4x4 corner nobody can hit -- the same arithmetic that
        hid the chat panel's diagonal cursor."""
        from PyQt6.QtCore import Qt  # noqa: F401  (edges are Qt.Edge values)
        from shell.window import RESIZE_MARGIN

        window = make_window()
        window.resize(1000, 700)
        window.show()
        qt_app.processEvents()

        assert window.CORNER_SIZE > RESIZE_MARGIN
        corners = [g for g in window._grips if bin(int(g.edges.value)).count("1") == 2]
        assert len(corners) == 4
        for grip in corners:
            assert grip.width() == window.CORNER_SIZE
            assert grip.height() == window.CORNER_SIZE

    def test_maximised_hides_them(self, qt_app, make_window):
        """A maximised window has no edge to drag, and a hidden grip cannot show a cursor."""
        window = make_window()
        window.show()
        qt_app.processEvents()
        window.showMaximized()
        qt_app.processEvents()

        assert all(not grip.isVisible() for grip in window._grips)

        window.showNormal()
        qt_app.processEvents()
        assert all(grip.isVisibleTo(window) for grip in window._grips)

    def test_a_grip_is_not_a_tab_stop(self, make_window):
        from PyQt6.QtCore import Qt

        window = make_window()
        assert all(g.focusPolicy() == Qt.FocusPolicy.NoFocus for g in window._grips)


# --- Presentation pass -------------------------------------------------------


class TestSidebarSwitchLook:
    """The chat chip: shaded in both states, grain over it, one line of text."""

    def test_it_draws_the_grain_tile_itself(self, qt_app, make_window, mocker):
        """Not by adding a ``GrainOverlay`` child.

        That class documents its own rule -- one instance per window, never per widget -- because a
        per-widget overlay is another widget above the controls it covers and another composite on
        every repaint. The chip draws the same tile inside its own ``paintEvent`` instead, which is
        why this asserts the tile was *used* rather than that a widget exists.
        """
        from shell.widgets import GrainOverlay, SidebarSwitch

        window = make_window()
        switch = window.sidebar.chat_switch
        spy = mocker.spy(GrainOverlay, "tile")

        switch.grab()
        qt_app.processEvents()

        assert spy.call_count >= 1
        assert not switch.findChildren(GrainOverlay), (
            "a per-widget GrainOverlay is the thing that class tells you not to do")
        assert isinstance(switch, SidebarSwitch)

    def test_both_states_are_shaded_not_flat(self):
        """A corner gradient in both states, so the chip reads as a lit surface rather than a box.

        Off it is the rail's own black with a warm corner; on it is the same shape with more accent
        in it. A flat fill in either state read as a rectangle drawn on top of the rail.
        """
        import theme

        for on in (False, True):
            for hover in (False, True):
                value = theme.sidebar_switch_gradient(on=on, hover=hover)
                assert value.startswith("qlineargradient"), (on, hover)
                # Cornered, not vertical or horizontal: both axes travel.
                assert "x2:1" in value and "y2:1" in value, (on, hover)

    def test_on_is_dimmer_than_the_flat_wash_it_replaced(self):
        """"It just goes orange and it's too bright."

        The old state was a flat 14% accent wash. The gradient now peaks at 20% and falls to 7%, so
        the *average* is below what it replaced while the lit corner is brighter -- which is what
        makes it read as shaded rather than simply darker.
        """
        import theme

        previous = 0.14
        peak, mid, deep = 0.20, 0.12, 0.07
        assert peak > previous, "the lit corner should still be brighter than the old flat fill"
        assert (peak + mid + deep) / 3 < previous
        assert theme.SIDEBAR_SWITCH_ON_GLOW == theme.blend(
            theme.ACCENT, theme.CHROME_FLAT, peak)
        assert theme.SIDEBAR_SWITCH_ON_DEEP == theme.blend(
            theme.ACCENT, theme.CHROME_FLAT, deep)

    def test_off_sits_on_the_rail_colour(self):
        """Off, the chip is the rail's own black -- not a lift off it, which looked like an empty
        box with a border round nothing."""
        import theme

        assert theme.SIDEBAR_SWITCH == theme.CHROME_FLAT


class TestSettingsPresentation:
    """Spacing and headings on the Settings form."""

    def test_the_experimental_group_has_no_checkbox(self, qt_app):
        """A group box's check indicator means "this group is enabled", not "expanded".

        It was checkable, and its check state hid its own contents -- so an unchecked group could be
        read as *the features being off*, when the toggles inside had their own state and were merely
        hidden. It also put a checkbox in a heading, which is what made the section look like a loose
        control floating between two panels, and it was the last thing drawing the platform's dotted
        focus rectangle.
        """
        from PyQt6.QtWidgets import QGroupBox

        from settings_dialog import SettingsForm

        form = SettingsForm()
        try:
            boxes = form.findChildren(QGroupBox)
            assert boxes, "no group boxes found"
            assert not any(box.isCheckable() for box in boxes), (
                [b.title() for b in boxes if b.isCheckable()])
        finally:
            form.deleteLater()

    def test_the_experimental_options_are_always_listed(self, qt_app):
        """They used to be hidden until the group was checked. All off by default is what actually
        protects someone who has not gone looking."""
        from settings_dialog import SettingsForm

        form = SettingsForm()
        try:
            assert len(form._experimental_checkboxes) == 4
            for setting, box in form._experimental_checkboxes.items():
                assert box.isVisibleTo(form), setting
        finally:
            form.deleteLater()

    def test_group_headings_are_bold_and_primary(self):
        """Semibold secondary grey at this size was quieter than the body text underneath it, which
        is the wrong way round for a heading."""
        import theme

        qss = theme.build_qss()
        title = qss.split("QGroupBox::title")[1].split("}")[0]
        assert f"font-weight: {theme.WEIGHT_BOLD}" in title
        assert f"color: {theme.TEXT_PRIMARY}" in title

    def test_sections_are_spaced_apart(self, qt_app):
        """At Qt's default spacing the form read as one undifferentiated column."""
        import theme
        from settings_dialog import SettingsForm

        form = SettingsForm()
        try:
            assert form.layout().spacing() >= theme.SPACE[2]
        finally:
            form.deleteLater()


class TestScrollbarUsesTheAccent:
    """Grey furniture until you reach for it, accent while you are using it."""

    def test_the_handle_is_grey_at_rest_and_accent_in_use(self):
        import theme

        qss = theme.build_qss()
        assert f"QScrollBar::handle:vertical:hover {{ background: {theme.ACCENT}; }}" in qss
        assert (f"QScrollBar::handle:vertical:pressed {{ background: {theme.ACCENT_PRESS}; }}"
                in qss)
        # And the resting state stays neutral, or the accent stops meaning "in use".
        resting = qss.split("QScrollBar::handle:vertical {")[1].split("}")[0]
        assert theme.BORDER_STRONG in resting
        assert theme.ACCENT not in resting

    def test_the_chat_panel_restates_it(self, make_hud_free_theme=None):
        """The transcript carries its own stylesheet, and a local ``QScrollArea`` rule stops the
        application sheet's scrollbar rules reaching its children."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "chat_hud.py").read_text(
            encoding="utf-8")
        assert "QScrollBar::handle:vertical:hover" in source
        assert "QScrollBar::handle:vertical:pressed" in source


class TestTitleBarLockup:
    """The mark and the wordmark move as one; only their distance from the edge changed."""

    def test_the_lockup_sits_closer_to_the_window_edge(self, qt_app, make_window):
        import theme

        window = make_window()
        window.show()
        qt_app.processEvents()
        left = window.titlebar.layout().contentsMargins().left()
        assert left == theme.SPACE[2]

    def test_the_gap_inside_the_lockup_is_unchanged(self, qt_app, make_window):
        """The pair moved together. 4px between the trimmed mark and the N is the measured value
        that reads as one lockup rather than two elements."""
        import theme

        window = make_window()
        window.show()
        qt_app.processEvents()
        bar = window.titlebar
        gap = bar._title.x() - (bar.mark.x() + bar.mark.width())
        assert 0 < gap <= theme.SPACE[2]


class TestKnowledgePathSpacing:
    def test_there_is_air_above_the_folder_path(self, qt_app, make_window, tmp_path):
        """The text above explains what the card is for; the path states where files go. At the
        card body's default spacing the two ran together as one block."""
        window = make_window(kb_dir=tmp_path / "kb")
        window.show_page("knowledge")
        window.resize(1100, 860)
        window.show()
        qt_app.processEvents()

        page = window.pages["knowledge"]
        above = page.empty if page.empty.isVisibleTo(page) else page.table
        gap = page.path_label.y() - (above.y() + above.height())
        assert gap >= 12, f"only {gap}px above the folder path"


# --- The restart marker's glyph ----------------------------------------------


class TestRestartMarkerGlyph:
    """"The arrow looks pixelated when it's small, and it's cut off at the bottom."

    Both symptoms, one cause. The marker was ``\\u21bb`` CLOCKWISE OPEN CIRCLE ARROW, which is not in
    the interface font -- measured, it exists only in **Segoe UI Symbol**, a legacy font Qt falls
    back to. Different hinting from the surrounding text is the pixelation; different vertical
    metrics is what put its ink outside the label's line box.

    ``shell/titlebar.py`` documents the same trap for ``\\u2b1c``, which Segoe UI renders as a solid
    white block. This class is the guard that stops the marker drifting back out of the text font.
    """

    def _families(self):
        import theme

        return [name.strip().strip('"') for name in theme.FONT_STACK.split(",")]

    def test_the_marker_is_in_a_font_the_app_asks_for(self, qt_app):
        """The invariant. Not "is it in some font on this machine" -- Qt will always find one -- but
        "is it in a family this application actually names".

        The icon fonts are part of ``FONT_STACK`` for exactly this reason: naming them is what makes
        Qt's ordered fallback decide which font supplies a private-use codepoint, instead of leaving
        it to whichever icon font happens to be installed.
        """
        from PyQt6.QtGui import QFont, QRawFont

        import theme
        from settings_dialog import RESTART_MARKER

        glyph = RESTART_MARKER.strip()
        assert len(glyph) == 1

        holders = []
        for family in self._families():
            font = QFont(family)
            font.setPointSize(theme.FONT_SMALL)
            try:
                if QRawFont.fromFont(font).supportsCharacter(ord(glyph)):
                    holders.append(family)
            except Exception:
                continue
        assert holders, (
            f"U+{ord(glyph):04X} is in none of {self._families()}, so it will render from a "
            "fallback font with different hinting and different vertical metrics")

    def test_the_marker_sits_at_text_scale(self, qt_app):
        """The complaint that retired the icon font: "it looks too big and weird".

        Measured as ink height against the **cap height of the surrounding text**. An icon font is
        drawn to fill the em box while a text character's capitals occupy roughly 70% of it, so an
        icon glyph inline runs 1.36-1.44x the letters beside it. A marker appended to a label has to
        sit at the scale of the label.

        There is no way to shrink one run of a plain-text label, and ``QCheckBox`` -- which carries
        nine of these markers -- does not support rich text. So the scale has to come from the glyph
        itself, which is why this is a test and not a stylesheet rule.
        """
        from PyQt6.QtCore import QRect, Qt
        from PyQt6.QtGui import QFont, QFontMetrics, QImage, QPainter

        import theme
        from settings_dialog import RESTART_MARKER

        glyph = RESTART_MARKER.strip()
        for size in (theme.FONT_SMALL, theme.FONT_BODY, theme.FONT_TITLE):
            font = QFont()
            font.setFamilies(self._families())
            font.setPointSize(size)
            metrics = QFontMetrics(font)

            box = QRect(0, 0, max(12, metrics.horizontalAdvance(glyph)) + 12,
                        metrics.height() + 12)
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(0xFF000000)
            painter = QPainter(image)
            painter.setFont(font)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), glyph)
            painter.end()

            rows = [y for y in range(image.height())
                    if any(image.pixelColor(x, y).red() > 60 for x in range(image.width()))]
            assert rows, f"{size}pt: the marker rendered no ink"
            ratio = (rows[-1] - rows[0] + 1) / max(1, metrics.capHeight())
            assert ratio <= 1.05, (
                f"{size}pt: the marker is {ratio:.2f}x cap height -- an icon-font glyph reads as "
                "oversized beside a label")
            assert ratio >= 0.6, (
                f"{size}pt: the marker is only {ratio:.2f}x cap height, too small to read as a "
                "symbol")

    def test_the_marker_is_a_circular_arrow_not_a_straight_one(self):
        """The marker means "reloads on the next start", and a straight arrow does not say that.

        Pinned by codepoint because it is the one property of this glyph that is a *design* decision
        rather than a measurement -- everything else about the choice was settled with numbers.
        """
        from settings_dialog import RESTART_MARKER

        assert ord(RESTART_MARKER.strip()) == 0x27F3

    def test_the_marker_is_a_real_glyph_and_not_a_notdef_box(self, qt_app):
        """The risk that comes with a private-use codepoint.

        ``U+E72C`` is in Windows' icon font, and private-use ranges are exactly where font fallback
        is ambiguous -- any installed icon font may claim the same codepoint. So this renders the
        marker and compares its ink against a codepoint no font can have. Equal ink means a box.
        """
        from PyQt6.QtCore import QRect, Qt
        from PyQt6.QtGui import QFont, QFontMetrics, QImage, QPainter

        import theme
        from settings_dialog import RESTART_MARKER

        def ink(codepoint: int) -> int:
            font = QFont()
            font.setFamilies([n.strip().strip('"') for n in theme.FONT_STACK.split(",")])
            font.setPointSize(theme.FONT_BODY)
            metrics = QFontMetrics(font)
            glyph = chr(codepoint)
            box = QRect(0, 0, max(10, metrics.horizontalAdvance(glyph)) + 10,
                        metrics.height() + 10)
            image = QImage(box.size(), QImage.Format.Format_ARGB32)
            image.fill(0xFF000000)
            painter = QPainter(image)
            painter.setFont(font)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), glyph)
            painter.end()
            return sum(1 for y in range(image.height()) for x in range(image.width())
                       if image.pixelColor(x, y).red() > 60)

        marker = ink(ord(RESTART_MARKER.strip()))
        notdef = ink(0x10FFFD)  # plane 16 private use: no font has this
        assert marker > 0
        assert marker != notdef, (
            f"the marker renders with the same ink as a notdef box ({marker}px) -- no installed "
            "font supplies it")

    def test_the_marker_fits_inside_its_line_box(self, qt_app):
        """The clipping, measured where it was worst: the 15pt teaching-mode label."""
        from PyQt6.QtGui import QFont, QFontMetrics

        import theme
        from settings_dialog import RESTART_MARKER

        glyph = RESTART_MARKER.strip()
        for size in (theme.FONT_SMALL, theme.FONT_BODY, theme.FONT_TITLE):
            font = QFont()
            font.setFamilies([n.strip().strip('"') for n in theme.FONT_STACK.split(",")])
            font.setPointSize(size)
            metrics = QFontMetrics(font)
            ink = metrics.tightBoundingRect(glyph)
            # `tightBoundingRect` is relative to the baseline: negative is above it.
            assert ink.bottom() <= metrics.descent(), (
                f"{size}pt: ink reaches {ink.bottom()} below the baseline, past the "
                f"{metrics.descent()}px descent")
            assert -ink.top() <= metrics.ascent(), (
                f"{size}pt: ink reaches {-ink.top()} above the baseline, past the "
                f"{metrics.ascent()}px ascent")

    def test_the_legend_and_the_page_status_use_the_constant(self):
        """Three places said "↻" and only one of them was the marker.

        A hardcoded copy in ``shell/pages/settings.py`` would have gone on explaining a symbol no
        label used any more, which is the failure mode of a legend written out by hand.
        """
        from pathlib import Path

        from settings_dialog import RESTART_MARKER, RESTART_NOTE

        assert RESTART_MARKER.strip() in RESTART_NOTE

        root = Path(__file__).resolve().parents[1]
        for relative in ("shell/pages/settings.py", "settings_dialog.py"):
            source = (root / relative).read_text(encoding="utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "\\u21bb" in stripped:
                    continue  # comments and docstrings may name the old glyph
                assert "\u21bb" not in stripped, f"{relative}: {stripped}"


class TestNavLabelTypography:
    """The rail's labels were body copy: default weight, secondary grey, no tracking."""

    def test_nav_items_are_weighted_tracked_and_brighter(self):
        import theme

        qss = theme.build_qss()
        block = qss.split("QPushButton#NavItem {")[1].split("}")[0]
        assert f"font-weight: {theme.WEIGHT_SEMIBOLD}" in block
        assert "letter-spacing" in block
        assert theme.NAV_LABEL in block

    def test_the_nav_weight_is_not_silently_ignored(self, qt_app):
        """The nav's weight request must actually make the text heavier than body copy.

        The bug this exists for: the rule originally asked for ``WEIGHT_MEDIUM`` and changed nothing,
        because Segoe UI has 300, 400, 600 and 700 and no 500 -- ``QFontInfo`` reported 400 straight
        back. A stylesheet line that silently does nothing is worse than one that does the wrong
        thing, because it looks like it was tried.

        ## Why this no longer asserts an exact weight

        It used to require the resolved weight to *equal* 600, and that made the suite fail on any
        machine without a Segoe UI Semibold face installed. Measured on two: a Windows 10 desktop
        resolves 600 to 600, while the Windows Server image CI runs on has a reduced font set and
        substitutes 700. Both are correct -- the label is heavier than body text either way, which is
        the entire design intent -- so the exact number was a fact about my font folder, not about
        Nimbus.

        The 500-is-missing detail is likewise a *reason for the choice* rather than something to pin.
        Asserting that 500 stays unavailable would fail on a machine with Inter installed, and Inter is
        in our own font stack.
        """
        from PyQt6.QtGui import QFont, QFontInfo

        import theme

        family = [n.strip().strip('"') for n in theme.FONT_STACK.split(",")]
        font = QFont()
        font.setFamilies(family)
        font.setPointSize(theme.FONT_BODY)
        font.setWeight(QFont.Weight(theme.WEIGHT_SEMIBOLD))

        resolved = QFontInfo(font).weight()
        assert resolved > theme.WEIGHT_REGULAR, (
            f"asked for {theme.WEIGHT_SEMIBOLD} and the font resolved {resolved}, which is no heavier "
            "than body copy -- the nav rule is doing nothing"
        )

    def test_the_resting_colour_sits_between_secondary_and_primary(self):
        """Bright enough to read as a control, still clearly behind the selected item."""
        import theme

        for channel in range(3):
            secondary = theme.parse_hex(theme.TEXT_SECONDARY)[channel]
            primary = theme.parse_hex(theme.TEXT_PRIMARY)[channel]
            nav = theme.parse_hex(theme.NAV_LABEL)[channel]
            assert secondary < nav < primary, (channel, secondary, nav, primary)

    def test_the_selected_item_is_still_clearly_ahead(self):
        """Three signals, not one: primary colour, semibold, and the accent wash."""
        import theme

        qss = theme.build_qss()
        checked = qss.split("QPushButton#NavItem:checked {")[1].split("}")[0]
        assert f"color: {theme.TEXT_PRIMARY}" in checked
        assert theme.ACCENT_WASH in checked
        # Three signals is enough. The selected item does not also get heavier -- everything is
        # semibold now, and a fourth signal on one row would be noise.
        assert theme.NAV_LABEL not in checked

    def test_the_font_stack_still_resolves_to_a_real_family(self, qt_app):
        """A stack whose first entry is missing is fine; one where *none* resolves is not.

        Measured on this machine: "Segoe UI Variable Text" leads ``FONT_STACK`` and is not installed,
        and Qt honours the rest of the list through ``QFont::setFamilies`` -- so the interface renders
        in Segoe UI. Worth pinning, because the failure mode is silent substitution to Tahoma.
        """
        from PyQt6.QtGui import QFontDatabase

        import theme

        families = [name.strip().strip('"') for name in theme.FONT_STACK.split(",")]
        installed = set(QFontDatabase.families())
        assert any(name in installed for name in families), families


class TestGrainOnBothRailComponents:
    """The rail sits outside the window's grain overlay, which covers the page stack only.

    So without this its two permanent components are the only surfaces in the interface with no
    texture -- and after the switch got its own, one textured chip sat beside one flat one.
    """

    def test_both_components_share_one_implementation(self, make_window):
        from shell.widgets import GrainedFrame

        window = make_window()
        assert isinstance(window.sidebar.chat_switch, GrainedFrame)
        assert isinstance(window.sidebar.privacy_status, GrainedFrame)

    def test_neither_adds_a_per_widget_overlay(self, make_window):
        """``GrainOverlay`` states the rule: one instance per window, never per widget."""
        from shell.widgets import GrainOverlay

        window = make_window()
        assert not window.sidebar.chat_switch.findChildren(GrainOverlay)
        assert not window.sidebar.privacy_status.findChildren(GrainOverlay)
        # The window keeps exactly one, over the page stack.
        assert len(window.findChildren(GrainOverlay)) == 1

    def test_the_chip_shares_the_switch_fill(self, qt_app):
        """One textured chip beside one flat one read as an accident, so they use one gradient."""
        import theme

        qss = theme.build_qss()
        chip = qss.split("QFrame#StatusChip {")[1].split("}")[0]
        assert theme.sidebar_switch_gradient() in chip

    def test_both_components_actually_render_the_tile(self, qt_app, make_window, mocker):
        """A pixel check, because "it has a paintEvent" is not the same as "the tile is drawn".

        Measured as a **pixel difference** between the real tile and a fully transparent one. A
        standard deviation over the whole chip was tried first and is useless here: the label text
        and the status dot dominate it, and the grain -- which lifts dark pixels slightly -- actually
        *lowered* the figure. Counting pixels that change when the tile is removed measures the tile
        and nothing else.
        """
        from PyQt6.QtGui import QPixmap

        import theme
        from shell.widgets import GrainOverlay

        window = make_window()
        window.show()
        qt_app.processEvents()

        transparent = QPixmap(theme.GRAIN_TILE, theme.GRAIN_TILE)
        transparent.fill(theme.qcolor(theme.CHROME_FLAT, 0))

        for name, widget in (("chat switch", window.sidebar.chat_switch),
                             ("privacy chip", window.sidebar.privacy_status)):
            textured = widget.grab().toImage()
            patch = mocker.patch.object(
                GrainOverlay, "tile", classmethod(lambda cls: transparent))
            plain = widget.grab().toImage()
            mocker.stop(patch)

            changed = sum(
                1
                for y in range(min(textured.height(), plain.height()))
                for x in range(min(textured.width(), plain.width()))
                if textured.pixelColor(x, y) != plain.pixelColor(x, y)
            )
            total = textured.width() * textured.height()
            assert changed > total * 0.05, (
                f"{name}: only {changed} of {total} pixels change when the grain is removed, "
                "so the tile is not being drawn")
