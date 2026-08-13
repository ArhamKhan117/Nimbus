"""Durable chat sessions for the HUD: SQLite store plus history rebuild (SHELL_AND_CHAT.md §4).

Two new tables in the **existing** ``~/.nimbus/index.db``, alongside ``apps`` (``memory.py``)
and ``review_queue`` (``review.py``). Same ``CREATE TABLE IF NOT EXISTS`` contract, same WAL
pragma, no ``ALTER`` -- users have live databases and this must be purely additive.
``test_existing_memory_and_review_tables_untouched`` is the gate on that promise.

## Structure mirrors ``review.ReviewQueue`` deliberately

``isolation_level=None``, ``row_factory = sqlite3.Row``, a connection opened and closed per
method, nothing held across turns. Three writers now share one database, which is fine under
WAL's single-writer model **provided every write happens on the Qt main thread**. That is why
``ChatHud.append`` -- not ``_pipeline_worker`` -- is the thing that calls ``add_message``: the
HUD lives on the Qt main thread by definition, so the invariant is structural rather than a
comment someone has to remember.

## Why the pure functions are separated out

``auto_title``, ``build_history`` and ``should_auto_new_session`` take values and return
values. No database, no clock, no Qt. The whole of the subtle behaviour -- the ten-exchange
window, the image budget, when a new session is justified -- is therefore testable
exhaustively and fast, exactly as ``review.py`` splits scheduling from storage.

## ``_history`` is the single source of truth, and this module is only its record

``app.py`` keeps ``_history`` in memory and passes it to the model. This store is a *record*
of the conversation plus what the model does not need (screenshots, system notes, timestamps).
Nothing reads back from it per turn, because that would put a database read on the hot path
and couple ``_pipeline_worker`` to the UI.

The two places the record flows back into ``_history`` are ``start_new_session`` and
``switch_session``, and both mutate the caller's list **in place**. That is the whole point:
"new chat" that only starts a new visual thread while still sending the last ten exchanges is
a lie, and making the clear part of the same call means a caller cannot create a session and
forget it (Invariant 7).
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from config import INDEX_DB_PATH, resolve_bounded_int_setting, resolve_setting


# --- Roles -------------------------------------------------------------------

ROLE_USER = "user"
ROLE_NIMBUS = "nimbus"
ROLE_SYSTEM = "system"

ROLES: tuple[str, ...] = (ROLE_USER, ROLE_NIMBUS, ROLE_SYSTEM)
"""``system`` is not padding -- it is how the HUD explains an *absence*.

"Screenshot skipped -- a password manager was open" (T2-1), "Cancelled" (T2-2), "New chat
started". Without a role for those, a privacy-suppressed turn looks indistinguishable from
Nimbus malfunctioning, and the user's conclusion is that the app is broken rather than that
it protected them."""


# --- Storage layout ----------------------------------------------------------

CHATS_DIR: Path = Path(INDEX_DB_PATH).parent / "chats"
"""``~/.nimbus/chats/<session_id>/<message_id>.jpg`` plus ``_thumb.jpg`` siblings.

Derived from ``INDEX_DB_PATH`` rather than declared independently so that pointing the
database somewhere else (which the tests and ``INDEX_DB_PATH`` env override both do) moves
the screenshots with it instead of scattering test images into the real profile."""

DEFAULT_RETENTION_DAYS = 14
"""Mirrors ``DIAGNOSTIC_RETENTION_DAYS``' pattern. ~150 KB per turn is ~50 MB after a few
hundred interactions, so unbounded growth is not hypothetical."""

MAX_HISTORY_EXCHANGES = 10
"""Matches ``app._MAX_HISTORY_EXCHANGES``. Duplicated rather than imported because importing
``app`` here would drag the whole orchestrator -- and a ``QApplication`` -- into a module that
must be testable on its own (§9.1). ``test_history_window_matches_the_app_constant`` pins the
two together so they cannot drift silently."""

TITLE_MAX_CHARS = 48
THUMBNAIL_WIDTH = 240
SCREENSHOT_QUALITY = 80

_MARKER_RADIUS = 12
"""Deliberately the same red circle-and-crosshair ``debug_log.DebugSession.save_screenshot``
draws, at the same radius and stroke width, so a chat thumbnail and a diagnostic screenshot
show the user the same marker for the same coordinate.

It is re-implemented rather than called because that method is bound to a ``DebugSession``'s
folder and gated on ``DIAGNOSTIC_CAPTURE``, so there is no way to reach the drawing without
also creating a diagnostic session the user did not ask for."""


def store_screenshots_enabled() -> bool:
    """Whether chat screenshots may be written to disk. **Defaults off.**

    Screen contents on disk is a materially bigger privacy commitment than a transcript, and
    it deserves an explicit yes rather than being inherited from having switched the HUD on
    for an unrelated reason (§10.1, decision 6).
    """
    return resolve_setting("CHAT_STORE_SCREENSHOTS", default="off").strip().lower() == "on"


def retention_days() -> int:
    """``CHAT_RETENTION_DAYS``, bounded like every other integer setting."""
    return resolve_bounded_int_setting(
        "CHAT_RETENTION_DAYS", default=DEFAULT_RETENTION_DAYS, minimum=1, maximum=365)


# --- Message model -----------------------------------------------------------

@dataclass(frozen=True)
class ChatMessage:
    """One turn in the transcript (SHELL_AND_CHAT.md §4 ``S-8``).

    ``coordinate`` is Space C -- the Nimbus declared-resolution coordinate the model returned,
    which is also the space the stored screenshot is in, so the marker can be drawn on the
    image with no transform. Re-pointing later emits it unchanged and ``app.py`` runs the same
    Space C -> physical conversion it already runs for a live answer.

    ``image`` and ``privacy_skipped`` are **not persisted and not compared**. They carry the
    pixels only as far as the main-thread ``add_message`` call, and ``privacy_skipped=True``
    is a hard stop there: a screenshot the Privacy Guard refused must never reach the disk,
    because writing it would silently undo T2-1 and the user believes they are protected
    (Invariant 6).
    """

    role: str
    text: str
    created_at: str = ""
    screenshot: str = ""
    coordinate: tuple[int, int] | None = None
    message_id: int = 0
    error: str = ""
    image: object = field(default=None, compare=False, repr=False)
    privacy_skipped: bool = field(default=False, compare=False, repr=False)


# --- Pure helpers ------------------------------------------------------------

def auto_title(text: str, limit: int = TITLE_MAX_CHARS) -> str:
    """A session title from the first user message. **No model call.**

    A title is cosmetic; spending a request and a round trip on one is not justified, and the
    first thing the user said is a better label than a generated summary anyway because it is
    what they will search for later.

    Truncates on a word boundary where one is available, so a title never ends mid-word.
    """
    cleaned = " ".join((text or "").split()).strip().rstrip("?.!,")
    if len(cleaned) <= limit:
        return cleaned
    clipped = cleaned[:limit]
    if " " in clipped[limit // 2:]:
        clipped = clipped[:clipped.rindex(" ")]
    return clipped.rstrip() + "\u2026"


def should_auto_new_session(
    previous_app: str,
    current_app: str,
    last_used_at: str,
    now: datetime | None = None,
    idle_minutes: int = 30,
) -> bool:
    """Whether a foreground-app change justifies starting a fresh session.

    Both conditions are required, and each guards against the other's failure mode. Per-app
    memory already exists, so a session spanning Excel and Photoshop is muddled context --
    but alt-tabbing to a browser for ten seconds must not fragment one conversation into
    three. Time alone is not enough either: an hour of continuous work in one app is still
    one conversation.
    """
    if not previous_app or not current_app:
        return False
    if previous_app.strip().lower() == current_app.strip().lower():
        return False
    try:
        last = datetime.fromisoformat(last_used_at)
    except (TypeError, ValueError):
        return False
    return (now or datetime.now()) - last >= timedelta(minutes=idle_minutes)


def _read_jpeg(path: Path) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def build_history(
    messages: list[ChatMessage],
    max_exchanges: int = MAX_HISTORY_EXCHANGES,
    image_count: int | None = None,
    chats_dir: Path | str | None = None,
    read_image=_read_jpeg,
) -> list[dict]:
    """Rebuild ``app._history`` from stored messages, honouring T2-4's image budget.

    Produces exactly the shape ``_pipeline_worker`` appends: ``{"role": "user"|"assistant",
    "content": [block, ...]}`` with Anthropic-form content blocks. Anything else would work
    until the first provider that actually reads history, which is the worst time to find out.

    ``system`` messages are dropped. They were never sent to the model, so replaying them
    into history would put UI copy -- "Screenshot skipped" -- into the conversation as if the
    user or Nimbus had said it.

    ``image_count`` defaults to the live ``HISTORY_IMAGE_COUNT`` (0, i.e. text only), read at
    call time rather than import time so a Settings change applies without a restart. The
    newest turns get the images: an old screenshot is actively misleading, because the user
    has moved on and the model would answer about a window that is no longer there.
    """
    if image_count is None:
        from config import HISTORY_IMAGE_COUNT
        image_count = HISTORY_IMAGE_COUNT
    base = Path(chats_dir) if chats_dir is not None else CHATS_DIR

    conversational = [m for m in messages if m.role in (ROLE_USER, ROLE_NIMBUS)]
    if max_exchanges > 0:
        conversational = conversational[-(max_exchanges * 2):]

    # Choose which screenshots earn an image block before building anything, so the budget
    # is applied newest-first regardless of where the screenshots happen to sit.
    with_images = [
        m for m in reversed(conversational)
        if m.role == ROLE_USER and m.screenshot
    ][:max(0, int(image_count))]
    chosen = {id(m) for m in with_images}

    history: list[dict] = []
    for message in conversational:
        blocks: list[dict] = [{"type": "text", "text": message.text}]
        if id(message) in chosen:
            data = read_image(base / message.screenshot)
            if data:
                from ai import history_image_block
                blocks.append(history_image_block(data))
        history.append({
            "role": "user" if message.role == ROLE_USER else "assistant",
            "content": blocks,
        })
    return history


# --- Storage -----------------------------------------------------------------

class SessionStore:
    """Chat sessions and messages, sharing ``memory.py``'s SQLite database (``S-8b``).

    Single-writer, called from the Qt main thread, exactly like ``MemoryStore`` and
    ``ReviewQueue``. Every method opens and closes its own connection so nothing is held
    across turns and a crash cannot leave a write transaction open.
    """

    def __init__(
        self,
        index_db_path: Path | str = INDEX_DB_PATH,
        chats_dir: Path | str | None = None,
        store_screenshots: bool | None = None,
    ) -> None:
        """
        Args:
            index_db_path: the shared ``~/.nimbus/index.db``.
            chats_dir: screenshot root. Defaults to a ``chats`` folder **beside the
                database**, so a test pointing the database at ``tmp_path`` cannot write
                images into the developer's real profile.
            store_screenshots: overrides ``CHAT_STORE_SCREENSHOTS``. Resolved once here
                rather than per turn: the setting is restart-gated anyway, and
                ``resolve_setting`` writes back to the keyring whenever it finds an
                environment value, which is not something to put on a per-interaction path.
        """
        self.index_db_path = Path(index_db_path)
        self.chats_dir = (
            Path(chats_dir) if chats_dir is not None
            else self.index_db_path.parent / "chats"
        )
        self.store_screenshots = (
            store_screenshots_enabled() if store_screenshots is None
            else bool(store_screenshots)
        )
        self.index_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # --- schema ---

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.index_db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Create ``chat_sessions`` and ``chat_messages``. Idempotent and purely additive.

        ``FOREIGN KEY`` is deliberately omitted. SQLite does not enforce one without
        ``PRAGMA foreign_keys=ON`` per connection, and neither ``memory.py`` nor ``review.py``
        sets it -- a constraint that looks enforced but is not is worse than none, because the
        next reader trusts it. Deletion cascades are therefore explicit in ``delete_session``,
        which also has to remove the screenshot folder that no SQL constraint could.
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    title        TEXT NOT NULL DEFAULT '',
                    app_name     TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   INTEGER NOT NULL,
                    role         TEXT NOT NULL,
                    text         TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    screenshot   TEXT NOT NULL DEFAULT '',
                    coord_x      INTEGER,
                    coord_y      INTEGER
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_session "
                "ON chat_messages(session_id, id)"
            )
            # Privacy Guard suppressions, for Home's "screenshots skipped this week".
            #
            # Here rather than in a file of its own because this database already exists, already
            # lives in the same folder, and is already pruned. The count is the most
            # trust-building number on Home precisely because it is an observation, and an
            # observation that resets when the process restarts is not much of one.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_skips (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    reason     TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_privacy_skips_created "
                "ON privacy_skips(created_at)"
            )
        finally:
            conn.close()

    # --- sessions ---

    def new_session(self, app_name: str = "", title: str = "",
                    now: datetime | None = None) -> int:
        """Create a session and return its id."""
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO chat_sessions (title, app_name, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?)",
                ((title or "").strip(), (app_name or "").strip().lower(), stamp, stamp),
            )
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def session(self, session_id: int) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def sessions(self, search: str = "", limit: int = 50) -> list[dict]:
        """Recent sessions, most recently used first, optionally filtered.

        The search is not premature. Sessions accumulate silently -- a few weeks of normal
        use is hundreds -- and a flat list stops being navigable well before that. It matches
        the title and the app name because those are the two things a user remembers about a
        conversation they are trying to find.
        """
        needle = (search or "").strip().lower()
        counted = (
            "SELECT s.*, ("
            " SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id"
            ") AS message_count FROM chat_sessions s"
        )
        order = " ORDER BY s.last_used_at DESC, s.id DESC LIMIT ?"
        conn = self._connect()
        try:
            if needle:
                term = f"%{needle}%"
                rows = conn.execute(
                    counted
                    + " WHERE LOWER(s.title) LIKE ? OR LOWER(s.app_name) LIKE ?"
                    + order,
                    (term, term, limit),
                ).fetchall()
            else:
                rows = conn.execute(counted + order, (limit,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def touch(self, session_id: int, now: datetime | None = None) -> None:
        """Bump ``last_used_at`` so ordering and retention both reflect real use."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE chat_sessions SET last_used_at = ? WHERE id = ?",
                ((now or datetime.now()).isoformat(timespec="seconds"), session_id),
            )
        finally:
            conn.close()

    def set_title(self, session_id: int, title: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE id = ?",
                ((title or "").strip(), session_id),
            )
        finally:
            conn.close()

    def delete_session(self, session_id: int) -> None:
        """Remove a session, its messages **and its screenshots**.

        There is no ``FOREIGN KEY``, so the cascade is ours to get right; and no database
        constraint could delete the image folder anyway. A "deleted" conversation that leaves
        the screen contents on disk is the one failure here that matters.
        """
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        finally:
            conn.close()
        shutil.rmtree(self.chats_dir / str(session_id), ignore_errors=True)

    def prune(self, days: int | None = None, now: datetime | None = None) -> int:
        """Delete sessions untouched for ``days``. Returns how many went.

        Best effort and never raises: this runs at startup, and a locked image file must not
        be able to stop Nimbus from launching.
        """
        keep_days = retention_days() if days is None else int(days)
        cutoff = (now or datetime.now()) - timedelta(days=keep_days)
        stamp = cutoff.isoformat(timespec="seconds")
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id FROM chat_sessions WHERE last_used_at < ?", (stamp,)
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            try:
                self.delete_session(int(row["id"]))
            except Exception:
                continue
        return len(rows)

    # --- messages ---

    def add_message(self, session_id: int, message: ChatMessage,
                    now: datetime | None = None) -> int:
        """Persist one turn, save its screenshot if allowed, and return the row id.

        Timestamps here rather than at the call site when ``created_at`` is blank, which
        removes a whole class of "forgot the timestamp" bugs from the pipeline.

        The first user message also titles the session, because that is the earliest moment
        the title is knowable and it costs nothing.
        """
        stamp = message.created_at or (now or datetime.now()).isoformat(timespec="seconds")
        coord = message.coordinate or (None, None)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, text, created_at, screenshot, coord_x, coord_y) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, message.role, message.text, stamp,
                 message.screenshot, coord[0], coord[1]),
            )
            message_id = int(cursor.lastrowid)
        finally:
            conn.close()

        if message.role == ROLE_USER:
            existing = self.session(session_id) or {}
            if not (existing.get("title") or "").strip():
                self.set_title(session_id, auto_title(message.text))
        self.touch(session_id, now=now)

        relative = self.save_screenshot(
            session_id, message_id, message.image,
            coordinate=message.coordinate,
            privacy_skipped=message.privacy_skipped,
        )
        if relative:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE chat_messages SET screenshot = ? WHERE id = ?",
                    (relative, message_id),
                )
            finally:
                conn.close()
        return message_id

    def append_delta(self, message_id: int, text: str) -> None:
        """Extend an open Nimbus turn as the reply streams in.

        An UPDATE per delta rather than one write at the end, so a crash mid-reply leaves the
        partial answer in the transcript instead of losing the turn entirely. Deltas arrive at
        sentence granularity from the TTS split, not per token, so this is a handful of small
        writes per turn rather than hundreds.
        """
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE chat_messages SET text = text || ? WHERE id = ?",
                (text, message_id),
            )
        finally:
            conn.close()

    def message(self, message_id: int) -> ChatMessage | None:
        """Re-read one turn as stored.

        The HUD calls this straight after ``add_message`` so the rendered row carries the row
        id and the screenshot path the store actually assigned -- rather than the HUD assuming
        what the store decided, which is precisely where a "screenshots are off" setting would
        get quietly ignored and a thumbnail rendered for a file that was never written.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _message_from_row(row)

    # --- Home's durable counters ---

    def record_privacy_skip(self, reason: str = "", now: datetime | None = None) -> None:
        """Note that the Privacy Guard suppressed a screenshot. Never raises.

        Called from the one capture choke point, so the number is a count of actual suppressions
        rather than an estimate assembled from log lines.
        """
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO privacy_skips (created_at, reason) VALUES (?, ?)",
                (stamp, str(reason or "")),
            )
        except sqlite3.Error:
            pass  # a status card is not worth failing an interaction for
        finally:
            conn.close()

    def _count_since(self, sql: str, since: datetime) -> int:
        conn = self._connect()
        try:
            row = conn.execute(sql, (since.isoformat(timespec="seconds"),)).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def count_privacy_skips_since(self, since: datetime) -> int:
        return self._count_since(
            "SELECT COUNT(*) FROM privacy_skips WHERE created_at >= ?", since)

    def count_questions_since(self, since: datetime) -> int:
        """Questions asked since ``since``, from the messages already stored.

        Counted from ``chat_messages`` rather than kept as its own tally, so Home's "questions this
        week" and the chat panel's transcript cannot disagree about what happened -- the same reason
        ``recent_turns`` reads from here.
        """
        return self._count_since(
            f"SELECT COUNT(*) FROM chat_messages WHERE role = '{ROLE_USER}' "
            "AND TRIM(text) <> '' AND created_at >= ?", since)

    def recent_turns(self, limit: int = 5) -> list[dict]:
        """The newest questions across **all** sessions, for Home's Recent table.

        Home used to read an in-memory list on ``NimbusApp``, so the table was empty after every
        restart even for a user with a week of conversations behind them -- reported as "it says
        empty when we clearly had a few sessions". This is the durable answer, and it is the same
        data the chat panel shows, so the two cannot disagree.

        Keys match what ``shell.pages.home`` expects: ``question``, ``app``, ``when``, ``target``.
        ``when`` is a real ``datetime`` where the stored value parses, so Home can render "2m ago";
        a stored value that does not parse is passed through as the string it is rather than
        guessed at.

        Only ``user`` rows, because a question is what the column shows. The answer is not joined
        in: pairing a question with the reply that followed it needs a correlated subquery for a
        column nobody displays.
        """
        limit = max(0, int(limit))
        if not limit:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.text AS question, m.created_at AS created_at,
                       m.coord_x AS coord_x, m.coord_y AS coord_y,
                       s.app_name AS app_name
                  FROM chat_messages m
                  JOIN chat_sessions s ON s.id = m.session_id
                 WHERE m.role = ? AND TRIM(m.text) <> ''
                 ORDER BY m.id DESC
                 LIMIT ?
                """,
                (ROLE_USER, limit),
            ).fetchall()
        except sqlite3.Error:
            # A status table is not worth taking the window down for.
            return []
        finally:
            conn.close()

        turns: list[dict] = []
        for row in rows:
            stamp: object = row["created_at"]
            try:
                stamp = datetime.fromisoformat(str(row["created_at"]))
            except (TypeError, ValueError):
                pass
            target = ""
            if row["coord_x"] is not None and row["coord_y"] is not None:
                target = f"{int(row['coord_x'])}, {int(row['coord_y'])}"
            turns.append({
                "question": row["question"] or "",
                "app": row["app_name"] or "",
                "when": stamp,
                "target": target,
            })
        return turns

    def messages(self, session_id: int) -> list[ChatMessage]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
        return [_message_from_row(row) for row in rows]

    # --- screenshots ---

    def save_screenshot(
        self,
        session_id: int,
        message_id: int,
        image,
        coordinate: tuple[int, int] | None = None,
        privacy_skipped: bool = False,
    ) -> str:
        """Write ``<session>/<message>.jpg`` plus a thumbnail. Returns a relative path or ``""``.

        Three refusals, in priority order, and the first is the one that matters:

        1. ``privacy_skipped`` -- the Privacy Guard's entire purpose is that those pixels are
           not retained. Writing them here would quietly undo T2-1, which is worse than never
           having had the guard, because the user believes they are protected (Invariant 6).
        2. ``store_screenshots`` off -- the default. Screen contents on disk is an explicit
           opt-in, not something inherited from enabling the HUD.
        3. no image, or the write fails -- returns ``""`` so the caller records a turn with no
           screenshot rather than a dangling path.

        Never raises. A thumbnail is a nicety; the transcript is the feature.
        """
        if privacy_skipped or not self.store_screenshots or image is None:
            return ""
        folder = self.chats_dir / str(session_id)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            full = image.copy()
            if coordinate:
                _draw_coordinate_marker(full, coordinate)
            full.save(str(folder / f"{message_id}.jpg"), "JPEG",
                      quality=SCREENSHOT_QUALITY)
            thumb = full.copy()
            ratio = THUMBNAIL_WIDTH / max(1, thumb.width)
            thumb = thumb.resize(
                (THUMBNAIL_WIDTH, max(1, int(thumb.height * ratio))))
            thumb.save(str(folder / f"{message_id}_thumb.jpg"), "JPEG",
                       quality=SCREENSHOT_QUALITY)
        except Exception:
            return ""
        return f"{session_id}/{message_id}.jpg"

    def screenshot_paths(self, relative: str) -> tuple[Path, Path]:
        """``(full, thumbnail)`` absolute paths for a stored screenshot."""
        full = self.chats_dir / relative
        return full, full.with_name(f"{full.stem}_thumb{full.suffix}")

    # --- the "that was wrong" flag ---

    def flag_wrong(self, message_id: int, now: datetime | None = None) -> bool:
        """Mark a Nimbus turn as wrong and pull it out of the review queue (T3-3).

        The flag has to *do* something or it is theatre. Reviewing a known-wrong answer for
        thirty days would actively teach the user the wrong thing, so the matching
        ``review_queue`` row is deleted -- and a ``system`` note goes into the transcript so
        the flag is visible next time the session is opened.

        The DELETE is plain SQL against ``review.py``'s table rather than a call into
        ``ReviewQueue``, which exposes no delete. Adding one would mean editing ``review.py``,
        and this workstream must not touch existing files (§9.1). Guarded on the table
        existing, so a database that predates the Knowledge Journal is unaffected.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT session_id, role, text FROM chat_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return False
            session_id, answer = int(row["session_id"]), row["text"]
            question = conn.execute(
                "SELECT text FROM chat_messages "
                "WHERE session_id = ? AND id < ? AND role = ? ORDER BY id DESC LIMIT 1",
                (session_id, message_id, ROLE_USER),
            ).fetchone()
            has_queue = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='review_queue'"
            ).fetchone()
            if has_queue is not None:
                if question is not None:
                    conn.execute(
                        "DELETE FROM review_queue WHERE question = ? AND answer = ?",
                        (question["text"].strip(), (answer or "").strip()),
                    )
                else:
                    conn.execute(
                        "DELETE FROM review_queue WHERE answer = ?",
                        ((answer or "").strip(),),
                    )
        finally:
            conn.close()

        self.add_message(
            session_id,
            ChatMessage(role=ROLE_SYSTEM, text="You marked that answer as wrong."),
            now=now,
        )
        return True

    # --- history rebuild ---

    def history_for_session(
        self,
        session_id: int,
        max_exchanges: int = MAX_HISTORY_EXCHANGES,
        image_count: int | None = None,
    ) -> list[dict]:
        """``app._history`` for a stored session. See ``build_history``."""
        return build_history(
            self.messages(session_id),
            max_exchanges=max_exchanges,
            image_count=image_count,
            chats_dir=self.chats_dir,
        )


# --- The two operations that must also touch ``_history`` --------------------

def start_new_session(
    store: SessionStore,
    app_name: str = "",
    history: list | None = None,
    now: datetime | None = None,
) -> int:
    """Create a session **and clear the caller's ``_history`` in place** (Invariant 7).

    Clearing is part of the same call on purpose. "New chat" that starts a fresh visual
    thread while still sending the model the last ten exchanges is a lie, and the way that
    lie happens is a caller creating the session and forgetting the clear. Making it one
    operation removes the opportunity.

    Mutates in place (``list.clear``) rather than returning a new list, because ``app.py``
    hands the same ``_history`` object to the pipeline; rebinding it here would leave the
    worker holding the old one.
    """
    if history is not None:
        history.clear()
    return store.new_session(app_name=app_name, now=now)


def switch_session(
    store: SessionStore,
    session_id: int,
    history: list | None = None,
    max_exchanges: int = MAX_HISTORY_EXCHANGES,
    image_count: int | None = None,
) -> list[dict]:
    """Load a session and rebuild ``_history`` from it, in place.

    Same in-place contract as ``start_new_session`` and for the same reason. Returns the
    rebuilt history as well, so a caller with no list of its own can still use it.
    """
    rebuilt = store.history_for_session(
        session_id, max_exchanges=max_exchanges, image_count=image_count)
    if history is not None:
        history[:] = rebuilt
    store.touch(session_id)
    return rebuilt


def _message_from_row(row) -> ChatMessage:
    """A ``sqlite3.Row`` -> ``ChatMessage``.

    Both coordinate columns must be present for a coordinate to exist. A half-null pair is
    treated as no coordinate rather than as ``(x, 0)``, because a re-point to a fabricated
    coordinate would fly the cursor somewhere the model never suggested.
    """
    has_coord = row["coord_x"] is not None and row["coord_y"] is not None
    return ChatMessage(
        role=row["role"],
        text=row["text"],
        created_at=row["created_at"],
        screenshot=row["screenshot"],
        coordinate=(int(row["coord_x"]), int(row["coord_y"])) if has_coord else None,
        message_id=int(row["id"]),
    )


def _draw_coordinate_marker(image, coordinate: tuple[int, int]) -> None:
    """Draw Nimbus's pointer target on a screenshot, in place. See ``_MARKER_RADIUS``."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    x, y = coordinate
    r = _MARKER_RADIUS
    draw.ellipse([(x - r, y - r), (x + r, y + r)], outline="red", width=3)
    draw.line([(x - r, y), (x + r, y)], fill="red", width=2)
    draw.line([(x, y - r), (x, y + r)], fill="red", width=2)
