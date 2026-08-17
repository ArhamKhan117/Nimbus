"""Knowledge: the per-app documentation browser (SHELL_AND_CHAT.md §3 `S-2`).

The knowledge base is, by `T3-2`'s own account, the most powerful feature nobody discovers --
it lives in a folder with a naming convention (``orionflow.exe.md``) that cannot be guessed.
The Settings dialog's "Open folder" button was the cheapest fix; this page is the real one: it
shows what Nimbus can currently read, per app, with counts and sizes, and accepts files dropped
straight onto it.

## It reads ``kb.py``, it does not reimplement it

Entry discovery uses ``kb.iter_kb_files`` and ``kb.SUPPORTED_SUFFIXES`` so this page can never
disagree with what the pipeline will actually read -- including the 40-file cap and the
recursive folder walk. ``kb.GUIDE_FILENAME`` is excluded from the app list because it is
Nimbus's own guide, not the user's knowledge.

Both layouts `kb.py` supports are shown as they are, rather than normalised into one shape:

* a **flat file** ``<app>.exe.md`` -- the original design and still the simplest thing that
  works;
* a **folder** ``<app>.exe/`` of ``.md``/``.txt``/``.pdf``/``.docx``.

## Drag and drop, and what it deliberately does not do

Dropped files are **copied**, never moved: a user dragging their only copy of a document out of
their own folder structure and having it disappear would be indefensible. Unsupported files are
reported rather than silently ignored, because a dropped ``.xlsx`` that vanishes looks like a
bug in Nimbus. Nothing is ever overwritten without the user having asked -- a name clash gets a
numbered suffix.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import theme
from shell.widgets import Card, label, style_table, table_item

KIND_LABELS = {
    "file": "Single file",
    "folder": "Folder",
    "file + folder": "File and folder",
    "folder + file": "File and folder",
}
"""Human labels for ``KbEntry.kind``.

The raw values are internal ("file", "folder + file") and were being shown as-is, which read as
a debug field. ``folder + file`` and ``file + folder`` are the same thing in different iteration
orders, so both map to one label -- otherwise the column would appear to hold two states that
mean the same."""


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.replace(" + ", " and ").capitalize())


def guide_html(kb_dir: Path | str | None = None) -> str:
    """The "how to add knowledge" instructions, as rendered rich text.

    Pure, so the copy is testable without a widget -- and the copy is the point of this panel.

    Written as three numbered steps because that is what the task is. The previous version
    showed the first 40 lines of the seeded ``README.md`` raw, Markdown syntax included, which
    asked the reader to parse ``#`` headings and code fences before they could find the one fact
    they needed: what to name the file. The filename example is the visual anchor here, because
    getting the name wrong is the only way this feature silently fails.
    """
    folder = str(kb_dir) if kb_dir else "the knowledge folder"
    return f"""
<style>
  body {{ color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_BODY}pt; }}
  p {{ margin: 0 0 8px 0; line-height: 145%; }}
  ol {{ margin: 0 0 4px 18px; padding: 0; }}
  li {{ margin-bottom: 7px; line-height: 145%; }}
  code {{ font-family: {theme.FONT_MONO}; color: {theme.ACCENT}; }}
  .step {{ color: {theme.TEXT_PRIMARY}; font-weight: {theme.WEIGHT_SEMIBOLD}; }}
  .note {{ color: {theme.TEXT_MUTED}; font-size: {theme.FONT_SMALL}pt; }}
</style>
<p>Nimbus already knows mainstream software. This is for the programs it cannot
know &mdash; an in-house tool, a company workflow, a plugin with no public documentation.</p>
<ol>
  <li><span class="step">Find the program's executable name.</span>
      Task Manager &rarr; Details shows it, for example <code>orionflow.exe</code>.</li>
  <li><span class="step">Name your notes after it.</span>
      Either one file called <code>orionflow.exe.md</code>, or a folder called
      <code>orionflow.exe</code> holding as many <code>.md</code>, <code>.txt</code>,
      <code>.pdf</code> and <code>.docx</code> files as you like.</li>
  <li><span class="step">Put it in {folder}.</span>
      Use the button below to open it.</li>
</ol>
<p class="note">Picked up on your next question &mdash; no restart. Nimbus reads up to 40 files
per application. Anything not named after an executable is ignored, so a stray file cannot
break anything.</p>
""".strip()


@dataclass(frozen=True)
class KbEntry:
    """One application's knowledge, as the page displays it."""

    app_name: str
    kind: str          # "file" | "folder" | "file + folder"
    file_count: int
    total_bytes: int
    path: Path


def human_size(total_bytes: int) -> str:
    """Bytes as a short human string. Pure, so it is testable without widgets."""
    size = float(max(0, total_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def scan_kb(kb_dir: Path) -> list[KbEntry]:
    """Every app with knowledge in ``kb_dir``, newest-largest-first by name.

    Uses ``kb.iter_kb_files`` for folders so the count shown is the count the pipeline will
    read -- same recursion, same suffix filter, same 40-file cap. A page that reported 60 files
    while ``recall`` read 40 would be worse than reporting nothing.
    """
    import kb

    kb_dir = Path(kb_dir)
    if not kb_dir.is_dir():
        return []

    found: dict[str, dict] = {}
    for child in sorted(kb_dir.iterdir(), key=lambda p: p.name.lower()):
        if child.is_file():
            if child.name == kb.GUIDE_FILENAME or child.suffix.lower() != ".md":
                continue
            record = found.setdefault(child.stem, {"kinds": [], "files": 0, "bytes": 0,
                                                   "path": child})
            record["kinds"].append("file")
            record["files"] += 1
            record["bytes"] += _safe_size(child)
        elif child.is_dir():
            files = kb.iter_kb_files(child)
            if not files:
                continue
            record = found.setdefault(child.name, {"kinds": [], "files": 0, "bytes": 0,
                                                   "path": child})
            record["kinds"].append("folder")
            record["files"] += len(files)
            record["bytes"] += sum(_safe_size(path) for path in files)
            record["path"] = child

    return [
        KbEntry(
            app_name=name,
            kind=" + ".join(record["kinds"]),
            file_count=record["files"],
            total_bytes=record["bytes"],
            path=record["path"],
        )
        for name, record in sorted(found.items())
    ]


def _safe_size(path: Path) -> int:
    """A file's size, or 0. A file locked by another program must not break the listing."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


class KnowledgePage(QWidget):
    """The knowledge browser. ``kb_dir`` and the folder-opener are injectable for tests."""

    sig_files_added = pyqtSignal(int)

    def __init__(
        self,
        *,
        kb_dir: Path | str | None = None,
        open_folder: Callable[[Path], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kb_dir_override = Path(kb_dir) if kb_dir is not None else None
        self._open_folder = open_folder or _open_in_explorer
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE[3])
        outer.addWidget(label("Knowledge", "PageTitle"))

        self._outer = outer
        self._list_card = self._build_list_card()
        outer.addWidget(self._list_card, stretch=1)
        outer.addWidget(self._build_guide_card())

        # Where the spare height goes when there is nothing to list.
        #
        # The first fix put a filler *inside* the card, which stopped the heading ballooning but
        # parked the empty space between the intro text and the folder path -- a tall gap in the
        # middle of a card with four lines of content in it. The space belongs below the cards, not
        # inside one, so it goes here and the card is told not to stretch while the list is empty.
        self._tail = QWidget()
        outer.addWidget(self._tail, stretch=1)

        self.refresh()

    # -- public ---------------------------------------------------------------

    @property
    def kb_dir(self) -> Path:
        """The folder in use. Resolved live from ``config`` unless overridden.

        Read on each access rather than captured, mirroring why
        ``SettingsDialog._on_open_kb_folder`` derives its folder from ``kb.ensure_guide``'s
        return value: two independent reads of ``KB_DIR`` can disagree, and then Nimbus seeds
        one folder and shows another.
        """
        if self._kb_dir_override is not None:
            return self._kb_dir_override
        from config import KB_DIR
        return Path(KB_DIR)

    def entries(self) -> list[KbEntry]:
        """What the page is currently showing, as data. Useful to callers and to tests."""
        return list(self._entries)

    def refresh(self) -> None:
        try:
            self._entries = scan_kb(self.kb_dir)
        except Exception:
            self._entries = []
        self.table.setRowCount(len(self._entries))
        for index, entry in enumerate(self._entries):
            cells = (
                table_item(entry.app_name, mono=True),
                table_item(_kind_label(entry.kind), muted=True),
                table_item(
                    f"{entry.file_count} file" + ("s" if entry.file_count != 1 else ""),
                    muted=True),
                table_item(human_size(entry.total_bytes), muted=True),
            )
            for column, cell in enumerate(cells):
                self.table.setItem(index, column, cell)
        self.table.setVisible(bool(self._entries))
        self.empty.setVisible(not self._entries)
        # The card only claims the spare height when it has a table to put it in. Empty, it hugs
        # its four lines of content and the slack goes to the tail below the cards.
        self._outer.setStretchFactor(self._list_card, 1 if self._entries else 0)
        self._tail.setVisible(not self._entries)
        self.path_label.setText(str(self.kb_dir))
        self._load_guide_preview()

    # -- construction ---------------------------------------------------------

    def _build_list_card(self) -> Card:
        """The list, and the two things that used to be wrong with it.

        **One explanation, not three.** This card carried a "drop files here, named after the
        .exe" paragraph *and* an empty-state paragraph saying much the same thing, directly
        above a whole card that explains the naming in three numbered steps. Three statements of
        one idea read as clutter, and the reader has to check all three for differences. The
        drop-target hint is now one short line that is always true, and the empty state says
        only what is true when the list is empty.

        **The spare height goes to the table.** The page gives this card ``stretch=1``, so it is
        taller than its content on any normal screen. Naming the table as the child that absorbs
        the surplus is what stops ``QVBoxLayout`` spreading it across the heading and the labels
        -- see ``shell.widgets.Card``.
        """
        card = Card("Per application")

        self.hint = label(
            "Drop files here, or use Open folder. Picked up on your next question.", "Muted")
        card.add(self.hint)

        self.empty = label(
            "Nothing yet. Nimbus already knows Excel and Blender; this is for the software it "
            "cannot know -- an in-house tool, a company workflow, a plugin with no public "
            "documentation.", "Secondary")
        self.empty.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        card.add(self.empty)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Application", "Layout", "Files", "Size"])
        style_table(self.table, stretch_column=0)
        card.add(self.table, stretch=1)

        # Air before the folder path. It is a different kind of thing from the text above it -- that
        # explains what this card is for, this states where the files go -- and at the card body's
        # default spacing the two ran together as one block.
        card.body.addSpacing(theme.SPACE[1])
        self.path_label = label("", "Mono")
        card.add(self.path_label)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[1])
        self.open_button = QPushButton("Open folder\u2026")
        self.open_button.setToolTip(
            "Teach Nimbus about software it does not know. Drop a Markdown file named after\n"
            "the program (for example orionflow.exe.md), or a folder of the same name holding\n"
            ".md, .txt, .pdf and .docx files.\n\n"
            "The folder contains a README explaining the naming and formats.\n"
            "Picked up on your next question -- no restart needed."
        )
        self.open_button.clicked.connect(self.open_kb_folder)
        row.addWidget(self.open_button)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        row.addWidget(refresh_button)
        row.addStretch(1)
        self.status = label("", "Muted")
        row.addWidget(self.status)
        card.add(buttons)
        return card

    def _build_guide_card(self) -> Card:
        """How to add knowledge, written out rather than dumped.

        This card used to show the first 40 lines of ``README.md`` verbatim, in a monospaced
        box, with the Markdown syntax still in it -- ``# Heading``, ``` ``` ``` fences and all.
        That is a file, not an explanation: the reader had to mentally strip the markup before
        they could follow it, and the one thing they needed (what to *name* the file) was
        somewhere in the middle of it.

        Now it is three numbered steps in rendered rich text, with the filename example as the
        visual anchor, and the README stays on disk for anyone who opens the folder.
        """
        card = Card("How to teach Nimbus about an application")
        self.guide = QTextBrowser()
        self.guide.setReadOnly(True)
        self.guide.setOpenExternalLinks(False)
        self.guide.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.guide.setFixedHeight(theme.SPACE[7] * 4)
        self.guide.setStyleSheet(
            f"QTextBrowser {{ background: {theme.rgba(theme.BG_SUNKEN, 0.55)};"
            f" border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_CONTROL}px;"
            f" padding: {theme.SPACE[2]}px; }}"
        )
        self.guide.setHtml(guide_html())
        card.add(self.guide)
        return card

    # -- actions --------------------------------------------------------------

    def open_kb_folder(self) -> None:
        """Seed the guide, then open the folder Nimbus actually wrote it into.

        ``ensure_guide`` runs here as well as at startup so the guide is present even if the
        user deleted it or their folder predates this version. It only writes when the file is
        absent, so their own edits are safe.
        """
        folder = self.kb_dir
        try:
            import kb
            guide = kb.ensure_guide()
            if guide is not None:
                folder = guide.parent
        except Exception:
            pass
        if not self._open_folder(folder):
            self.status.setText(f"Could not open {folder}")
            return
        self.status.setText("")
        self.refresh()

    def _load_guide_preview(self) -> None:
        """Re-render the instructions with the live folder path.

        The path is the one thing here that is not static, and it is worth restating in the
        instructions rather than only in the small label above: the reader is being told to put
        a file somewhere, and the sentence telling them should say where.
        """
        try:
            self.guide.setHtml(guide_html(self.kb_dir))
        except Exception:
            self.guide.setHtml(guide_html())

    # -- drag and drop --------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if self._supported_urls(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._supported_urls(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._supported_urls(event)
        if not paths:
            event.ignore()
            return
        added, skipped = self.add_paths(paths)
        event.acceptProposedAction()
        if added:
            self.sig_files_added.emit(added)
        parts = []
        if added:
            parts.append(f"Copied {added} item{'s' if added != 1 else ''}")
        if skipped:
            parts.append(f"{skipped} unsupported and skipped")
        self.status.setText(" \u00b7 ".join(parts))
        self.refresh()

    def add_paths(self, paths: Sequence[Path]) -> tuple[int, int]:
        """Copy ``paths`` into the knowledge folder. Returns ``(added, skipped)``.

        Split out from ``dropEvent`` so the copy rules are testable without synthesising a Qt
        drag: what counts as supported, that a clash is suffixed rather than overwritten, and
        that the source is left alone.
        """
        import kb

        target_root = self.kb_dir
        added = skipped = 0
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return (0, len(paths))

        for source in paths:
            source = Path(source)
            try:
                if source.is_dir():
                    destination = _unique(target_root / source.name)
                    shutil.copytree(source, destination)
                    added += 1
                elif source.suffix.lower() in kb.SUPPORTED_SUFFIXES:
                    destination = _unique(target_root / source.name)
                    shutil.copy2(source, destination)
                    added += 1
                else:
                    skipped += 1
            except OSError:
                skipped += 1
        return (added, skipped)

    @staticmethod
    def _supported_urls(event) -> list[Path]:
        """Local paths from a drag event that are worth accepting.

        Folders are always accepted -- their contents are filtered by ``kb.iter_kb_files``
        later -- and loose files only when the suffix is one ``kb.py`` can read.
        """
        import kb

        data = event.mimeData()
        if data is None or not data.hasUrls():
            return []
        paths: list[Path] = []
        for url in data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or path.suffix.lower() in kb.SUPPORTED_SUFFIXES:
                paths.append(path)
        return paths


def _unique(path: Path) -> Path:
    """``name.md`` -> ``name (2).md`` when taken. Never overwrites the user's own files."""
    if not path.exists():
        return path
    for index in range(2, 100):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} (copy){path.suffix}")


def _open_in_explorer(folder: Path) -> bool:
    """Default folder opener. Injected in tests so no test opens a real Explorer window."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
