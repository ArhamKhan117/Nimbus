"""Small, one-time welcome dialog shown after Nimbus has been configured."""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout


class WelcomeDialog(QDialog):
    """Explain the two permissions/controls users need before first use."""

    def __init__(self, hotkey: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Nimbus")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        title = QLabel("<h2>Welcome to Nimbus</h2>")
        layout.addWidget(title)
        layout.addWidget(QLabel(
            "Nimbus is ready to help with what is on your screen. "
            "Two quick things to know:"
        ))
        layout.addWidget(QLabel(
            "<b>Microphone</b><br>When you first hold push-to-talk, Windows may ask "
            "for microphone access. Choose Allow so Nimbus can hear you."
        ))
        pretty = "+".join(part.capitalize() for part in hotkey.split("+"))
        layout.addWidget(QLabel(
            f"<b>Push to talk</b><br>Hold <b>{pretty}</b> while speaking, then release "
            "to send. You can pause it or change it from the tray icon."
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
