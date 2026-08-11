"""Tests for the Privacy Guard (T2-1).

`should_skip_capture` is a pure function, so the whole policy is testable with no mocks and
no I/O. That matters more here than elsewhere: a silently broken blocklist is worse than no
blocklist, because the user believes they are protected.

The two behaviours most worth pinning down are the ones that are easy to get backwards:

* it must **fail OPEN** on detection failure, or a transient Win32 hiccup turns a privacy
  feature into random breakage, and
* a suppressed capture must **not abort the turn** -- the user asked a question and gets an
  answer, just a blind one.
"""

import pytest


class TestShouldSkipCapture:
    """T2-1: pure policy function. No I/O, no mocks needed."""

    @pytest.mark.parametrize("app", [
        # Verified installed on the development machine, not guessed.
        "kpm.exe", "KPM.exe", "kpm_viewer.exe",
        # Standard names for the rest of the field.
        "KeePass.exe", "keepass.exe", "Bitwarden.exe", "1Password.exe", "LastPass.exe",
        "keepassxc.exe", "dashlane.exe", "nordpass.exe",
    ])
    def test_blocklisted_apps_skip(self, app):
        from privacy import should_skip_capture
        skip, reason = should_skip_capture(app, "")
        assert skip is True
        assert reason

    def test_app_match_is_case_insensitive(self):
        from privacy import should_skip_capture
        assert should_skip_capture("KEEPASS.EXE", "")[0] is True
        assert should_skip_capture("keepass.exe", "")[0] is True

    @pytest.mark.parametrize("title", [
        "Sign in - Google", "Log in to your account", "Online Banking",
        "config.env - VS Code", "Enter your password", "2FA verification code",
        "Bank of Somewhere - Chrome", "Checkout - Shop", "Recovery code",
        "id_rsa - Notepad", "secrets.yaml - Kiro", "wallet - Exodus",
        "New InPrivate window", "Incognito - Chrome", "seed phrase backup",
    ])
    def test_blocklisted_titles_skip(self, title):
        from privacy import should_skip_capture
        skip, reason = should_skip_capture("chrome.exe", title)
        assert skip is True, f"{title!r} should have been blocked"
        assert reason

    @pytest.mark.parametrize("title", [
        "IMPROVEMENTS.md - Kiro",
        "Stack Overflow - How to center a div",
        "Untitled Spreadsheet - Excel",
        "Inbox (3) - Mail",
        # Near-misses that must NOT trip: the words appear but not as a secret.
        "Environment variables in Python - docs",
        "How password hashing works - blog",
    ])
    def test_ordinary_titles_do_not_skip(self, title):
        from privacy import should_skip_capture
        skip, _ = should_skip_capture("chrome.exe", title)
        assert skip is False, f"{title!r} was blocked but should not be"

    def test_ordinary_app_does_not_skip(self):
        from privacy import should_skip_capture
        assert should_skip_capture("Kiro.exe", "main.py")[0] is False

    def test_unknown_app_does_not_skip(self):
        """Fail OPEN on detection failure -- a hiccup must not break Nimbus.

        get_foreground_app() returns ('unknown', '') when the Win32 calls fail, which
        happens transiently during window transitions and against elevated processes.
        """
        from privacy import should_skip_capture
        assert should_skip_capture("unknown", "")[0] is False

    def test_empty_app_and_title_do_not_skip(self):
        from privacy import should_skip_capture
        assert should_skip_capture("", "")[0] is False

    def test_reason_string_is_user_presentable(self):
        """No regex source or exe paths in the toast text -- the toast is on screen and may
        itself end up in a screenshot or screen recording."""
        from privacy import should_skip_capture
        for app, title in [("keepass.exe", ""), ("chrome.exe", "Sign in - Google")]:
            _, reason = should_skip_capture(app, title)
            assert reason == reason.strip() and reason
            for forbidden in ("\\b", ".exe", "(?:", "re.", "C:\\", "|"):
                assert forbidden not in reason, f"{forbidden!r} leaked into {reason!r}"

    def test_disabled_guard_never_skips(self):
        from privacy import should_skip_capture
        assert should_skip_capture("keepass.exe", "Sign in", enabled=False)[0] is False

    def test_disabled_guard_returns_empty_reason(self):
        from privacy import should_skip_capture
        assert should_skip_capture("keepass.exe", "x", enabled=False) == (False, "")

    def test_user_added_app_is_honoured(self):
        from privacy import DEFAULT_BLOCKED_APPS, should_skip_capture
        skip, _ = should_skip_capture(
            "MySecretApp.exe", "",
            blocked_apps=DEFAULT_BLOCKED_APPS + ("mysecretapp.exe",),
        )
        assert skip is True

    def test_user_added_title_pattern_is_honoured(self):
        from privacy import DEFAULT_BLOCKED_TITLE_PATTERNS, should_skip_capture
        skip, _ = should_skip_capture(
            "chrome.exe", "Quarterly Salary Review",
            blocked_title_patterns=DEFAULT_BLOCKED_TITLE_PATTERNS + (r"salary",),
        )
        assert skip is True

    def test_malformed_user_regex_is_ignored_not_fatal(self):
        """A bad user pattern must not crash the pipeline on every interaction."""
        from privacy import should_skip_capture
        skip, _ = should_skip_capture(
            "chrome.exe", "Sign in - Google",
            blocked_title_patterns=("[unclosed", r"\bsign[ -]?in\b"),
        )
        assert skip is True, "valid patterns must still apply alongside a broken one"

    def test_only_malformed_patterns_degrades_gracefully(self):
        from privacy import should_skip_capture
        assert should_skip_capture(
            "chrome.exe", "Sign in", blocked_title_patterns=("[bad", "(also bad"),
        ) == (False, "")

    def test_app_block_wins_regardless_of_title(self):
        from privacy import should_skip_capture
        assert should_skip_capture("keepass.exe", "IMPROVEMENTS.md")[0] is True

    def test_function_is_pure(self):
        """Same inputs, same answer, repeatedly -- no hidden state or clock."""
        from privacy import should_skip_capture
        first = should_skip_capture("chrome.exe", "Sign in - Google")
        for _ in range(5):
            assert should_skip_capture("chrome.exe", "Sign in - Google") == first


class TestDefaultsAreSane:
    def test_guard_is_on_by_default(self, first_run_config):
        """The ONE deliberate exception to "new settings default to current behaviour",
        because the current behaviour is the defect rather than a preference."""
        assert first_run_config.PRIVACY_GUARD == "on"

    def test_user_extension_lists_default_empty(self, first_run_config):
        assert first_run_config.PRIVACY_GUARD_APPS == ""
        assert first_run_config.PRIVACY_GUARD_TITLES == ""

    def test_blocked_app_names_are_lowercase(self):
        """Matching lowercases the foreground name, so an uppercase entry is dead."""
        from privacy import DEFAULT_BLOCKED_APPS
        for app in DEFAULT_BLOCKED_APPS:
            assert app == app.lower(), f"{app!r} could never match"

    def test_every_default_title_pattern_compiles(self):
        import re
        from privacy import DEFAULT_BLOCKED_TITLE_PATTERNS
        for pattern in DEFAULT_BLOCKED_TITLE_PATTERNS:
            re.compile(pattern)  # must not raise

    def test_password_managers_are_covered(self):
        from privacy import DEFAULT_BLOCKED_APPS
        assert any("keepass" in a for a in DEFAULT_BLOCKED_APPS)
        assert any("bitwarden" in a for a in DEFAULT_BLOCKED_APPS)
        assert "kpm.exe" in DEFAULT_BLOCKED_APPS, "verified-installed manager missing"


class TestPipelineIntegration:
    """The guard must suppress the screenshot WITHOUT aborting the interaction."""

    def _app(self, current_app="Kiro.exe", current_title="main.py"):
        from app import NimbusApp

        class _Sig:
            def __init__(self):
                self.calls = []
            def emit(self, *a):
                self.calls.append(a)

        app = NimbusApp.__new__(NimbusApp)
        app._current_app = current_app
        app._current_title = current_title
        app.__dict__["sig_hide_overlay"] = _Sig()
        app.__dict__["sig_show_overlay"] = _Sig()
        app.__dict__["sig_show_toast"] = _Sig()
        # SHELL_AND_CHAT.md §3 `S-2`: suppressions are counted at this choke point so Home can
        # report how many times Nimbus chose not to look at the screen.
        app._privacy_skips = []
        return app

    def test_a_suppression_is_counted_for_home(self, mocker):
        """The count is an observation, which is what makes it worth showing."""
        mocker.patch("app.capture_all_screens")
        app = self._app(current_app="keepass.exe", current_title="")

        app._capture_screens_guarded()
        app._capture_screens_guarded()

        assert app.screenshots_skipped_this_week() == 2

    def test_an_allowed_capture_is_not_counted(self, mocker):
        mocker.patch("app.capture_all_screens", return_value=["capture"])
        app = self._app(current_app="notepad.exe", current_title="notes.txt")

        app._capture_screens_guarded()

        assert app.screenshots_skipped_this_week() == 0

    def test_capture_skipped_when_privacy_guard_trips(self, mocker):
        grab = mocker.patch("app.capture_all_screens")
        app = self._app(current_app="keepass.exe", current_title="")
        assert app._capture_screens_guarded() == []
        grab.assert_not_called()

    def test_overlay_is_not_touched_when_capture_is_skipped(self, mocker):
        """No hide/show cycle means no flicker for a turn that captures nothing."""
        mocker.patch("app.capture_all_screens")
        app = self._app(current_app="keepass.exe", current_title="")
        app._capture_screens_guarded()
        assert app.__dict__["sig_hide_overlay"].calls == []
        assert app.__dict__["sig_show_overlay"].calls == []

    def test_toast_shown_when_capture_skipped(self, mocker):
        mocker.patch("app.capture_all_screens")
        app = self._app(current_app="keepass.exe", current_title="")
        app._capture_screens_guarded()
        toasts = app.__dict__["sig_show_toast"].calls
        assert len(toasts) == 1
        assert "skipped" in toasts[0][0].lower()

    def test_normal_app_captures_and_cycles_the_overlay(self, mocker):
        """Invariant #3: overlay hidden BEFORE grab, restored after."""
        grab = mocker.patch("app.capture_all_screens", return_value=["cap"])
        app = self._app()
        assert app._capture_screens_guarded() == ["cap"]
        grab.assert_called_once()
        assert len(app.__dict__["sig_hide_overlay"].calls) == 1
        assert len(app.__dict__["sig_show_overlay"].calls) == 1

    def test_overlay_restored_even_if_grab_raises(self, mocker):
        """Otherwise the pointer stays invisible for the rest of the session."""
        mocker.patch("app.capture_all_screens", side_effect=RuntimeError("mss died"))
        app = self._app()
        with pytest.raises(RuntimeError):
            app._capture_screens_guarded()
        assert len(app.__dict__["sig_show_overlay"].calls) == 1

    def test_the_capture_waits_for_the_compositor_between_hide_and_grab(self, mocker):
        """Invariant 3 depends on the *order*, not just the calls.

        Hiding the overlay is asynchronous -- the window is gone only once the compositor has
        presented the frame without it. Grabbing before that wait feeds Nimbus its own pointer, which
        is the feedback loop Invariant 3 exists to prevent. So the sequence is pinned, not just the
        fact that a wait happens somewhere."""
        order = []
        mocker.patch("app.capture_all_screens", side_effect=lambda *a, **k: order.append("grab") or ["cap"])
        mocker.patch("app._wait_for_compositor", side_effect=lambda: order.append("wait"))

        app = self._app()
        app.__dict__["sig_hide_overlay"].emit = lambda *a: order.append("hide")
        app.__dict__["sig_show_overlay"].emit = lambda *a: order.append("show")

        app._capture_screens_guarded()
        assert order == ["hide", "wait", "grab", "show"]

    def test_the_compositor_wait_falls_back_to_a_sleep_when_dwmflush_is_unavailable(self, mocker):
        """A missing compositor call must cost latency, never Invariant 3.

        ``DwmFlush`` is the measured 55ms saving over the old fixed sleep, but if it is unavailable or
        fails, skipping the wait entirely would let the overlay into its own screenshot. So the
        fallback is the thing worth testing."""
        import app as app_module

        waited = []
        mocker.patch.object(app_module.ctypes, "windll", create=True)
        mocker.patch.object(app_module.ctypes.windll.dwmapi, "DwmFlush",
                            side_effect=OSError("no dwmapi"))
        mocker.patch.object(app_module.threading, "Event",
                            return_value=type("E", (), {"wait": lambda self, t: waited.append(t)})())

        app_module._wait_for_compositor()
        assert waited == [app_module._CAPTURE_SETTLE_FALLBACK]

    def test_verdict_uses_press_time_app_not_a_fresh_lookup(self, mocker):
        """The decision must be about the window the user was looking at when they asked,
        not whatever is focused by the time the capture thread runs."""
        lookup = mocker.patch("app.get_foreground_app")
        app = self._app(current_app="keepass.exe", current_title="")
        assert app._privacy_verdict()[0] is True
        lookup.assert_not_called()

    def test_user_extension_reaches_the_verdict(self, mocker):
        mocker.patch("config.PRIVACY_GUARD_APPS", "mysecret.exe")
        mocker.patch("app.capture_all_screens")
        app = self._app(current_app="MySecret.exe", current_title="")
        assert app._privacy_verdict()[0] is True

    def test_guard_off_restores_unconditional_capture(self, mocker):
        grab = mocker.patch("app.capture_all_screens", return_value=["cap"])
        mocker.patch("config.PRIVACY_GUARD", "off")
        app = self._app(current_app="keepass.exe", current_title="")
        assert app._capture_screens_guarded() == ["cap"]
        grab.assert_called_once()
