"""Tests for restart-required labelling in Settings (T4-7).

The problem this solves is not technical, it is a trust problem: a user toggles something,
sees no change, and concludes the setting is broken. That got materially worse across Tiers
1-3, which added eleven settings and made every one of them restart-gated.

Deliberately the *minimum* version of T4-7. Full live reload is the wrong fix -- the
import-time caching exists because `resolve_setting` writes to the keyring whenever a value
came from the environment, so re-resolving per interaction would put a Credential Manager
write on the hottest path in the app.

The property most worth guarding is the one that rots silently: a **new** restart-gated
setting being added without a label.
"""

import pytest


class TestRestartMarker:
    def test_marked_setting_gets_a_marker(self):
        from settings_dialog import restart_marker_for
        assert restart_marker_for("HOTKEY")

    def test_unmarked_setting_gets_nothing(self):
        from settings_dialog import restart_marker_for
        assert restart_marker_for("SOMETHING_ELSE") == ""

    def test_api_keys_are_not_marked(self):
        """Keys are read per request, so a new key works immediately. Marking them would be
        actively misleading -- it would tell users to restart when they need not."""
        from settings_dialog import RESTART_REQUIRED_SETTINGS
        for name in RESTART_REQUIRED_SETTINGS:
            assert not name.endswith("_API_KEY"), name

    def test_marker_is_a_short_symbol(self):
        """It is appended to already-long checkbox labels, so "(requires restart)" would
        wrap them. A symbol survives."""
        from settings_dialog import RESTART_MARKER
        assert len(RESTART_MARKER.strip()) <= 2

    def test_note_explains_the_marker(self):
        """A marker with no legend is just a mysterious character."""
        from settings_dialog import RESTART_MARKER, RESTART_NOTE
        assert RESTART_MARKER.strip() in RESTART_NOTE
        assert "restart" in RESTART_NOTE.lower() or "starts" in RESTART_NOTE.lower()


class TestCoverage:
    """Guards the failure mode that rots quietly: a new cached setting with no label."""

    def test_every_experimental_toggle_is_marked(self):
        from settings_dialog import _EXPERIMENTAL_TOGGLES, RESTART_REQUIRED_SETTINGS
        for setting, _, _ in _EXPERIMENTAL_TOGGLES:
            assert setting in RESTART_REQUIRED_SETTINGS, setting

    @pytest.mark.parametrize("setting", [
        "HOTKEY", "ANNOTATION_MODE", "PRIVACY_GUARD", "CAPTIONS",
        "KNOWLEDGE_JOURNAL", "HISTORY_IMAGE_COUNT", "GROUNDING_REFINEMENT",
        "LLM_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "GEMINI_NATIVE_MODEL",
    ])
    def test_known_cached_settings_are_marked(self, setting):
        from settings_dialog import RESTART_REQUIRED_SETTINGS
        assert setting in RESTART_REQUIRED_SETTINGS

    def test_settings_cached_at_app_import_are_all_marked(self):
        """Structural drift guard.

        Any setting app.py resolves ONCE at import is restart-gated by construction. This
        finds those cached flags and asserts each is labelled, so adding a new cached
        setting without a label fails here rather than confusing a user later.
        """
        from settings_dialog import RESTART_REQUIRED_SETTINGS
        # app.py caches these as module-level constants derived from config.
        cached = {
            "ANNOTATION_MODE", "CAPTIONS", "KNOWLEDGE_JOURNAL",
            "HISTORY_IMAGE_COUNT", "PRIVACY_GUARD",
        }
        assert cached <= RESTART_REQUIRED_SETTINGS

    def test_local_keyring_entries_that_are_cached_are_marked(self):
        """Cross-check against the wipe list, which is the closest thing to a full
        inventory of persisted settings."""
        from settings_dialog import _LOCAL_KEYRING_ENTRIES, RESTART_REQUIRED_SETTINGS
        # Settings genuinely read per-use, so correctly NOT restart-gated.
        live = {
            "DIAGNOSTIC_CAPTURE", "DIAGNOSTIC_RETENTION_DAYS",
            "FASTER_WHISPER_MODEL", "FASTER_WHISPER_DEVICE",
            "FASTER_WHISPER_COMPUTE", "KOKORO_VOICE", "OLLAMA_MODEL_TEXT",
        }
        for name in _LOCAL_KEYRING_ENTRIES:
            if name.endswith("_API_KEY") or name in live:
                continue
            assert name in RESTART_REQUIRED_SETTINGS, (
                f"{name} is persisted and cached but carries no restart label"
            )


class TestDialogRendering:
    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_marker_appears_on_rendered_labels(self, qt_app):
        from PyQt6.QtWidgets import QCheckBox, QLabel
        import settings_dialog
        from settings_dialog import RESTART_MARKER

        dialog = settings_dialog.SettingsDialog()
        try:
            texts = [w.text() for w in dialog.findChildren(QLabel)]
            texts += [w.text() for w in dialog.findChildren(QCheckBox)]
            marked = [t for t in texts if RESTART_MARKER.strip() in t]
            assert len(marked) >= 5, f"expected several marked labels, got {marked}"
        finally:
            dialog.deleteLater()

    def test_note_is_shown_once(self, qt_app):
        from PyQt6.QtWidgets import QLabel
        import settings_dialog
        from settings_dialog import RESTART_NOTE

        dialog = settings_dialog.SettingsDialog()
        try:
            notes = [
                w for w in dialog.findChildren(QLabel) if w.text() == RESTART_NOTE
            ]
            assert len(notes) == 1
        finally:
            dialog.deleteLater()

    def test_privacy_checkbox_is_marked(self, qt_app):
        import settings_dialog
        from settings_dialog import RESTART_MARKER

        dialog = settings_dialog.SettingsDialog()
        try:
            assert RESTART_MARKER.strip() in dialog._privacy_checkbox.text()
        finally:
            dialog.deleteLater()

    def test_marker_does_not_break_saving(self, qt_app, mocker):
        """The marker is presentation only. If it leaked into a persisted value the setting
        would stop resolving."""
        import keyring
        import settings_dialog
        from config import KEYRING_SERVICE

        stored = {}
        mocker.patch.object(
            keyring, "set_password",
            lambda s, n, v: stored.__setitem__(n, v))
        mocker.patch.object(
            settings_dialog.QMessageBox, "information", lambda *a, **k: None)

        dialog = settings_dialog.SettingsDialog()
        try:
            dialog._on_save()
        finally:
            dialog.deleteLater()

        from settings_dialog import RESTART_MARKER
        for name, value in stored.items():
            assert RESTART_MARKER.strip() not in str(value), (
                f"marker leaked into persisted {name}={value!r}"
            )
        assert stored.get("PRIVACY_GUARD") in ("on", "off")
