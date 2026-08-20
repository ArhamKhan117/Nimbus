"""Fixture labeller for the grounding-accuracy harness (T1-8).

Turns hours of hand-editing JSON into minutes of clicking. Capture a screen, drag a box
around a UI target, type what you'd ask Nimbus to find it, save.

Why this exists before the harness itself: T1-8 needs ground-truth boxes, and the doc's
own estimate flagged manual labelling as the real cost. Without a tool, labelling one
screen means reading pixel coordinates out of an image editor by hand.

Usage
-----
    # Capture the current screen and start labelling it
    py -3.13 tools/label_fixtures.py --capture

    # Or label an existing image
    py -3.13 tools/label_fixtures.py --image path/to/shot.png

Controls
--------
    drag                draw a box around a target
    type + Enter        set the query for the box just drawn
    Ctrl+Z              undo the last box
    Ctrl+S              save the sidecar JSON
    Esc                 quit (prompts if unsaved)

Output
------
``tools/grounding_fixtures/<name>.png`` plus ``<name>.json``:

    {"image": "vscode_4k_200dpi.png",
     "monitor": {"width": 3840, "height": 2160},
     "capture": {"width": 1920, "height": 1080},
     "targets": [{"query": "the save icon", "box": [1204, 88, 1232, 116]}]}

``box`` is ``[x0, y0, x1, y1]`` in **capture-image pixels**, i.e. Space C — the same
space the model's normalised coordinates convert into, so the harness compares like with
like and needs no extra transform.

Script-only: never imported by a runtime module, and ``nimbus.spec`` excludes this tree.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "grounding_fixtures"


def _slugify(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in text.strip()]
    return "".join(keep).strip("_") or "fixture"


def build_sidecar(
    image_name: str,
    capture_size: tuple[int, int],
    monitor: dict | None,
    targets: list[dict],
) -> dict:
    """Assemble the sidecar dict. Pure, so the schema is unit-testable."""
    return {
        "image": image_name,
        "monitor": dict(monitor) if monitor else None,
        "capture": {"width": capture_size[0], "height": capture_size[1]},
        "targets": list(targets),
    }


def validate_sidecar(data: dict) -> list[str]:
    """Return a list of problems, empty when valid.

    The harness refuses malformed fixtures rather than silently scoring against a bad
    box, so validation lives here where it can be fixed at labelling time.
    """
    problems: list[str] = []
    if not data.get("image"):
        problems.append("missing image name")
    cap = data.get("capture") or {}
    cw, ch = cap.get("width"), cap.get("height")
    if not (isinstance(cw, int) and isinstance(ch, int) and cw > 0 and ch > 0):
        problems.append("capture size must be positive integers")
        cw = ch = None
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        problems.append("at least one target is required")
        return problems
    for i, target in enumerate(targets):
        if not str(target.get("query", "")).strip():
            problems.append(f"target {i}: empty query")
        box = target.get("box")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            problems.append(f"target {i}: box must be [x0, y0, x1, y1]")
            continue
        try:
            x0, y0, x1, y1 = (int(v) for v in box)
        except (TypeError, ValueError):
            problems.append(f"target {i}: box values must be integers")
            continue
        if x1 <= x0 or y1 <= y0:
            problems.append(f"target {i}: box is empty or inverted")
        if cw and ch and not (0 <= x0 and 0 <= y0 and x1 <= cw and y1 <= ch):
            problems.append(f"target {i}: box outside the capture bounds")
    return problems


def _load_image(args):
    """Return ``(PIL image, monitor dict | None, suggested name)``."""
    if args.capture:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import capture as nimbus_capture

        cap = nimbus_capture.capture_all_screens()[0]
        name = args.name or _slugify(
            f"{cap.target_width}x{cap.target_height}_scale{cap.scale_x:.0f}"
        )
        return cap.image, dict(cap.monitor), name

    from PIL import Image

    path = Path(args.image)
    if not path.is_file():
        raise SystemExit(f"no such image: {path}")
    return Image.open(path).convert("RGB"), None, (args.name or path.stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", action="store_true",
                        help="capture the screen under the cursor")
    source.add_argument("--image", help="label an existing image file")
    parser.add_argument("--name", help="fixture name (default: derived)")
    args = parser.parse_args()

    image, monitor, name = _load_image(args)

    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import (
        QApplication, QInputDialog, QLabel, QMainWindow, QMessageBox,
    )

    app = QApplication(sys.argv)

    qimage = QImage(
        image.tobytes("raw", "RGB"), image.width, image.height,
        image.width * 3, QImage.Format.Format_RGB888,
    )
    pixmap = QPixmap.fromImage(qimage)

    class Canvas(QLabel):
        """Scaled preview that maps clicks back to full-resolution pixels."""

        def __init__(self):
            super().__init__()
            self.targets: list[dict] = []
            self.dirty = False
            self._origin = None
            self._current: QRect | None = None
            screen = app.primaryScreen().availableGeometry()
            self._scale = min(
                1.0,
                (screen.width() - 80) / image.width,
                (screen.height() - 140) / image.height,
            )
            self.setPixmap(pixmap.scaled(
                int(image.width * self._scale), int(image.height * self._scale),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self.setMouseTracking(True)

        def _to_image(self, point) -> tuple[int, int]:
            return (
                max(0, min(image.width - 1, int(point.x() / self._scale))),
                max(0, min(image.height - 1, int(point.y() / self._scale))),
            )

        def mousePressEvent(self, event):
            self._origin = event.position().toPoint()
            self._current = QRect(self._origin, self._origin)
            self.update()

        def mouseMoveEvent(self, event):
            if self._origin is not None:
                self._current = QRect(
                    self._origin, event.position().toPoint()).normalized()
                self.update()

        def mouseReleaseEvent(self, event):
            if self._origin is None:
                return
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self._origin, self._current = None, None
            if rect.width() < 4 or rect.height() < 4:
                self.update()
                return
            x0, y0 = self._to_image(rect.topLeft())
            x1, y1 = self._to_image(rect.bottomRight())
            query, ok = QInputDialog.getText(
                self, "Target query",
                "What would you ask Nimbus to find this?\n"
                "(phrase it naturally, e.g. 'the save icon')",
            )
            if ok and query.strip():
                self.targets.append(
                    {"query": query.strip(), "box": [x0, y0, x1, y1]})
                self.dirty = True
                self._refresh_title()
            self.update()

        def _refresh_title(self):
            window.setWindowTitle(
                f"{name} - {len(self.targets)} target(s)"
                f"{' *' if self.dirty else ''}  |  Ctrl+S save, Ctrl+Z undo"
            )

        def undo(self):
            if self.targets:
                self.targets.pop()
                self.dirty = True
                self._refresh_title()
                self.update()

        def paintEvent(self, event):
            super().paintEvent(event)
            painter = QPainter(self)
            font = QFont("Segoe UI", 9, QFont.Weight.Bold)
            painter.setFont(font)
            for i, target in enumerate(self.targets, start=1):
                x0, y0, x1, y1 = target["box"]
                rect = QRect(
                    int(x0 * self._scale), int(y0 * self._scale),
                    int((x1 - x0) * self._scale), int((y1 - y0) * self._scale),
                )
                painter.setPen(QPen(QColor(34, 197, 94), 2))
                painter.drawRect(rect)
                label = f"{i}. {target['query']}"
                painter.setPen(QColor(0, 0, 0, 190))
                painter.drawText(rect.left() + 5, rect.top() + 15, label)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(rect.left() + 4, rect.top() + 14, label)
            if self._current is not None:
                painter.setPen(QPen(QColor(59, 130, 246), 2, Qt.PenStyle.DashLine))
                painter.drawRect(self._current)

    class Window(QMainWindow):
        def keyPressEvent(self, event):
            ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
            if ctrl and event.key() == Qt.Key.Key_S:
                self.save()
            elif ctrl and event.key() == Qt.Key.Key_Z:
                canvas.undo()
            elif event.key() == Qt.Key.Key_Escape:
                self.close()

        def save(self):
            if not canvas.targets:
                QMessageBox.warning(self, "Nothing to save",
                                    "Draw at least one box first.")
                return
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            image_name = f"{name}.png"
            image.save(FIXTURE_DIR / image_name)
            data = build_sidecar(
                image_name, (image.width, image.height), monitor, canvas.targets,
            )
            problems = validate_sidecar(data)
            if problems:
                QMessageBox.critical(self, "Invalid fixture", "\n".join(problems))
                return
            (FIXTURE_DIR / f"{name}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8")
            canvas.dirty = False
            canvas._refresh_title()
            QMessageBox.information(
                self, "Saved",
                f"{image_name}\n{name}.json\n\n"
                f"{len(canvas.targets)} target(s) in {FIXTURE_DIR}",
            )

        def closeEvent(self, event):
            if canvas.dirty:
                reply = QMessageBox.question(
                    self, "Unsaved labels",
                    "You have unsaved labels. Save before closing?",
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel,
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
                if reply == QMessageBox.StandardButton.Save:
                    self.save()
            event.accept()

    window = Window()
    canvas = Canvas()
    window.setCentralWidget(canvas)
    canvas._refresh_title()
    window.show()

    print(f"Labelling {name} ({image.width}x{image.height})")
    print("  drag to box a target, type the query, Ctrl+S to save, Esc to quit")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
