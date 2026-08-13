"""Unit tests for sessions.py — the chat session store and history rebuild.

No Qt anywhere in this file. The store and every pure helper are deliberately Qt-free so the
subtle behaviour — the ten-exchange window, the image budget, the privacy refusal — is tested
fast and without a QApplication, the same separation review.py uses.

Imports live inside the test functions, per IMPROVEMENTS.md §1.4.
"""

import pytest


@pytest.fixture
def store(tmp_path):
    """A store pointed entirely at tmp_path.

    ``chats_dir`` defaults to a folder beside the database, so pointing the database at
    ``tmp_path`` is enough to keep test screenshots out of the developer's real profile. The
    explicit ``store_screenshots`` avoids the test inheriting whatever the machine's keyring
    happens to say.
    """
    from sessions import SessionStore
    return SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=False)


def _image(width=64, height=48, colour=(90, 120, 200)):
    from PIL import Image
    return Image.new("RGB", (width, height), colour)


class TestSchema:
    def test_schema_created_idempotently(self, tmp_path):
        from sessions import SessionStore

        path = tmp_path / "index.db"
        SessionStore(index_db_path=path, store_screenshots=False).new_session("excel.exe")
        again = SessionStore(index_db_path=path, store_screenshots=False)

        assert len(again.sessions()) == 1

    def test_wal_is_enabled(self, store):
        """Three writers share this database; WAL is what makes that safe."""
        conn = store._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal"

    def test_message_index_exists(self, store):
        conn = store._connect()
        try:
            names = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'").fetchall()
            }
        finally:
            conn.close()
        assert "idx_chat_messages_session" in names

    def test_existing_memory_and_review_tables_untouched(self, tmp_path):
        """Backward-compat gate. Users have live databases with apps + review_queue."""
        from memory import MemoryStore
        from review import ReviewQueue
        from sessions import ChatMessage, SessionStore

        db = tmp_path / "index.db"
        memory = MemoryStore(memory_dir=tmp_path / "mem", index_db_path=db)
        memory.record("excel.exe", "Book1", "hi", "hello", [])
        queue = ReviewQueue(index_db_path=db)
        queue.add("excel.exe", "where is export", "top right")
        apps_before = memory.list_known_apps()
        review_before = queue.stats()

        store = SessionStore(index_db_path=db, store_screenshots=False)
        session_id = store.new_session("excel.exe")
        store.add_message(session_id, ChatMessage(role="user", text="anything"))

        assert memory.list_known_apps() == apps_before
        assert queue.stats() == review_before

    def test_tables_are_additive_only(self, tmp_path):
        from sessions import SessionStore

        db = tmp_path / "index.db"
        SessionStore(index_db_path=db, store_screenshots=False)
        store = SessionStore(index_db_path=db, store_screenshots=False)
        conn = store._connect()
        try:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        finally:
            conn.close()
        assert {"chat_sessions", "chat_messages"} <= tables


class TestMessages:
    def test_message_roundtrips_with_its_coordinate(self, store):
        from sessions import ChatMessage

        session_id = store.new_session("excel.exe")
        store.add_message(session_id, ChatMessage(role="user", text="where is export"))
        store.add_message(session_id, ChatMessage(
            role="nimbus", text="top right", coordinate=(400, 120)))

        messages = store.messages(session_id)
        assert [m.role for m in messages] == ["user", "nimbus"]
        assert messages[1].coordinate == (400, 120)

    def test_missing_coordinate_stays_none(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="nimbus", text="no target"))
        assert store.messages(session_id)[0].coordinate is None

    def test_created_at_is_stamped_when_blank(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="hi"))
        assert store.messages(session_id)[0].created_at

    def test_system_role_is_stored(self, store):
        """A privacy-suppressed turn must be explainable, or it looks like a malfunction."""
        from sessions import ROLE_SYSTEM, ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(
            role=ROLE_SYSTEM, text="Screenshot skipped — a password manager was open"))
        assert store.messages(session_id)[0].role == ROLE_SYSTEM

    def test_append_delta_extends_the_open_turn(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        message_id = store.add_message(session_id, ChatMessage(role="nimbus", text="it's "))
        store.append_delta(message_id, "top ")
        store.append_delta(message_id, "right.")
        assert store.messages(session_id)[0].text == "it's top right."

    def test_message_reads_back_a_single_row(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        message_id = store.add_message(session_id, ChatMessage(role="user", text="hello"))
        assert store.message(message_id).text == "hello"
        assert store.message(message_id + 999) is None


class TestTitles:
    def test_auto_title_from_first_user_message(self, store):
        from sessions import ChatMessage

        session_id = store.new_session("excel.exe")
        store.add_message(session_id, ChatMessage(
            role="user", text="where is the export button?"))
        assert store.session(session_id)["title"] == "where is the export button"

    def test_second_message_does_not_retitle(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="first question"))
        store.add_message(session_id, ChatMessage(role="user", text="second question"))
        assert store.session(session_id)["title"] == "first question"

    def test_auto_title_truncates_on_a_word_boundary(self):
        from sessions import auto_title

        title = auto_title("where exactly is the export button hiding in this ribbon", 30)
        assert title.endswith("\u2026")
        assert len(title) <= 31
        assert not title.rstrip("\u2026").endswith(" ")

    def test_auto_title_makes_no_api_call(self, mocker):
        """A title is cosmetic; spending a request on it is not justified."""
        import ai
        from sessions import auto_title

        create = mocker.patch.object(ai, "create_ai_client")
        assert auto_title("what is a pivot table?") == "what is a pivot table"
        create.assert_not_called()

    def test_nimbus_turn_never_titles_the_session(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="nimbus", text="it's top right"))
        assert store.session(session_id)["title"] == ""


class TestScreenshots:
    def test_screenshots_disabled_by_default(self, tmp_path, mocker):
        """CHAT_STORE_SCREENSHOTS is an explicit opt-in, not inherited from enabling the HUD."""
        import sessions

        mocker.patch.object(sessions, "resolve_setting", lambda name, default: default)
        store = sessions.SessionStore(index_db_path=tmp_path / "index.db")

        assert store.store_screenshots is False
        session_id = store.new_session()
        message_id = store.add_message(session_id, sessions.ChatMessage(
            role="user", text="what is this", image=_image()))
        assert store.message(message_id).screenshot == ""
        assert not (store.chats_dir).exists()

    def test_privacy_suppressed_turn_stores_no_screenshot(self, tmp_path):
        """Invariant 6 — the one that would silently undo T2-1."""
        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        message_id = store.add_message(session_id, ChatMessage(
            role="user", text="what is this", image=_image(), privacy_skipped=True))

        assert store.message(message_id).screenshot == ""
        assert list(store.chats_dir.glob("**/*.jpg")) == []

    def test_privacy_refusal_outranks_the_enabled_setting(self, tmp_path):
        """Order matters: the guard must win even when the user opted into storage."""
        from sessions import SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        assert store.save_screenshot(1, 1, _image(), privacy_skipped=True) == ""

    def test_enabled_storage_writes_a_full_image_and_a_thumbnail(self, tmp_path):
        from sessions import THUMBNAIL_WIDTH, ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        message_id = store.add_message(session_id, ChatMessage(
            role="user", text="what is this", image=_image(800, 600), coordinate=(100, 90)))

        relative = store.message(message_id).screenshot
        assert relative == f"{session_id}/{message_id}.jpg"
        full, thumb = store.screenshot_paths(relative)
        assert full.exists() and thumb.exists()

        from PIL import Image
        assert Image.open(thumb).width == THUMBNAIL_WIDTH

    def test_a_broken_image_does_not_fail_the_turn(self, tmp_path):
        """A screenshot is a nicety; the transcript is the feature."""
        from sessions import ChatMessage, SessionStore

        class Explodes:
            def copy(self):
                raise OSError("decoder gone")

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        message_id = store.add_message(session_id, ChatMessage(
            role="user", text="still recorded", image=Explodes()))

        assert store.message(message_id).text == "still recorded"
        assert store.message(message_id).screenshot == ""


class TestDeletionAndRetention:
    def test_deleting_a_session_removes_its_screenshots(self, tmp_path):
        """No FOREIGN KEY, so the cascade is ours to get right."""
        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(
            role="user", text="q", image=_image()))
        folder = store.chats_dir / str(session_id)
        assert folder.exists()

        store.delete_session(session_id)

        assert store.session(session_id) is None
        assert store.messages(session_id) == []
        assert not folder.exists()

    def test_retention_prunes_old_sessions(self, tmp_path):
        from datetime import datetime, timedelta

        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        long_ago = datetime.now() - timedelta(days=40)
        stale = store.new_session("old.exe", now=long_ago)
        store.add_message(stale, ChatMessage(
            role="user", text="old", image=_image()), now=long_ago)
        fresh = store.new_session("new.exe")

        assert store.prune(days=14) == 1
        assert store.session(stale) is None
        assert store.session(fresh) is not None
        assert not (store.chats_dir / str(stale)).exists()

    def test_prune_uses_last_used_not_created(self, tmp_path):
        """A months-old conversation the user still returns to is not stale."""
        from datetime import datetime, timedelta

        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=False)
        long_ago = datetime.now() - timedelta(days=90)
        session_id = store.new_session("old.exe", now=long_ago)
        store.add_message(session_id, ChatMessage(role="user", text="revisited"))

        assert store.prune(days=14) == 0

    def test_prune_default_reads_the_setting(self, tmp_path, mocker):
        import sessions

        resolver = mocker.patch.object(
            sessions, "retention_days", return_value=1)
        sessions.SessionStore(
            index_db_path=tmp_path / "index.db", store_screenshots=False).prune()
        resolver.assert_called_once()


class TestSessionListing:
    def test_sessions_are_most_recently_used_first(self, store):
        from datetime import datetime, timedelta

        older = store.new_session("a.exe", title="older",
                                  now=datetime.now() - timedelta(hours=2))
        newer = store.new_session("b.exe", title="newer")
        assert [row["id"] for row in store.sessions()] == [newer, older]

    def test_search_matches_title_and_app(self, store):
        store.new_session("excel.exe", title="pivot tables")
        store.new_session("photoshop.exe", title="layer masks")

        assert [row["title"] for row in store.sessions(search="pivot")] == ["pivot tables"]
        assert [row["title"] for row in store.sessions(search="photoshop")] == ["layer masks"]
        assert len(store.sessions(search="")) == 2

    def test_listing_reports_message_counts(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="one"))
        store.add_message(session_id, ChatMessage(role="nimbus", text="two"))
        assert store.sessions()[0]["message_count"] == 2


class TestWrongFlag:
    def test_wrong_flag_excludes_the_turn_from_the_review_queue(self, tmp_path):
        """T3-3 interaction: reviewing a known-wrong answer teaches the wrong thing."""
        from datetime import date, timedelta

        from review import ReviewQueue
        from sessions import ChatMessage, SessionStore

        db = tmp_path / "index.db"
        queue = ReviewQueue(index_db_path=db)
        queue.add("excel.exe", "where is export", "top right, next to Share")
        due_date = date.today() + timedelta(days=2)
        assert len(queue.due(today=due_date)) == 1

        store = SessionStore(index_db_path=db, store_screenshots=False)
        session_id = store.new_session("excel.exe")
        store.add_message(session_id, ChatMessage(role="user", text="where is export"))
        answer_id = store.add_message(session_id, ChatMessage(
            role="nimbus", text="top right, next to Share"))

        assert store.flag_wrong(answer_id) is True
        assert queue.due(today=due_date) == []

    def test_wrong_flag_leaves_other_review_items_alone(self, tmp_path):
        from datetime import date, timedelta

        from review import ReviewQueue
        from sessions import ChatMessage, SessionStore

        db = tmp_path / "index.db"
        queue = ReviewQueue(index_db_path=db)
        queue.add("excel.exe", "where is export", "top right")
        queue.add("excel.exe", "what is a pivot", "a summary table")

        store = SessionStore(index_db_path=db, store_screenshots=False)
        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="where is export"))
        answer_id = store.add_message(session_id, ChatMessage(
            role="nimbus", text="top right"))
        store.flag_wrong(answer_id)

        remaining = queue.due(today=date.today() + timedelta(days=2))
        assert [item["question"] for item in remaining] == ["what is a pivot"]

    def test_wrong_flag_adds_a_visible_system_note(self, tmp_path):
        from sessions import ROLE_SYSTEM, ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=False)
        session_id = store.new_session()
        answer_id = store.add_message(session_id, ChatMessage(role="nimbus", text="wrong"))
        store.flag_wrong(answer_id)

        assert store.messages(session_id)[-1].role == ROLE_SYSTEM

    def test_wrong_flag_survives_a_database_with_no_review_queue(self, tmp_path):
        """A database predating the Knowledge Journal must not raise."""
        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=False)
        session_id = store.new_session()
        answer_id = store.add_message(session_id, ChatMessage(role="nimbus", text="wrong"))
        assert store.flag_wrong(answer_id) is True

    def test_flagging_an_unknown_message_is_false(self, store):
        assert store.flag_wrong(4242) is False


class TestHistoryRebuild:
    def test_history_window_matches_the_app_constant(self):
        """sessions.MAX_HISTORY_EXCHANGES is a copy; this is the pin that stops it drifting."""
        from app import _MAX_HISTORY_EXCHANGES
        from sessions import MAX_HISTORY_EXCHANGES

        assert MAX_HISTORY_EXCHANGES == _MAX_HISTORY_EXCHANGES

    def test_history_shape_matches_the_pipeline(self, store):
        from sessions import ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="q"))
        store.add_message(session_id, ChatMessage(role="nimbus", text="a"))

        history = store.history_for_session(session_id, image_count=0)
        assert history == [
            {"role": "user", "content": [{"type": "text", "text": "q"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
        ]

    def test_switching_session_rebuilds_history_within_max_exchanges(self, store):
        from sessions import MAX_HISTORY_EXCHANGES, ChatMessage, switch_session

        session_id = store.new_session()
        for i in range(15):
            store.add_message(session_id, ChatMessage(role="user", text=f"q{i}"))
            store.add_message(session_id, ChatMessage(role="nimbus", text=f"a{i}"))

        history = ["stale"]
        switch_session(store, session_id, history, image_count=0)

        assert len(history) == MAX_HISTORY_EXCHANGES * 2
        assert history[0]["content"][0]["text"] == "q5"
        assert "stale" not in history

    def test_system_messages_never_enter_history(self, store):
        """They were never sent to the model; replaying UI copy as speech would be wrong."""
        from sessions import ROLE_SYSTEM, ChatMessage

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="q"))
        store.add_message(session_id, ChatMessage(
            role=ROLE_SYSTEM, text="Screenshot skipped"))
        store.add_message(session_id, ChatMessage(role="nimbus", text="a"))

        history = store.history_for_session(session_id, image_count=0)
        assert [turn["role"] for turn in history] == ["user", "assistant"]

    def test_switching_session_honours_history_image_count(self, tmp_path):
        """T2-4 interaction."""
        from sessions import ChatMessage, SessionStore

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        for i in range(3):
            store.add_message(session_id, ChatMessage(
                role="user", text=f"q{i}", image=_image()))
            store.add_message(session_id, ChatMessage(role="nimbus", text=f"a{i}"))

        def image_blocks(count):
            history = store.history_for_session(session_id, image_count=count)
            return sum(
                1 for turn in history for block in turn["content"]
                if block.get("type") == "image"
            )

        assert image_blocks(0) == 0
        assert image_blocks(1) == 1
        assert image_blocks(3) == 3

    def test_image_budget_prefers_the_newest_turns(self, tmp_path):
        """A stale screenshot invites an answer about a window that is no longer there."""
        from sessions import ChatMessage, SessionStore, build_history

        store = SessionStore(index_db_path=tmp_path / "index.db", store_screenshots=True)
        session_id = store.new_session()
        for i in range(3):
            store.add_message(session_id, ChatMessage(
                role="user", text=f"q{i}", image=_image()))

        messages = store.messages(session_id)
        history = build_history(
            messages, image_count=1, chats_dir=store.chats_dir)
        with_image = [
            turn for turn in history
            if any(block.get("type") == "image" for block in turn["content"])
        ]
        assert [turn["content"][0]["text"] for turn in with_image] == ["q2"]

    def test_history_image_count_defaults_to_the_live_setting(self, mocker):
        from sessions import ROLE_USER, ChatMessage, build_history

        mocker.patch("config.HISTORY_IMAGE_COUNT", 0)
        history = build_history(
            [ChatMessage(role=ROLE_USER, text="q", screenshot="1/1.jpg")])
        assert history[0]["content"] == [{"type": "text", "text": "q"}]

    def test_a_missing_screenshot_file_degrades_to_text(self, store):
        from sessions import ROLE_USER, ChatMessage, build_history

        history = build_history(
            [ChatMessage(role=ROLE_USER, text="q", screenshot="9/9.jpg")],
            image_count=1, chats_dir=store.chats_dir)
        assert history[0]["content"] == [{"type": "text", "text": "q"}]


class TestNewSessionClearsHistory:
    def test_new_session_clears_history(self, store):
        """Invariant 7 — 'zero context' must be true."""
        from sessions import start_new_session

        history = [{"role": "user", "content": [{"type": "text", "text": "old"}]}]
        session_id = start_new_session(store, "excel.exe", history)

        assert history == []
        assert store.session(session_id) is not None

    def test_new_session_clears_in_place_not_by_rebinding(self, store):
        """app.py hands the same list to the worker; rebinding would leave it holding the old."""
        from sessions import start_new_session

        history = [{"role": "user", "content": []}]
        same_object = history
        start_new_session(store, "excel.exe", history)
        assert same_object is history and same_object == []

    def test_new_session_without_a_history_list_still_works(self, store):
        from sessions import start_new_session
        assert start_new_session(store) > 0

    def test_switch_session_rebuilds_in_place(self, store):
        from sessions import ChatMessage, switch_session

        session_id = store.new_session()
        store.add_message(session_id, ChatMessage(role="user", text="q"))
        history = [{"role": "user", "content": []}]
        same_object = history
        switch_session(store, session_id, history, image_count=0)

        assert same_object is history
        assert history[0]["content"][0]["text"] == "q"

    def test_switch_session_touches_last_used(self, store):
        from sessions import switch_session

        session_id = store.new_session()
        before = store.session(session_id)["last_used_at"]
        switch_session(store, session_id, image_count=0)
        assert store.session(session_id)["last_used_at"] >= before


class TestAutoNewSession:
    def test_app_change_after_idle_starts_a_session(self):
        from datetime import datetime, timedelta

        from sessions import should_auto_new_session

        now = datetime(2025, 1, 1, 12, 0, 0)
        last = (now - timedelta(minutes=45)).isoformat()
        assert should_auto_new_session("excel.exe", "photoshop.exe", last, now) is True

    def test_a_brief_alt_tab_does_not_fragment_a_conversation(self):
        from datetime import datetime, timedelta

        from sessions import should_auto_new_session

        now = datetime(2025, 1, 1, 12, 0, 0)
        last = (now - timedelta(seconds=10)).isoformat()
        assert should_auto_new_session("excel.exe", "chrome.exe", last, now) is False

    def test_same_app_never_starts_a_session_however_long(self):
        from datetime import datetime, timedelta

        from sessions import should_auto_new_session

        now = datetime(2025, 1, 1, 12, 0, 0)
        last = (now - timedelta(days=3)).isoformat()
        assert should_auto_new_session("excel.exe", "EXCEL.EXE", last, now) is False

    def test_unparseable_timestamp_is_conservative(self):
        from sessions import should_auto_new_session
        assert should_auto_new_session("a.exe", "b.exe", "not-a-date") is False

    def test_unknown_app_is_conservative(self):
        """Foreground detection fails transiently; that must not fragment a conversation."""
        from sessions import should_auto_new_session
        assert should_auto_new_session("", "b.exe", "2025-01-01T12:00:00") is False


class TestSettings:
    def test_store_screenshots_defaults_off(self, mocker):
        import sessions

        mocker.patch.object(sessions, "resolve_setting", lambda name, default: default)
        assert sessions.store_screenshots_enabled() is False

    def test_store_screenshots_honours_on(self, mocker):
        import sessions

        mocker.patch.object(sessions, "resolve_setting", lambda name, default: "ON")
        assert sessions.store_screenshots_enabled() is True

    def test_retention_days_defaults_to_fourteen(self, mocker):
        import sessions

        mocker.patch.object(
            sessions, "resolve_bounded_int_setting",
            lambda name, default, minimum, maximum: default)
        assert sessions.retention_days() == sessions.DEFAULT_RETENTION_DAYS

    def test_chats_dir_follows_the_database(self, tmp_path):
        """Otherwise a test with a tmp database still writes images to the real profile."""
        from sessions import SessionStore

        store = SessionStore(index_db_path=tmp_path / "nested" / "index.db",
                             store_screenshots=False)
        assert store.chats_dir == tmp_path / "nested" / "chats"
