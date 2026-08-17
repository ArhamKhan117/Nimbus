"""Journal: the T3-3 review queue, made visible (SHELL_AND_CHAT.md §3 `S-2`).

``review.py`` has been recording what Nimbus has taught, on a spaced-repetition schedule, since
`T3-3` -- and until now the only way to reach it was to say "quiz me" and hope. This page is the
window onto it: how many items are due, what they are, how accurate the answers have been, and
a button to start.

## What it reads, and what it refuses to write

Reads ``ReviewQueue.due``, ``.stats()`` and ``.recap()``. It does **not** grade items: grading
needs a spoken answer and, for positional items, a real grounding call against the live screen,
which is the pipeline's job. The page's "Quiz me" emits a signal and stops there -- the same
route the spoken "quiz me" intent already takes.

``review.write_insights`` is deliberately behind an explicit button rather than called on
refresh. Writing a file to the user's disk as a side effect of looking at a page is the kind of
surprise that erodes trust in a local-first app, and ``insights.md`` is meant to be a thing they
asked for.

## The store is injected, and never constructed by default

``ReviewQueue()`` touches ``~/.nimbus/index.db``. Constructing one just because a page was
built would mean every shell test wrote to the developer's real database, which the test
conventions explicitly forbid. With nothing injected the page renders its empty state.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

import theme
from shell.widgets import Card, label, style_table, table_item

DUE_LIMIT = 20
"""How many due items to list. ``ReviewQueue.due`` defaults to 10; a page has room for more,
and the number due is shown separately anyway."""

RECAP_DAYS = 7
""""Learned this week" window, matching Home's "this week" framing."""

NO_NUMBER = "\u2014"


def accuracy_text(stats: dict) -> str:
    """"84%" or an honest "not yet reviewed". Pure, and it mirrors ``review.write_insights``.

    Same phrasing as the insights file on purpose: two different words for the same
    ungraded state would read as two different states.
    """
    correct = int(stats.get("correct", 0) or 0)
    wrong = int(stats.get("wrong", 0) or 0)
    graded = correct + wrong
    if graded <= 0:
        return "not yet reviewed"
    return f"{round(correct / graded * 100)}%"


class JournalPage(QWidget):
    """The review queue. ``queue_provider`` returns a ``review.ReviewQueue`` or ``None``."""

    sig_quiz_me = pyqtSignal()

    def __init__(
        self,
        *,
        queue_provider: Callable[[], object] | None = None,
        insights_path_provider: Callable[[], object] | None = None,
        today_provider: Callable[[], date] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._queue_provider = queue_provider
        self._insights_path_provider = insights_path_provider or _configured_insights_path
        self._today_provider = today_provider or date.today

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE[3])
        outer.addWidget(label("Journal", "PageTitle"))

        cards = QGridLayout()
        cards.setSpacing(theme.SPACE[3])
        cards.addWidget(self._build_due_card(), 0, 0)
        cards.addWidget(self._build_accuracy_card(), 0, 1)
        cards.addWidget(self._build_week_card(), 0, 2)
        for column in range(3):
            cards.setColumnStretch(column, 1)
        outer.addLayout(cards)

        self._outer = outer
        self._items_card = self._build_items_card()
        outer.addWidget(self._items_card, stretch=1)

        # Spare height below the cards rather than inside one. See ``knowledge.py``.
        self._tail = QWidget()
        outer.addWidget(self._tail, stretch=1)

        self.refresh()

    # -- public ---------------------------------------------------------------

    def queue(self):
        """The injected ``ReviewQueue``, or ``None``. Never constructs one."""
        if self._queue_provider is None:
            return None
        try:
            return self._queue_provider()
        except Exception:
            return None

    def refresh(self) -> None:
        queue = self.queue()
        if queue is None:
            self.due_count.setText(NO_NUMBER)
            self.accuracy.setText(NO_NUMBER)
            self.week_count.setText(NO_NUMBER)
            self._fill_items([])
            self.quiz_button.setEnabled(False)
            self.empty.setText(
                "Your journal starts filling itself as you ask questions. Nothing to review "
                "yet.")
            return

        today = self._today_provider()
        due = self._safe(lambda: queue.due(today=today, limit=DUE_LIMIT), [])
        stats = self._safe(queue.stats, {"total": 0, "correct": 0, "wrong": 0})
        recap = self._safe(
            lambda: queue.recap(since=today - timedelta(days=RECAP_DAYS), limit=DUE_LIMIT), [])

        self.due_count.setText(str(len(due)))
        self.accuracy.setText(accuracy_text(stats))
        self.accuracy_detail.setText(
            f"{stats.get('total', 0)} topics \u00b7 {stats.get('correct', 0)} right \u00b7 "
            f"{stats.get('wrong', 0)} wrong")
        self.week_count.setText(str(len(recap)))
        self._fill_items(due)
        self.quiz_button.setEnabled(bool(due))
        self.empty.setText(
            "Nothing due today. Items come back on a widening schedule -- tomorrow, three "
            "days, a week -- so this being empty is the system working.")

    def write_insights(self) -> None:
        """Write ``insights.md`` on request. Reports failure instead of swallowing it."""
        queue = self.queue()
        if queue is None:
            return
        try:
            import review

            today = self._today_provider()
            stats = queue.stats()
            due = queue.due(today=today, limit=DUE_LIMIT)
            path = review.write_insights(
                self._insights_path_provider(), stats, len(due))
            self.status.setText(f"Written to {path}")
        except Exception as exc:
            self.status.setText(f"Could not write the summary: {exc}")

    # -- construction ---------------------------------------------------------

    def _build_due_card(self) -> Card:
        card = Card("Due now")
        self.due_count = label(NO_NUMBER, "Display")
        card.add(self.due_count)
        card.add(label("topics ready to review", "Muted"))
        card.body.addStretch(1)
        return card

    def _build_accuracy_card(self) -> Card:
        card = Card("Accuracy")
        self.accuracy = label(NO_NUMBER, "Display")
        card.add(self.accuracy)
        self.accuracy_detail = label("", "Muted")
        card.add(self.accuracy_detail)
        card.body.addStretch(1)
        return card

    def _build_week_card(self) -> Card:
        card = Card("Learned this week")
        self.week_count = label(NO_NUMBER, "Display")
        card.add(self.week_count)
        card.add(label("new topics", "Muted"))
        card.body.addStretch(1)
        return card

    def _build_items_card(self) -> Card:
        card = Card("Review queue")
        self.empty = label("", "Secondary")
        card.add(self.empty)

        self.items = QTableWidget(0, 4)
        self.items.setHorizontalHeaderLabels(["Topic", "Application", "Due", "History"])
        style_table(self.items, stretch_column=0)
        card.add(self.items, stretch=1)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE[1])
        self.quiz_button = QPushButton("Quiz me")
        self.quiz_button.setObjectName("Primary")
        self.quiz_button.setToolTip(
            "Starts the same review Nimbus runs when you say \"quiz me\". Positional items are\n"
            "asked against your live screen, which is the part a flashcard app cannot do."
        )
        self.quiz_button.clicked.connect(self.sig_quiz_me.emit)
        row.addWidget(self.quiz_button)
        insights = QPushButton("Write summary file\u2026")
        insights.setToolTip(
            "Writes a plain-Markdown progress summary you can read or delete in any editor.")
        insights.clicked.connect(self.write_insights)
        row.addWidget(insights)
        row.addStretch(1)
        self.status = label("", "Muted")
        row.addWidget(self.status)
        card.add(buttons)
        return card

    # -- internals ------------------------------------------------------------

    def _fill_items(self, rows) -> None:
        rows = list(rows)
        self.items.setRowCount(len(rows))
        for index, item in enumerate(rows):
            correct = item.get("times_correct", 0)
            wrong = item.get("times_wrong", 0)
            cells = (
                table_item(str(item.get("question", ""))),
                table_item(str(item.get("app_name", "")), mono=True),
                table_item(str(item.get("next_review", "")), muted=True),
                table_item(
                    f"{correct}\u2713 {wrong}\u2717" if (correct or wrong) else NO_NUMBER,
                    muted=True),
            )
            for column, cell in enumerate(cells):
                cell.setToolTip(cell.text())
                self.items.setItem(index, column, cell)
        self.items.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self._outer.setStretchFactor(self._items_card, 1 if rows else 0)
        self._tail.setVisible(not rows)

    @staticmethod
    def _safe(call, fallback):
        try:
            return call()
        except Exception:
            return fallback


def _configured_insights_path():
    from config import INSIGHTS_PATH
    return INSIGHTS_PATH
