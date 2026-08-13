"""Knowledge Journal: spaced repetition over what Nimbus has taught (T3-3).

Nimbus already remembers every interaction per app in ``memory.py``. That record is used to
give the *model* context, and for nothing else. This module turns it into something for the
**user**: the things they asked about come back days later, on a schedule, so they actually
learn the software instead of re-asking the same question next month.

**Why this is not a flashcard app with extra steps.** Nimbus can see the screen, so a
review item can be *positional* -- "show me where the export button is" -- and be graded
against a real grounding call. No flashcard tool can ask that question, because none of them
know what is on screen. That is the differentiator, and it is why review items carry an
optional ``target_label``.

## Design decisions worth stating

**Same database, new table.** ``memory.py``'s schema uses ``CREATE TABLE IF NOT EXISTS`` and
enables WAL, so adding a table needs no migration and no ``ALTER``. Existing ``apps`` data is
untouched -- users have live databases and this must be purely additive.

**Scheduling is pure functions.** ``next_interval`` and ``adjust_ease`` take numbers and
return numbers, so the whole algorithm is exhaustively testable with no database and no
clock. The stateful part is only storage.

**Intent matching is local and keyword-based.** "next", "quiz me", "what should I review" are
matched here with no API call -- the whole point is that navigating your own journal is free
and instant. The precedent is ``app._looks_directional()``. The hazard is false positives
hijacking a real question, which is why ``classify_review_intent`` requires the transcript to
be *predominantly* a command rather than merely to contain one.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from config import INDEX_DB_PATH


INTERVALS_DAYS: tuple[int, ...] = (1, 3, 7, 14, 30, 60, 120)
"""Review intervals in days, indexed by ``interval_index``.

A fixed ladder rather than SM-2's computed interval. Deliberate: the classic formula needs a
per-item ease *and* a repetition count to produce sensible numbers, and it misbehaves badly
on small datasets, which is exactly what a personal journal is. A ladder is predictable,
explainable to the user, and impossible to get wrong.

Capped at 120 days because software changes. An item last reviewed four months ago may be
about a UI that no longer exists."""

MIN_EASE = 1.3
MAX_EASE = 2.8
DEFAULT_EASE = 2.5
"""Ease bounds, borrowed from SM-2's conventions.

Ease scales the ladder rather than replacing it, so a consistently easy item stretches out
and a hard one stays tight, while the ladder keeps the numbers sane. The floor matters: an
item the user keeps failing must not collapse to a zero-day interval and be asked forever."""

EASE_CORRECT_BONUS = 0.10
EASE_INCORRECT_PENALTY = 0.25
"""Asymmetric on purpose -- failure moves ease more than success.

Getting something right once is weak evidence of knowing it; getting it wrong is strong
evidence of not knowing it."""


# --- Pure scheduling ---------------------------------------------------------

def next_interval_index(interval_index: int, correct: bool) -> int:
    """Advance or reset the position on the interval ladder.

    A wrong answer resets to the beginning rather than stepping back one rung. Stepping back
    would keep a genuinely unknown item circulating at week-long gaps; resetting means the
    user sees it tomorrow, which is what not knowing something warrants.
    """
    if not correct:
        return 0
    return min(interval_index + 1, len(INTERVALS_DAYS) - 1)


def adjust_ease(ease: float, correct: bool) -> float:
    """Nudge the ease factor and clamp it into ``[MIN_EASE, MAX_EASE]``."""
    delta = EASE_CORRECT_BONUS if correct else -EASE_INCORRECT_PENALTY
    return max(MIN_EASE, min(MAX_EASE, ease + delta))


def next_interval_days(interval_index: int, ease: float) -> int:
    """Days until the next review, from ladder position scaled by ease.

    Never returns less than 1: a zero-day interval would make the item due again in the same
    session and the user would be asked the same thing repeatedly.
    """
    index = max(0, min(interval_index, len(INTERVALS_DAYS) - 1))
    base = INTERVALS_DAYS[index]
    scaled = base * (ease / DEFAULT_EASE)
    return max(1, min(int(round(scaled)), INTERVALS_DAYS[-1]))


def schedule(interval_index: int, ease: float, correct: bool) -> tuple[int, float, int]:
    """Full scheduling step. Returns ``(interval_index, ease, days_until_next)``.

    The single entry point callers should use, so ladder position and ease can never be
    updated inconsistently.
    """
    new_index = next_interval_index(interval_index, correct)
    new_ease = adjust_ease(ease, correct)
    return (new_index, new_ease, next_interval_days(new_index, new_ease))


# --- Local intent matching ---------------------------------------------------

_INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quiz", ("quiz me", "test me", "ask me", "quiz", "practise me", "practice me")),
    ("due", ("what should i review", "what do i need to review", "anything to review",
             "what's due", "whats due", "what is due", "review time")),
    ("recap", ("what did we cover", "what have we covered", "what did i learn",
               "what have i learned", "recap", "summarise today", "summarize today")),
)
"""Intent -> trigger phrases. Longest-first matching happens in the classifier."""

_MAX_INTENT_WORDS = 6
"""A transcript longer than this is treated as a real question, never a command.

This is the false-positive guard. "quiz me" is a command; "how would you quiz me on this
spreadsheet formula" is a question that happens to contain one, and hijacking it would be
worse than not having the feature. Six words is generous for every phrase above."""


def classify_review_intent(transcript: str) -> str | None:
    """Return ``"quiz"`` / ``"due"`` / ``"recap"``, or ``None`` if not a journal command.

    Runs locally with no API call -- navigating your own journal should be free and instant.

    Requires the transcript to be *predominantly* the command, not merely to contain it. A
    false positive here silently replaces a genuine question with a quiz, which is a far
    worse failure than a missed shortcut.
    """
    cleaned = " ".join((transcript or "").lower().replace("?", " ").split())
    if not cleaned or len(cleaned.split()) > _MAX_INTENT_WORDS:
        return None
    matches = [
        (len(phrase), intent)
        for intent, phrases in _INTENT_PATTERNS
        for phrase in phrases
        if phrase in cleaned
    ]
    if not matches:
        return None
    # Longest phrase wins: "what should i review" must not be shadowed by a bare "review".
    return max(matches)[1]


# --- Storage -----------------------------------------------------------------

class ReviewQueue:
    """Spaced-repetition store, sharing ``memory.py``'s SQLite database (T3-3).

    Single-writer, called from the Qt main thread, exactly like ``MemoryStore``. Every method
    opens and closes its own connection so nothing is held across turns.
    """

    def __init__(self, index_db_path: Path | str = INDEX_DB_PATH) -> None:
        self.index_db_path = Path(index_db_path)
        self.index_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.index_db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Create the ``review_queue`` table. Idempotent, and purely additive.

        Mirrors ``MemoryStore._ensure_schema`` deliberately: same ``CREATE TABLE IF NOT
        EXISTS`` contract, same WAL pragma, no ``ALTER`` against the existing ``apps``
        table. Users have live databases and this must not disturb them.
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_name       TEXT NOT NULL,
                    question       TEXT NOT NULL,
                    answer         TEXT NOT NULL,
                    target_label   TEXT NOT NULL DEFAULT '',
                    first_learned  TEXT NOT NULL,
                    next_review    TEXT NOT NULL,
                    interval_index INTEGER NOT NULL DEFAULT 0,
                    ease           REAL NOT NULL DEFAULT 2.5,
                    times_correct  INTEGER NOT NULL DEFAULT 0,
                    times_wrong    INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_next "
                "ON review_queue(next_review)"
            )
        finally:
            conn.close()

    def add(
        self,
        app_name: str,
        question: str,
        answer: str,
        target_label: str = "",
        today: date | None = None,
    ) -> int | None:
        """Queue a taught item for review tomorrow. Returns its id, or None if skipped.

        ``target_label`` makes the item *positional* -- it can later be asked as "show me
        where X is" and graded against a real grounding call, which is the whole reason this
        beats a flashcard app.

        Silently skips empty questions and answers rather than raising: this is called at the
        end of a successful interaction, and a journal write must never be able to fail a
        turn the user already got value from.
        """
        question, answer = (question or "").strip(), (answer or "").strip()
        if not question or not answer:
            return None
        today = today or date.today()
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO review_queue
                    (app_name, question, answer, target_label,
                     first_learned, next_review, interval_index, ease)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    (app_name or "unknown").strip().lower(),
                    question, answer, (target_label or "").strip(),
                    today.isoformat(),
                    (today + timedelta(days=INTERVALS_DAYS[0])).isoformat(),
                    DEFAULT_EASE,
                ),
            )
            return cursor.lastrowid
        finally:
            conn.close()

    def due(self, today: date | None = None, limit: int = 10) -> list[dict]:
        """Items due for review, oldest due-date first."""
        today = today or date.today()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM review_queue
                WHERE next_review <= ?
                ORDER BY next_review ASC, id ASC
                LIMIT ?
                """,
                (today.isoformat(), limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def grade(self, item_id: int, correct: bool, today: date | None = None) -> dict | None:
        """Record an answer and reschedule. Returns the updated row, or None if absent."""
        today = today or date.today()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM review_queue WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            index, ease, days = schedule(
                int(row["interval_index"]), float(row["ease"]), correct)
            conn.execute(
                """
                UPDATE review_queue
                SET interval_index = ?, ease = ?, next_review = ?,
                    times_correct = times_correct + ?,
                    times_wrong = times_wrong + ?
                WHERE id = ?
                """,
                (
                    index, ease, (today + timedelta(days=days)).isoformat(),
                    1 if correct else 0, 0 if correct else 1, item_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM review_queue WHERE id = ?", (item_id,)
            ).fetchone()
            return dict(updated)
        finally:
            conn.close()

    def recap(self, app_name: str | None = None, since: date | None = None,
              limit: int = 10) -> list[dict]:
        """Items first learned on or after ``since`` -- powers "what did we cover today?"."""
        since = since or date.today()
        conn = self._connect()
        try:
            if app_name:
                rows = conn.execute(
                    "SELECT * FROM review_queue WHERE first_learned >= ? "
                    "AND app_name = ? ORDER BY id DESC LIMIT ?",
                    (since.isoformat(), app_name.strip().lower(), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM review_queue WHERE first_learned >= ? "
                    "ORDER BY id DESC LIMIT ?",
                    (since.isoformat(), limit),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        """Totals for the insights summary."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(times_correct), 0) AS correct,
                       COALESCE(SUM(times_wrong), 0) AS wrong
                FROM review_queue
                """
            ).fetchone()
            return {
                "total": int(row["total"]),
                "correct": int(row["correct"]),
                "wrong": int(row["wrong"]),
            }
        finally:
            conn.close()


def format_recap_for_speech(items: list[dict]) -> str:
    """Turn recap rows into one spoken sentence (T3-3).

    Written for the ear, per the system prompt's contract: no lists, no numbering, no
    markdown. Caps at three topics because a spoken list longer than that is unfollowable --
    the limit is a speech constraint, not a data one.
    """
    if not items:
        return "we haven't covered anything new yet today."
    topics = [i["question"].rstrip("?").strip().lower() for i in items[:3]]
    if len(topics) == 1:
        return f"today we covered {topics[0]}."
    if len(topics) == 2:
        return f"today we covered {topics[0]}, and {topics[1]}."
    return f"today we covered {topics[0]}, {topics[1]}, and {topics[2]}."


def write_insights(path: Path | str, stats: dict, due_count: int) -> Path:
    """Write a human-readable progress summary to ``INSIGHTS_PATH`` (T3-3).

    Plain Markdown, honouring the transparency contract ``memory.py`` sets out: the user can
    read, edit and delete their own data with no tooling. ``config.INSIGHTS_PATH`` was
    defined and written by nothing until now.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    graded = stats["correct"] + stats["wrong"]
    accuracy = f"{round(stats['correct'] / graded * 100)}%" if graded else "not yet reviewed"
    path.write_text(
        "# Nimbus learning insights\n\n"
        f"_Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"
        f"- Topics in your journal: **{stats['total']}**\n"
        f"- Due for review now: **{due_count}**\n"
        f"- Answered correctly: **{stats['correct']}**\n"
        f"- Answered incorrectly: **{stats['wrong']}**\n"
        f"- Accuracy: **{accuracy}**\n\n"
        "This file is yours. Edit or delete it freely; Nimbus rewrites it as you learn.\n",
        encoding="utf-8",
    )
    return path
