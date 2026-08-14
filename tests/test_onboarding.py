"""Tests for the one-time first-run welcome dialog's display content."""

import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_welcome_dialog_explains_mic_and_configured_hotkey(qapp):
    from onboarding import WelcomeDialog
    dialog = WelcomeDialog("ctrl+shift+f2")
    text = " ".join(label.text() for label in dialog.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "Microphone" in text
    assert "Ctrl+Shift+F2" in text
