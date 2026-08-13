"""Tests for the Knowledge Journal (T3-3).

Three areas, in descending order of how badly a bug would hurt:

1. **Backward compatibility.** Users have live SQLite databases holding their memory. Adding
   a table must not disturb the existing `apps` data in any way. That is the gate.
2. **Intent matching false positives.** A command that hijacks a genuine question silently
   replaces a real answer with a quiz. Worse than the feature not existing.
3. **Scheduling maths.** Pure functions, so exhaustively testable -- and an unbounded
   interval or a zero-day interval both make the feature useless in opposite directions.
"""

from datetime import date, timedelta

import pytest


@pytest.fixture
def queue(tmp_path):
    from review import ReviewQueue
    return ReviewQueue(index_db_path=tmp_path / "index.db")


class TestSM2Scheduler:
    """Pure scheduling math — no I/O, no clock."""

    def test_first_correct_answer_advances_one_rung(self):
        from review import next_interval_index
        assert next_interval_index(0, correct=True) == 1

    def test_consecutive_correct_advances_interval(self):
        from review import next_interval_index
        index = 0
        for expected in (1, 2, 3, 4):
            index = next_interval_index(index, correct=True)
            assert index == expected

    def test_incorrect_answer_resets_interval(self):
        """Resets to the start rather than stepping back one rung: an item the user does
        not know should come back tomorrow, not in a week."""
        from review import next_interval_index
        assert next_interval_index(5, correct=False) == 0

    def test_interval_never_exceeds_maximum(self):
        from review import INTERVALS_DAYS, next_interval_index
        index = 0
        for _ in range(50):
            index = next_interval_index(index, correct=True)
        assert index == len(INTERVALS_DAYS) - 1

    def test_ease_factor_bounded(self):
        from review import MAX_EASE, MIN_EASE, adjust_ease
        ease = 2.5
        for _ in range(100):
            ease = adjust_ease(ease, correct=True)
        assert ease <= MAX_EASE
        for _ in range(100):
            ease = adjust_ease(ease, correct=False)
        assert ease >= MIN_EASE

    def test_failure_moves_ease_more_than_success(self):
        """Getting something right once is weak evidence; getting it wrong is strong."""
        from review import adjust_ease
        up = adjust_ease(2.0, correct=True) - 2.0
        down = 2.0 - adjust_ease(2.0, correct=False)
        assert down > up

    def test_days_never_zero(self):
        """A zero-day interval would re-ask the same item in the same session."""
        from review import MIN_EASE, next_interval_days
        for index in range(-3, 12):
            assert next_interval_days(index, MIN_EASE) >= 1

    def test_days_capped_at_the_ladder_maximum(self):
        """Software changes; an item from a year ago may describe a UI that is gone."""
        from review import INTERVALS_DAYS, MAX_EASE, next_interval_days
        assert next_interval_days(99, MAX_EASE) <= INTERVALS_DAYS[-1]

    def test_higher_ease_stretches_the_interval(self):
        from review import next_interval_days
        assert next_interval_days(3, 2.8) > next_interval_days(3, 1.3)

    def test_schedule_returns_consistent_triple(self):
        from review import schedule
        index, ease, days = schedule(2, 2.5, correct=True)
        assert index == 3 and ease > 2.5 and days >= 1

    def test_schedule_resets_on_failure(self):
        from review import schedule
        index, ease, days = schedule(5, 2.5, correct=False)
        assert index == 0 and ease < 2.5 and days >= 1


class TestReviewIntent:
    @pytest.mark.parametrize("transcript,expected", [
        ("quiz me", "quiz"),
        ("test me", "quiz"),
        ("Quiz me!", "quiz"),
        ("what should i review", "due"),
        ("what's due", "due"),
        ("what did we cover today", "recap"),
        ("recap", "recap"),
        ("what did i learn", "recap"),
    ])
    def test_intent_matching(self, transcript, expected):
        from review import classify_review_intent
        assert classify_review_intent(transcript) == expected

    @pytest.mark.parametrize("transcript", [
        "what is a pivot table",
        "where is the save button",
        "how do i export this video",
        # The dangerous near-misses: they contain a command phrase but are questions.
        "how would you quiz me on this spreadsheet formula",
        "can you explain what a review process is in git",
        "what did we cover in the meeting about the quarterly budget review",
    ])
    def test_real_questions_are_not_hijacked(self, transcript):
        """A false positive silently replaces a genuine answer with a quiz -- worse than the
        feature not existing at all."""
        from review import classify_review_intent
        assert classify_review_intent(transcript) is None

    @pytest.mark.parametrize("transcript", ["", "   ", None])
    def test_empty_input_is_not_an_intent(self, transcript):
        from review import classify_review_intent
        assert classify_review_intent(transcript) is None

    def test_longest_phrase_wins(self):
        """'what should i review' must not be shadowed by a bare 'review'."""
        from review import classify_review_intent
        assert classify_review_intent("what should i review") == "due"

    def test_word_limit_is_the_guard(self):
        from review import _MAX_INTENT_WORDS, classify_review_intent
        long_command = "quiz me " + "x " * _MAX_INTENT_WORDS
        assert classify_review_intent(long_command) is None


class TestReviewQueue:
    def test_add_returns_an_id(self, queue):
        assert queue.add("excel.exe", "what is a pivot table", "it summarises data") == 1

    def test_added_item_is_not_due_today(self, queue):
        """First review is tomorrow; showing it immediately teaches nothing."""
        queue.add("excel.exe", "q", "a")
        assert queue.due(today=date.today()) == []

    def test_due_items_returned_in_order(self, queue):
        today = date.today()
        queue.add("a.exe", "first", "a", today=today - timedelta(days=10))
        queue.add("a.exe", "second", "a", today=today - timedelta(days=5))
        due = queue.due(today=today)
        assert [d["question"] for d in due] == ["first", "second"]

    def test_future_items_not_due(self, queue):
        queue.add("a.exe", "q", "a", today=date.today() + timedelta(days=30))
        assert queue.due() == []

    def test_due_respects_limit(self, queue):
        past = date.today() - timedelta(days=10)
        for i in range(8):
            queue.add("a.exe", f"q{i}", "a", today=past)
        assert len(queue.due(limit=3)) == 3

    @pytest.mark.parametrize("question,answer", [
        ("", "a"), ("q", ""), ("   ", "a"), (None, "a"), ("q", None),
    ])
    def test_empty_content_is_skipped_not_raised(self, queue, question, answer):
        """Called at the end of a successful turn; it must never be able to spoil one."""
        assert queue.add("a.exe", question, answer) is None

    def test_target_label_is_stored(self, queue):
        """What makes an item positional -- "show me where X is" graded against a real
        grounding call. The whole differentiator over a flashcard app."""
        queue.add("a.exe", "q", "a", target_label="export button",
                  today=date.today() - timedelta(days=5))
        assert queue.due()[0]["target_label"] == "export button"

    def test_grade_correct_pushes_review_further_out(self, queue):
        past = date.today() - timedelta(days=5)
        item_id = queue.add("a.exe", "q", "a", today=past)
        before = queue.due()[0]["next_review"]
        after = queue.grade(item_id, correct=True)
        assert after["next_review"] > before
        assert after["times_correct"] == 1

    def test_grade_incorrect_resets_the_ladder(self, queue):
        past = date.today() - timedelta(days=40)
        item_id = queue.add("a.exe", "q", "a", today=past)
        queue.grade(item_id, correct=True)
        queue.grade(item_id, correct=True)
        wrong = queue.grade(item_id, correct=False)
        assert wrong["interval_index"] == 0
        assert wrong["times_wrong"] == 1

    def test_grade_unknown_id_returns_none(self, queue):
        assert queue.grade(9999, correct=True) is None

    def test_recap_returns_todays_items(self, queue):
        queue.add("a.exe", "learned today", "a")
        queue.add("a.exe", "learned last week", "a",
                  today=date.today() - timedelta(days=7))
        recap = queue.recap()
        assert [r["question"] for r in recap] == ["learned today"]

    def test_recap_can_filter_by_app(self, queue):
        queue.add("excel.exe", "excel thing", "a")
        queue.add("code.exe", "code thing", "a")
        assert [r["question"] for r in queue.recap(app_name="excel.exe")] == [
            "excel thing"]

    def test_app_name_is_normalised(self, queue):
        queue.add("Excel.EXE", "q", "a")
        assert queue.recap(app_name="excel.exe")

    def test_stats_counts_grades(self, queue):
        past = date.today() - timedelta(days=5)
        first = queue.add("a.exe", "q1", "a", today=past)
        second = queue.add("a.exe", "q2", "a", today=past)
        queue.grade(first, correct=True)
        queue.grade(second, correct=False)
        stats = queue.stats()
        assert stats == {"total": 2, "correct": 1, "wrong": 1}

    def test_schema_created_idempotently(self, tmp_path):
        """Matches memory.py's CREATE TABLE IF NOT EXISTS contract."""
        from review import ReviewQueue
        path = tmp_path / "index.db"
        ReviewQueue(index_db_path=path).add("a.exe", "q", "a")
        again = ReviewQueue(index_db_path=path)
        assert again.stats()["total"] == 1


class TestBackwardCompatibility:
    """THE gate for this item. Users have live databases full of their memory."""

    def test_existing_apps_table_untouched(self, tmp_path):
        """Adding a table must not disturb existing memory data."""
        from memory import MemoryStore
        from review import ReviewQueue

        db = tmp_path / "index.db"
        store = MemoryStore(memory_dir=tmp_path / "mem", index_db_path=db)
        store.record("excel.exe", "Book1", "hi", "hello", [])
        before = store.list_known_apps()

        ReviewQueue(index_db_path=db).add("excel.exe", "q", "a")

        assert store.list_known_apps() == before
        assert store.recall("excel.exe")

    def test_memory_still_works_after_journal_writes(self, tmp_path):
        from memory import MemoryStore
        from review import ReviewQueue

        db = tmp_path / "index.db"
        store = MemoryStore(memory_dir=tmp_path / "mem", index_db_path=db)
        queue = ReviewQueue(index_db_path=db)
        for i in range(5):
            store.record("a.exe", "t", f"q{i}", f"a{i}", [])
            queue.add("a.exe", f"q{i}", f"a{i}")
        assert "q4" in store.recall("a.exe")
        assert queue.stats()["total"] == 5

    def test_journal_works_on_a_database_memory_created_first(self, tmp_path):
        """Real upgrade path: an existing user's DB has `apps` but no `review_queue`."""
        from memory import MemoryStore
        from review import ReviewQueue

        db = tmp_path / "index.db"
        MemoryStore(memory_dir=tmp_path / "mem", index_db_path=db).record(
            "a.exe", "t", "q", "a", [])
        assert ReviewQueue(index_db_path=db).add("a.exe", "q", "a") == 1


class TestSpeechFormatting:
    def test_empty_recap_has_a_spoken_fallback(self):
        from review import format_recap_for_speech
        assert "haven't covered" in format_recap_for_speech([])

    @pytest.mark.parametrize("count", [1, 2, 3, 7])
    def test_recap_reads_as_a_sentence(self, count):
        from review import format_recap_for_speech
        items = [{"question": f"topic {i}?"} for i in range(count)]
        spoken = format_recap_for_speech(items)
        assert spoken.endswith(".")
        assert "?" not in spoken, "question marks read oddly mid-sentence"

    def test_recap_caps_at_three_topics(self):
        """A spoken list longer than three is unfollowable. A speech constraint, not a data
        one."""
        from review import format_recap_for_speech
        items = [{"question": f"topic{i}"} for i in range(9)]
        spoken = format_recap_for_speech(items)
        assert "topic3" not in spoken

    def test_no_markdown_or_list_syntax(self):
        """Everything produced here is read aloud."""
        from review import format_recap_for_speech
        spoken = format_recap_for_speech([{"question": "a"}, {"question": "b"}])
        for char in ("*", "#", "-", "\n", "`"):
            assert char not in spoken


class TestInsights:
    def test_writes_a_readable_markdown_file(self, tmp_path):
        from review import write_insights
        path = write_insights(
            tmp_path / "insights.md", {"total": 5, "correct": 3, "wrong": 1}, 2)
        text = path.read_text(encoding="utf-8")
        assert "# Nimbus learning insights" in text
        assert "**5**" in text

    def test_accuracy_handles_no_grades(self, tmp_path):
        """Division by zero on a brand-new journal."""
        from review import write_insights
        text = write_insights(
            tmp_path / "i.md", {"total": 1, "correct": 0, "wrong": 0}, 0
        ).read_text(encoding="utf-8")
        assert "not yet reviewed" in text

    def test_creates_parent_directory(self, tmp_path):
        from review import write_insights
        path = write_insights(
            tmp_path / "nested" / "deep" / "i.md",
            {"total": 0, "correct": 0, "wrong": 0}, 0)
        assert path.exists()

    def test_insights_path_is_configured_and_now_used(self, first_run_config):
        """config.INSIGHTS_PATH was defined and written by nothing before T3-3."""
        assert str(first_run_config.INSIGHTS_PATH).endswith("insights.md")


class TestDefaults:
    def test_journal_defaults_on(self, first_run_config):
        assert first_run_config.KNOWLEDGE_JOURNAL == "on"

    def test_enabled_flag_cached_at_import(self):
        import app
        assert isinstance(app.JOURNAL_ENABLED, bool)


class TestAppIntegration:
    def _app(self, journal, enabled=True):
        from app import NimbusApp
        instance = NimbusApp.__new__(NimbusApp)
        instance._journal_store = journal
        return instance

    def _queue(self, tmp_path):
        from review import ReviewQueue
        return ReviewQueue(index_db_path=tmp_path / "index.db")

    def test_normal_question_falls_through_to_the_pipeline(self, tmp_path, mocker):
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)
        instance = self._app(self._queue(tmp_path))
        assert instance._handle_journal_intent(
            "where is the save button", "a.exe") is None

    def test_recap_is_answered_locally(self, tmp_path, mocker):
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)
        queue = self._queue(tmp_path)
        queue.add("a.exe", "pivot tables", "they summarise data")
        instance = self._app(queue)
        reply = instance._handle_journal_intent("what did we cover today", "a.exe")
        assert reply is not None and "pivot tables" in reply

    def test_quiz_with_nothing_due_says_so(self, tmp_path, mocker):
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)
        instance = self._app(self._queue(tmp_path))
        reply = instance._handle_journal_intent("quiz me", "a.exe")
        assert reply is not None and "nothing's due" in reply

    def test_quiz_uses_the_positional_form_when_a_target_exists(self, tmp_path, mocker):
        """The screen-aware question no flashcard app can ask."""
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)
        queue = self._queue(tmp_path)
        queue.add("a.exe", "q", "a", target_label="export button",
                  today=date.today() - timedelta(days=5))
        instance = self._app(queue)
        reply = instance._handle_journal_intent("quiz me", "a.exe")
        assert "show me where the export button is" in reply

    def test_disabled_journal_answers_nothing(self, tmp_path, mocker):
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", False)
        instance = self._app(self._queue(tmp_path))
        assert instance._handle_journal_intent("quiz me", "a.exe") is None

    def test_journal_failure_falls_back_to_the_pipeline(self, mocker):
        """A broken journal must degrade to a normal answer, not a dead interaction."""
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)

        class _Broken:
            def recap(self, *a, **k):
                raise RuntimeError("db gone")
            def due(self, *a, **k):
                raise RuntimeError("db gone")

        instance = self._app(_Broken())
        assert instance._handle_journal_intent("quiz me", "a.exe") is None

    def test_record_entry_swallows_failures(self, mocker):
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)

        class _Broken:
            def add(self, **kwargs):
                raise RuntimeError("disk full")

        instance = self._app(_Broken())
        logged = []
        dbg = type("D", (), {"log": lambda self, m: logged.append(m)})()
        instance._record_journal_entry("a.exe", "q", "a", None, dbg)
        assert any("JOURNAL" in m for m in logged)

    def test_record_entry_captures_the_pointer_label(self, tmp_path, mocker):
        import app as app_module
        mocker.patch.object(app_module, "JOURNAL_ENABLED", True)
        queue = self._queue(tmp_path)
        instance = self._app(queue)
        result = type("R", (), {"element_label": "save button"})()
        dbg = type("D", (), {"log": lambda self, m: None})()
        instance._record_journal_entry("a.exe", "where is save", "up top", result, dbg)
        assert queue.recap()[0]["target_label"] == "save button"
