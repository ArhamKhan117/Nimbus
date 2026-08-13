"""Tests for the expanded knowledge base (T3-2): folders, PDFs, DOCX, ranking.

Priority order of what these guard:

1. **Backward compatibility.** A user with a flat `myapp.exe.md` must be completely
   unaffected. That is the gate, and `TestFlatFileStillWorks` is the whole point of it.
2. **The sanitiser drift.** `kb` had its own copy of `memory._sanitize_app_name` whose
   docstring claimed to mirror it "exactly". Measurement found 7 of 15 inputs disagreeing,
   which broke the documented mental model: users are told to copy the canonical name out of
   their memory folder, but the two functions looked for different filenames.
3. **Never breaking the pipeline.** KB files are user-controlled. A corrupt PDF, an exotic
   encoding or a locked file must cost at most that one file.
4. **Ranking beats tail-truncation** without reordering content that already fits.
"""

from pathlib import Path

import pytest


class TestSanitiserDriftGuard:
    """The `⚠ VERIFY` item for T3-2, and it found a real defect."""

    CASES = [
        "MYAPP.EXE", "myapp.exe", "Fusion360.exe", "notepad++.exe", "Kiro.exe",
        "My App: v2.EXE", "a/b\\c.exe", "app?.exe", 'we"ird.exe', "pipe|app.exe",
        "star*.exe", "lt<gt>.exe", "  spaced.exe  ", "trailing.exe ", "UPPER.EXE",
    ]

    @pytest.mark.parametrize("app_name", CASES)
    def test_sanitize_matches_memory_module_exactly(self, app_name):
        """Drift guard across kb.py and memory.py.

        Users navigate by matching filenames between ~/.nimbus/memory/ and the KB folder, so
        a disagreement means a KB file that is silently never found.
        """
        from kb import _sanitize_app_name
        from memory import _sanitize_app_name as memory_sanitize
        assert _sanitize_app_name(app_name) == memory_sanitize(app_name)

    def test_whitespace_is_stripped_like_memory(self):
        """The case that actually bit: memory showed `spaced.exe`, kb looked for
        `  spaced.exe  ` and found nothing."""
        from kb import _sanitize_app_name
        assert _sanitize_app_name("  spaced.exe  ") == "spaced.exe"

    def test_all_windows_reserved_chars_replaced(self):
        from kb import _sanitize_app_name
        for char in '<>:"/\\|?*':
            assert char not in _sanitize_app_name(f"a{char}b.exe")

    def test_empty_input_returns_empty_not_raises(self):
        """memory raises ValueError; recall treats empty as "no KB", so kb must absorb it."""
        from kb import _sanitize_app_name
        assert _sanitize_app_name("") == ""
        assert _sanitize_app_name("   ") == ""


class TestFlatFileStillWorks:
    """Backward-compat gate — existing user files must not break."""

    def test_flat_file_still_works(self, tmp_path: Path):
        from kb import recall
        (tmp_path / "myapp.exe.md").write_text("flat content", encoding="utf-8")
        assert recall("MYAPP.EXE", kb_dir=tmp_path) == ("flat content", "myapp.exe")

    def test_no_query_over_budget_still_tail_truncates(self, tmp_path: Path):
        """The original overflow behaviour, preserved exactly for callers passing no query."""
        from kb import recall
        (tmp_path / "a.exe.md").write_text("x" * 100 + "TAIL", encoding="utf-8")
        content, _ = recall("a.exe", kb_dir=tmp_path, max_chars=10)
        assert content == "xxxxxxTAIL"

    def test_missing_everything_returns_empty(self, tmp_path: Path):
        from kb import recall
        assert recall("nothing.exe", kb_dir=tmp_path) == ("", "")

    def test_blank_app_name_returns_empty(self, tmp_path: Path):
        from kb import recall
        assert recall("   ", kb_dir=tmp_path) == ("", "")

    def test_non_positive_max_chars_returns_empty(self, tmp_path: Path):
        """text[-0:] returns the WHOLE string, so 0 must be caught explicitly."""
        from kb import recall
        (tmp_path / "a.exe.md").write_text("secret", encoding="utf-8")
        assert recall("a.exe", kb_dir=tmp_path, max_chars=0) == ("", "")

    def test_empty_flat_file_is_treated_as_absent(self, tmp_path: Path):
        from kb import recall
        (tmp_path / "a.exe.md").write_text("   \n ", encoding="utf-8")
        assert recall("a.exe", kb_dir=tmp_path) == ("", "")


class TestFolderSupport:
    def test_folder_contents_concatenated_in_stable_order(self, tmp_path: Path):
        from kb import recall
        folder = tmp_path / "myapp.exe"
        folder.mkdir()
        (folder / "b_second.md").write_text("SECOND", encoding="utf-8")
        (folder / "a_first.md").write_text("FIRST", encoding="utf-8")
        content, name = recall("MyApp.exe", kb_dir=tmp_path)
        assert name == "myapp.exe"
        assert content.index("FIRST") < content.index("SECOND")

    def test_order_is_identical_across_runs(self, tmp_path: Path):
        """Unstable ordering would change what survives truncation between two identical
        questions -- the kind of non-determinism that makes a bug unreproducible."""
        from kb import recall
        folder = tmp_path / "a.exe"
        folder.mkdir()
        for i in range(8):
            (folder / f"f{i}.md").write_text(f"content{i}", encoding="utf-8")
        first = recall("a.exe", kb_dir=tmp_path)[0]
        assert all(recall("a.exe", kb_dir=tmp_path)[0] == first for _ in range(3))

    def test_nested_subfolders_are_read(self, tmp_path: Path):
        from kb import recall
        nested = tmp_path / "a.exe" / "guides" / "advanced"
        nested.mkdir(parents=True)
        (nested / "deep.md").write_text("DEEP CONTENT", encoding="utf-8")
        assert "DEEP CONTENT" in recall("a.exe", kb_dir=tmp_path)[0]

    def test_flat_file_and_folder_both_used_flat_first(self, tmp_path: Path):
        """An existing user who later adds a folder keeps their original file leading."""
        from kb import recall
        (tmp_path / "a.exe.md").write_text("FLATFILE", encoding="utf-8")
        folder = tmp_path / "a.exe"
        folder.mkdir()
        (folder / "extra.md").write_text("FOLDERFILE", encoding="utf-8")
        content, _ = recall("a.exe", kb_dir=tmp_path)
        assert content.index("FLATFILE") < content.index("FOLDERFILE")

    def test_unsupported_files_ignored_silently(self, tmp_path: Path):
        """Users keep screenshots and spreadsheets beside their notes; not an error."""
        from kb import recall
        folder = tmp_path / "a.exe"
        folder.mkdir()
        (folder / "notes.md").write_text("KEEP", encoding="utf-8")
        (folder / "diagram.png").write_bytes(b"\x89PNG fake")
        (folder / "data.xlsx").write_bytes(b"fake")
        content, _ = recall("a.exe", kb_dir=tmp_path)
        assert "KEEP" in content
        assert "PNG" not in content

    def test_each_folder_file_is_named_inline(self, tmp_path: Path):
        """So the model knows two documents are separate, and the user can tell which of
        their files an answer came from."""
        from kb import recall
        folder = tmp_path / "a.exe"
        folder.mkdir()
        (folder / "shortcuts.md").write_text("ctrl+s saves", encoding="utf-8")
        assert "shortcuts.md" in recall("a.exe", kb_dir=tmp_path)[0]

    def test_file_count_is_capped(self, tmp_path: Path):
        """Guards against a folder with thousands of files stalling the hot path."""
        from kb import MAX_FILES_PER_APP, iter_kb_files
        folder = tmp_path / "a.exe"
        folder.mkdir()
        for i in range(MAX_FILES_PER_APP + 15):
            (folder / f"f{i:04d}.md").write_text("x", encoding="utf-8")
        assert len(iter_kb_files(folder)) == MAX_FILES_PER_APP

    def test_missing_folder_is_not_an_error(self, tmp_path: Path):
        from kb import iter_kb_files
        assert iter_kb_files(tmp_path / "nope") == []

    def test_txt_files_are_read(self, tmp_path: Path):
        from kb import recall
        folder = tmp_path / "a.exe"
        folder.mkdir()
        (folder / "notes.txt").write_text("PLAIN TEXT", encoding="utf-8")
        assert "PLAIN TEXT" in recall("a.exe", kb_dir=tmp_path)[0]


class TestDocumentExtraction:
    @staticmethod
    def _pdf_with_text(lines: list[str]) -> bytes:
        """Build a tiny but valid single-page PDF containing a real text stream.

        Hand-built rather than pulling in reportlab as a test-only dependency. A blank page
        from `PdfWriter.add_blank_page` extracts to an empty string, which would only prove
        "does not raise" -- not that extraction actually works.
        """
        text_ops = "BT /F1 12 Tf 72 760 Td 14 TL\n"
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            text_ops += f"({escaped}) Tj T*\n"
        text_ops += "ET"
        stream = text_ops.encode("latin-1")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for index, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode()
        return bytes(out)

    def test_pdf_text_is_extracted(self, tmp_path: Path):
        pytest.importorskip("pypdf")
        from kb import extract_text

        path = tmp_path / "doc.pdf"
        path.write_bytes(self._pdf_with_text([
            "OrionFlow troubleshooting",
            "Error ORN-4471 means the runlet registry lock is stale.",
        ]))
        text = extract_text(path)
        assert "ORN-4471" in text
        assert "registry lock" in text

    def test_pdf_in_a_folder_reaches_recall(self, tmp_path: Path):
        """The in-house-software case end to end: a PDF manual answering a question no model
        could know."""
        pytest.importorskip("pypdf")
        from kb import recall

        folder = tmp_path / "orionflow.exe"
        folder.mkdir()
        (folder / "guide.pdf").write_bytes(self._pdf_with_text([
            "Error ORN-4471 means the runlet registry lock is stale.",
        ]))
        content, name = recall("OrionFlow.exe", kb_dir=tmp_path, query="ORN-4471")
        assert "ORN-4471" in content
        assert name == "orionflow.exe"

    def test_docx_paragraphs_and_tables_are_extracted(self, tmp_path: Path):
        docx = pytest.importorskip("docx")
        from kb import extract_text

        document = docx.Document()
        document.add_paragraph("PARAGRAPH CONTENT")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "CTRL+S"
        table.rows[0].cells[1].text = "SAVE FILE"
        path = tmp_path / "doc.docx"
        document.save(str(path))

        text = extract_text(path)
        assert "PARAGRAPH CONTENT" in text
        # Tables carry shortcuts and field definitions -- exactly the useful content.
        assert "CTRL+S" in text and "SAVE FILE" in text

    def test_docx_in_a_folder_reaches_recall(self, tmp_path: Path):
        docx = pytest.importorskip("docx")
        from kb import recall

        folder = tmp_path / "a.exe"
        folder.mkdir()
        document = docx.Document()
        document.add_paragraph("INHOUSE TOOL GUIDE")
        document.save(str(folder / "guide.docx"))
        assert "INHOUSE TOOL GUIDE" in recall("a.exe", kb_dir=tmp_path)[0]

    def test_unreadable_file_skipped_not_fatal(self, tmp_path: Path):
        """KB files are user-controlled; a bad encoding must not break the pipeline.
        app.py already wraps kb.recall in try/except — keep that true."""
        from kb import recall
        folder = tmp_path / "a.exe"
        folder.mkdir()
        (folder / "corrupt.pdf").write_bytes(b"not a real pdf at all")
        (folder / "good.md").write_text("GOOD CONTENT", encoding="utf-8")
        content, _ = recall("a.exe", kb_dir=tmp_path)
        assert "GOOD CONTENT" in content

    def test_extract_never_raises_on_anything(self, tmp_path: Path):
        from kb import extract_text
        broken = tmp_path / "x.docx"
        broken.write_bytes(b"\x00\x01 not a docx")
        assert extract_text(broken) == ""
        assert extract_text(tmp_path / "missing.pdf") == ""
        assert extract_text(tmp_path / "thing.unknown") == ""

    def test_invalid_utf8_is_replaced_not_fatal(self, tmp_path: Path):
        from kb import extract_text
        path = tmp_path / "weird.md"
        path.write_bytes(b"valid \xff\xfe invalid")
        assert "valid" in extract_text(path)


class TestRanking:
    def test_under_budget_content_is_returned_unchanged(self):
        """Ranking must not reorder content that all fits -- document order carries meaning."""
        from kb import rank_and_truncate
        text = "# A\nalpha\n# B\nbeta"
        assert rank_and_truncate(text, "beta", 10_000) == text

    def test_over_budget_content_ranked_then_truncated(self):
        from kb import rank_and_truncate
        text = (
            "# Intro\n" + "filler " * 200 + "\n"
            "# Exporting\nuse the export button to render your project\n"
            "# Licensing\n" + "legal " * 200
        )
        result = rank_and_truncate(text, "how do i export my project", 200)
        assert "export button" in result
        assert len(result) <= 200

    def test_no_query_falls_back_to_tail_truncation(self):
        from kb import rank_and_truncate
        text = "# A\n" + "x" * 100 + "\n# B\nTAILEND"
        assert rank_and_truncate(text, "", 12).endswith("TAILEND")

    def test_short_query_words_are_ignored(self):
        """"is", "a", "to" match everything and rank nothing."""
        from kb import query_terms
        assert query_terms("what is a to be or not") == {"what", "not"}

    def test_kept_sections_stay_in_document_order(self):
        """Headings must still read in sequence, not by score."""
        from kb import rank_and_truncate
        text = (
            "# First\nexport settings live here\n"
            "# Second\n" + "pad " * 100 + "\n"
            "# Third\nexport presets are listed here\n"
        )
        result = rank_and_truncate(text, "export", 120)
        if "First" in result and "Third" in result:
            assert result.index("First") < result.index("Third")

    def test_score_counts_distinct_terms_not_occurrences(self):
        """A section repeating one word is not more relevant than one covering five.
        Counting occurrences would let a glossary entry outrank the real answer."""
        from kb import score_section
        repeated = "export export export export export"
        varied = "export render project"
        assert score_section(varied, {"export", "render", "project"}) > \
            score_section(repeated, {"export", "render", "project"})

    def test_no_headings_falls_back_to_one_section(self):
        """An extracted PDF has no Markdown headings; splitting it would cut mid-sentence."""
        from kb import split_sections
        assert split_sections("plain text with no headings at all") == [
            "plain text with no headings at all"]

    def test_empty_text_has_no_sections(self):
        from kb import split_sections
        assert split_sections("   ") == []

    def test_ranking_reaches_recall(self, tmp_path: Path):
        from kb import recall
        folder = tmp_path / "a.exe"
        folder.mkdir()
        (folder / "a_licensing.md").write_text(
            "# Licensing\n" + "legal " * 300, encoding="utf-8")
        (folder / "b_export.md").write_text(
            "# Exporting\nclick the export button to render", encoding="utf-8")
        content, _ = recall(
            "a.exe", kb_dir=tmp_path, max_chars=200, query="how do i export")
        assert "export button" in content

    def test_zero_score_sections_not_kept_once_relevant_ones_are(self):
        from kb import rank_and_truncate
        text = (
            "# Relevant\nexport render project\n"
            "# Irrelevant\n" + "unrelated words here " * 20
        )
        result = rank_and_truncate(text, "export render project", 60)
        assert "export render project" in result
        assert "unrelated" not in result


class TestGuideSeeding:
    """The knowledge base is the most powerful feature nobody discovers.

    `config._resolve_kb_dir` creates the folder at startup, but nothing explained it, so a
    new user saw an empty directory with no indication that a file must be named
    `orionflow.exe.md` to match the executable. That convention is not guessable, so without a
    seeded guide the whole feature is invisible.
    """

    def test_guide_is_written_when_absent(self, tmp_path: Path):
        from kb import GUIDE_FILENAME, ensure_guide
        path = ensure_guide(kb_dir=tmp_path)
        assert path == tmp_path / GUIDE_FILENAME
        assert path.is_file()

    def test_guide_explains_the_naming_convention(self, tmp_path: Path):
        """The single most important thing it must convey."""
        text = ensure_and_read(tmp_path)
        assert ".exe" in text
        assert "memory" in text.lower(), "must say where to find the canonical name"

    def test_guide_documents_every_supported_format(self, tmp_path: Path):
        from kb import SUPPORTED_SUFFIXES
        text = ensure_and_read(tmp_path).lower()
        for suffix in SUPPORTED_SUFFIXES:
            assert suffix in text, f"{suffix} is supported but undocumented"

    def test_guide_explains_why_headings_matter(self, tmp_path: Path):
        """Without headings a large file is one block and gets cut, so this is load-bearing
        advice rather than styling."""
        text = ensure_and_read(tmp_path).lower()
        assert "heading" in text

    def test_guide_states_the_privacy_position(self, tmp_path: Path):
        text = ensure_and_read(tmp_path).lower()
        assert "ollama" in text or "never leaves" in text

    def test_existing_guide_is_never_overwritten(self, tmp_path: Path):
        """A user may edit or replace it; their content must survive."""
        from kb import GUIDE_FILENAME, ensure_guide
        path = tmp_path / GUIDE_FILENAME
        path.write_text("MY OWN NOTES", encoding="utf-8")
        ensure_guide(kb_dir=tmp_path)
        assert path.read_text(encoding="utf-8") == "MY OWN NOTES"

    def test_creates_the_folder_if_missing(self, tmp_path: Path):
        from kb import ensure_guide
        target = tmp_path / "does" / "not" / "exist"
        assert ensure_guide(kb_dir=target) is not None
        assert target.is_dir()

    def test_unwritable_folder_returns_none_not_raises(self, tmp_path: Path, mocker):
        """A read-only or redirected folder must not prevent Nimbus starting."""
        from kb import ensure_guide
        mocker.patch(
            "pathlib.Path.write_text", side_effect=OSError("read-only"))
        assert ensure_guide(kb_dir=tmp_path) is None

    def test_guide_cannot_be_mistaken_for_app_content(self, tmp_path: Path):
        """A flat lookup reads `<app>.md`, so this would need an app literally named
        `README` with no extension. get_foreground_app returns an .exe basename."""
        from kb import ensure_guide, recall
        ensure_guide(kb_dir=tmp_path)
        assert recall("readme.exe", kb_dir=tmp_path) == ("", "")
        assert recall("Kiro.exe", kb_dir=tmp_path) == ("", "")

    def test_guide_is_embedded_not_a_data_file(self):
        """Shipping it as a PyInstaller `datas` entry would need sys._MEIPASS resolution that
        differs between the frozen build and a source checkout -- a recurring source of
        "works in dev, missing in the installer" bugs."""
        import kb
        assert isinstance(kb._GUIDE_TEXT, str)
        assert len(kb._GUIDE_TEXT) > 500

    def test_startup_seeds_the_guide(self):
        """Regression gate: without the startup call, users with an existing empty folder
        never get it."""
        import inspect
        import app
        assert "kb.ensure_guide()" in inspect.getsource(app)


def ensure_and_read(tmp_path: Path) -> str:
    from kb import GUIDE_FILENAME, ensure_guide
    ensure_guide(kb_dir=tmp_path)
    return (tmp_path / GUIDE_FILENAME).read_text(encoding="utf-8")


class TestSettingsDiscoverability:
    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_settings_has_an_open_folder_button(self, qt_app):
        from PyQt6.QtWidgets import QPushButton
        import settings_dialog

        dialog = settings_dialog.SettingsDialog()
        try:
            labels = [b.text() for b in dialog.findChildren(QPushButton)]
            assert any("knowledge base" in text.lower() for text in labels), labels
        finally:
            dialog.deleteLater()

    def test_button_tooltip_shows_the_naming_convention(self, qt_app):
        """A button labelled "open folder" with no explanation would not fix discovery."""
        from PyQt6.QtWidgets import QPushButton
        import settings_dialog

        dialog = settings_dialog.SettingsDialog()
        try:
            button = next(
                b for b in dialog.findChildren(QPushButton)
                if "knowledge base" in b.text().lower()
            )
            tooltip = button.toolTip()
            assert ".exe.md" in tooltip, "must show the naming pattern"
            assert ".pdf" in tooltip and ".docx" in tooltip
        finally:
            dialog.deleteLater()

    def test_opening_seeds_the_guide(self, qt_app, tmp_path, mocker):
        """Covers a user whose folder predates this version, or who deleted the guide.

        Patches `kb.KB_DIR`, not `config.KB_DIR` — kb.py holds its own
        `from config import KB_DIR` reference, so patching config alone has no effect on it.
        That divergence is exactly why `_on_open_kb_folder` now derives the folder it opens
        from the path `ensure_guide` returns.
        """
        import kb
        import settings_dialog
        from PyQt6.QtGui import QDesktopServices

        mocker.patch.object(kb, "KB_DIR", tmp_path)
        opened = []
        mocker.patch.object(
            QDesktopServices, "openUrl",
            lambda url: opened.append(url.toLocalFile()) or True)

        dialog = settings_dialog.SettingsDialog()
        try:
            dialog._on_open_kb_folder()
        finally:
            dialog.deleteLater()
        assert (tmp_path / kb.GUIDE_FILENAME).is_file()
        assert opened and Path(opened[0]) == tmp_path, (
            "the folder opened must be the one seeded"
        )

    def test_failure_to_open_tells_the_user_the_path(self, qt_app, tmp_path, mocker):
        """The user clicked a button, so a silent no-op is the wrong failure mode -- unlike
        the startup call, which is deliberately silent."""
        import kb
        import settings_dialog
        from PyQt6.QtGui import QDesktopServices

        mocker.patch.object(kb, "KB_DIR", tmp_path)
        mocker.patch.object(QDesktopServices, "openUrl", return_value=False)
        shown = []
        mocker.patch.object(
            settings_dialog.QMessageBox, "information",
            lambda *args, **kwargs: shown.append(args))

        dialog = settings_dialog.SettingsDialog()
        try:
            dialog._on_open_kb_folder()
        finally:
            dialog.deleteLater()
        assert shown, "user must be told where the folder is"


class TestSettingsFitsSmallScreens:
    """Regression: the dialog grew past a 1366x768 laptop across Tiers 1-3.

    Measured at 744px of content -- 783 with the window frame -- against 728 usable on a
    768-tall screen. The Save button would have landed off-screen, on a dialog that is modal
    at first launch, so the user could not have completed setup at all.

    Growth came from work in these tiers: the Privacy group, the experimental group, the
    restart note, the hotkey capture row and the knowledge-base button.
    """

    @pytest.fixture(scope="class")
    def qt_app(self):
        from PyQt6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def _dialog(self):
        import settings_dialog
        return settings_dialog.SettingsDialog()

    def test_content_is_scrollable(self, qt_app):
        from PyQt6.QtWidgets import QScrollArea
        dialog = self._dialog()
        try:
            assert dialog.findChild(QScrollArea) is not None
        finally:
            dialog.deleteLater()

    def test_save_button_is_outside_the_scroll_area(self, qt_app):
        """The load-bearing part. A fully scrolled dialog can still hide Save below the
        fold; keeping the button box in the shell makes that impossible however many
        settings are added later."""
        from PyQt6.QtWidgets import QDialogButtonBox, QScrollArea
        dialog = self._dialog()
        try:
            scroll = dialog.findChild(QScrollArea)
            box = dialog.findChild(QDialogButtonBox)
            assert box is not None and scroll is not None
            assert box not in scroll.findChildren(QDialogButtonBox)
        finally:
            dialog.deleteLater()

    @pytest.mark.parametrize("screen_height", [768, 900, 1080, 1440])
    def test_minimum_height_fits_common_screens(self, qt_app, screen_height):
        dialog = self._dialog()
        try:
            usable = screen_height - 40   # taskbar
            frame = 39                    # title bar + borders
            assert dialog.minimumSizeHint().height() + frame <= usable
        finally:
            dialog.deleteLater()

    def test_opens_at_content_height_not_minimum(self, qt_app):
        """Scrolling alone would let it open at ~111px -- a letterbox nobody can use."""
        dialog = self._dialog()
        try:
            assert dialog.height() > 400
        finally:
            dialog.deleteLater()

    def test_opens_within_the_screen_cap(self, qt_app):
        from PyQt6.QtWidgets import QApplication
        dialog = self._dialog()
        try:
            available = QApplication.primaryScreen().availableGeometry().height()
            assert dialog.height() <= int(available * 0.88) + 1
        finally:
            dialog.deleteLater()

    def test_sized_from_content_not_the_scroll_area(self, qt_app):
        """A QScrollArea reports its OWN small sizeHint, not its child's. Sizing from the
        dialog's layout produced a 426px letterbox."""
        dialog = self._dialog()
        try:
            assert dialog.height() >= min(
                dialog._page.sizeHint().height(), dialog.height())
            assert dialog._page.sizeHint().height() > 400
        finally:
            dialog.deleteLater()

    def test_every_control_is_still_reachable(self, qt_app):
        """Guards against the scroll refactor silently dropping a widget out of the tree."""
        from PyQt6.QtWidgets import QCheckBox, QPushButton
        dialog = self._dialog()
        try:
            assert dialog._hotkey_capture is not None
            assert dialog._hotkey_input is not None
            assert dialog._privacy_checkbox is not None
            assert len(dialog._experimental_checkboxes) == 4
            labels = [b.text().lower() for b in dialog.findChildren(QPushButton)]
            assert any("knowledge base" in text for text in labels)
            assert any("clear all" in text for text in labels)
            # "Teaching mode" since the annotation toggle became a highlighted feature row: the
            # long "draw on screen — boxes, arrows, ..." label moved into the description beside
            # it, where a user deciding whether to turn it on can actually read it.
            assert any(
                "teaching mode" in c.text().lower()
                for c in dialog.findChildren(QCheckBox)
            )
        finally:
            dialog.deleteLater()


class TestPipelineIntegration:
    def test_app_passes_the_transcript_as_query(self):
        """Regression gate: without the query, ranking never activates and the item is
        pointless."""
        import inspect
        import app
        source = inspect.getsource(app)
        assert "kb.recall(app_name, query=transcript)" in source

    def test_recall_signature_keeps_query_optional(self):
        """Existing callers and tests must keep working without it."""
        import inspect
        import kb
        params = inspect.signature(kb.recall).parameters
        assert params["query"].default == ""

    def test_supported_suffixes_are_documented_set(self):
        from kb import DOCX_SUFFIXES, PDF_SUFFIXES, SUPPORTED_SUFFIXES, TEXT_SUFFIXES
        assert SUPPORTED_SUFFIXES == TEXT_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES
        assert ".md" in SUPPORTED_SUFFIXES and ".pdf" in SUPPORTED_SUFFIXES
        assert ".docx" in SUPPORTED_SUFFIXES
