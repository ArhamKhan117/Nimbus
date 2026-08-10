"""Unit tests for T1-6a KB caching, T1-5 citations, and the T1-8 fixture labeller.

All mock-based. The caching manager takes an injected client, and the labeller's pure
schema functions are tested without a QApplication.
"""

import pytest


# --- T1-6a: caching decisions (pure) ----------------------------------------

class TestCacheThreshold:
    """Caching is a cost optimisation, so the decision to cache must itself be cheap -
    no count_tokens round trip just to decide."""

    def test_max_size_kb_is_worth_caching(self):
        """Measured live: 60,000 chars = 10,002 real tokens, and caching served
        10,008 of 10,013 prompt tokens from cache."""
        from gemini_cache import is_worth_caching
        assert is_worth_caching("x" * 60_000) is True

    def test_tiny_kb_is_not_worth_caching(self):
        from gemini_cache import is_worth_caching
        assert is_worth_caching("a short note about the app") is False

    def test_empty_kb_is_not_worth_caching(self):
        from gemini_cache import is_worth_caching
        assert is_worth_caching("") is False

    def test_estimate_is_pessimistic(self):
        """The estimator must OVER-count so the threshold admits content rather than
        silently skipping it. Real ratio measured at ~6 chars/token; we assume 4."""
        from gemini_cache import estimate_tokens
        assert estimate_tokens("x" * 60_000) >= 10_002

    def test_threshold_is_below_a_max_size_kb(self):
        from gemini_cache import MIN_CACHEABLE_TOKENS, estimate_tokens
        assert MIN_CACHEABLE_TOKENS < estimate_tokens("x" * 60_000)


class TestContentKey:
    """Keyed by content hash, not just app name, so editing the KB file invalidates
    immediately rather than serving stale docs for up to the TTL."""

    def test_same_content_same_key(self):
        from gemini_cache import content_key
        assert content_key("a.exe", "body") == content_key("a.exe", "body")

    def test_edited_content_changes_key(self):
        from gemini_cache import content_key
        assert content_key("a.exe", "body") != content_key("a.exe", "body edited")

    def test_different_apps_differ(self):
        from gemini_cache import content_key
        assert content_key("a.exe", "body") != content_key("b.exe", "body")


# --- T1-6a: manager behaviour ------------------------------------------------

class _FakeCaches:
    def __init__(self, fail=False):
        self.created, self.deleted, self.fail = [], [], fail

    def create(self, model, config):
        if self.fail:
            raise RuntimeError("caching unsupported for this model")
        name = f"cachedContents/fake{len(self.created)}"
        self.created.append(name)
        return type("Cache", (), {"name": name})()

    def delete(self, name):
        self.deleted.append(name)


class _FakeClient:
    def __init__(self, fail=False):
        self.caches = _FakeCaches(fail=fail)


BIG = "x" * 60_000


class TestKBCacheManager:
    def _mgr(self, fail=False):
        from gemini_cache import KBCacheManager
        client = _FakeClient(fail=fail)
        return KBCacheManager(client, "gemini-3-flash-preview"), client

    def test_first_call_creates_cache(self):
        mgr, client = self._mgr()
        name = mgr.get_or_create("a.exe", BIG, "sys")
        assert name == "cachedContents/fake0"
        assert mgr.stats["misses"] == 1

    def test_second_call_hits(self):
        mgr, client = self._mgr()
        first = mgr.get_or_create("a.exe", BIG, "sys")
        second = mgr.get_or_create("a.exe", BIG, "sys")
        assert first == second
        assert mgr.stats["hits"] == 1
        assert len(client.caches.created) == 1, "must not create twice"

    def test_edited_kb_creates_a_new_cache(self):
        mgr, client = self._mgr()
        mgr.get_or_create("a.exe", BIG, "sys")
        mgr.get_or_create("a.exe", BIG + " edited", "sys")
        assert len(client.caches.created) == 2

    def test_small_kb_is_skipped_without_an_api_call(self):
        mgr, client = self._mgr()
        assert mgr.get_or_create("a.exe", "tiny", "sys") is None
        assert client.caches.created == []
        assert mgr.stats["skipped"] == 1

    def test_failure_returns_none_and_does_not_raise(self):
        """Caching must never break an interaction - the caller falls back to inline
        injection, which is exactly today's behaviour."""
        mgr, _ = self._mgr(fail=True)
        assert mgr.get_or_create("a.exe", BIG, "sys") is None
        assert mgr.stats["failures"] == 1

    def test_failure_is_retried_not_permanently_cached(self):
        mgr, _ = self._mgr(fail=True)
        mgr.get_or_create("a.exe", BIG, "sys")
        mgr.get_or_create("a.exe", BIG, "sys")
        assert mgr.stats["failures"] == 2

    def test_invalidate_all_deletes_every_cache(self):
        """Caches are billed for storage duration, so leaking them past exit costs the
        user money for nothing."""
        mgr, client = self._mgr()
        mgr.get_or_create("a.exe", BIG, "sys")
        mgr.get_or_create("b.exe", BIG, "sys")
        mgr.invalidate_all()
        assert len(client.caches.deleted) == 2

    def test_invalidate_all_survives_delete_errors(self):
        mgr, client = self._mgr()
        mgr.get_or_create("a.exe", BIG, "sys")

        def boom(name):
            raise RuntimeError("already expired")

        client.caches.delete = boom
        mgr.invalidate_all()  # must not raise

    def test_concurrent_get_or_create_creates_once(self):
        import threading
        mgr, client = self._mgr()
        results, barrier = [], threading.Barrier(4)

        def worker():
            barrier.wait()
            results.append(mgr.get_or_create("a.exe", BIG, "sys"))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(set(results)) == 1, "all threads must share one cache name"


# --- T1-6b / speech hygiene --------------------------------------------------

class TestCodeExecutionSpeechHygiene:
    """T1-6b: verified live, enabling code execution makes the model switch into
    document mode and emit LaTeX and markdown headings. On a maths tutoring answer -
    the exact use case - that is worse than useless read aloud."""

    def test_inline_latex_removed(self):
        from ai import strip_non_speech
        out = strip_non_speech(r"the derivative is $f'(x) = 3x^2$ here")
        assert "$" not in out and "f'(x)" not in out

    def test_display_latex_removed(self):
        from ai import strip_non_speech
        assert "$" not in strip_non_speech(r"so $$f'(x) = u'v + uv'$$ follows")

    def test_markdown_heading_text_kept_syntax_dropped(self):
        from ai import strip_non_speech
        out = strip_non_speech("### 1. Analytical Derivation\nwe apply the rule.")
        assert "#" not in out
        assert "Analytical Derivation" in out

    def test_bold_markers_removed_words_kept(self):
        from ai import strip_non_speech
        out = strip_non_speech("we use the **Product Rule** here")
        assert "**" not in out and "Product Rule" in out

    def test_inline_code_ticks_removed(self):
        from ai import strip_non_speech
        assert "`" not in strip_non_speech("call the `save` method")

    def test_plain_prose_untouched(self):
        from ai import strip_non_speech
        text = "the derivative is three x squared times sine x plus x cubed times cosine x."
        assert strip_non_speech(text) == text

    def test_currency_amount_is_not_mistaken_for_latex(self):
        """A lone dollar sign must not swallow the rest of the sentence - the pattern
        requires a closing delimiter."""
        from ai import strip_non_speech
        out = strip_non_speech("it costs 20 dollars in total")
        assert "20 dollars in total" in out


# --- T1-5: citations ---------------------------------------------------------

class _FakeWeb:
    def __init__(self, title, uri):
        self.title, self.uri = title, uri


class _FakeGroundingChunk:
    def __init__(self, title, uri):
        self.web = _FakeWeb(title, uri)


class _FakeMeta:
    def __init__(self, chunks=(), queries=()):
        self.grounding_chunks = list(chunks)
        self.web_search_queries = list(queries)


class _FakeCandidateWithMeta:
    def __init__(self, text=None, meta=None):
        part = type("P", (), {"text": text, "function_call": None})()
        self.content = type("C", (), {"parts": [part]})()
        self.grounding_metadata = meta


class _FakeChunkWithMeta:
    def __init__(self, text=None, meta=None):
        self.candidates = [_FakeCandidateWithMeta(text, meta)]


def _response(chunks):
    from gemini_native import _GeminiNativeStreamingResponse
    return _GeminiNativeStreamingResponse(iter(chunks), 1000, 500)


class TestGroundingCitations:
    """T1-5: citations must be captured for the debug log and memory record, and must
    never reach spoken text - reading URLs aloud breaks the write-for-the-ear contract."""

    def test_citations_captured(self):
        meta = _FakeMeta(chunks=[_FakeGroundingChunk("python.org", "https://python.org")])
        stream = _response([_FakeChunkWithMeta("python 3.14 is current.", meta)])
        list(stream.text_deltas())
        cites = stream.citations()
        assert cites == [{"title": "python.org", "uri": "https://python.org"}]

    def test_urls_never_appear_in_spoken_text(self):
        meta = _FakeMeta(chunks=[_FakeGroundingChunk("python.org", "https://python.org")])
        stream = _response([_FakeChunkWithMeta("python 3.14 is current.", meta)])
        list(stream.text_deltas())
        spoken = stream.final_result().spoken_text
        assert "http" not in spoken and "python.org" not in spoken

    def test_duplicate_citations_deduped(self):
        chunk = _FakeGroundingChunk("a", "https://a.example")
        meta = _FakeMeta(chunks=[chunk, chunk])
        stream = _response([_FakeChunkWithMeta("t", meta), _FakeChunkWithMeta("u", meta)])
        list(stream.text_deltas())
        assert len(stream.citations()) == 1

    def test_citation_without_uri_is_dropped(self):
        meta = _FakeMeta(chunks=[_FakeGroundingChunk("no uri", "")])
        stream = _response([_FakeChunkWithMeta("t", meta)])
        list(stream.text_deltas())
        assert stream.citations() == []

    def test_search_queries_captured_when_citations_absent(self):
        """Measured limitation: a strong persona system_instruction suppresses
        grounding_chunks even when search demonstrably ran and improved the answer.
        Capturing queries distinguishes 'grounding broke' from 'no attribution'."""
        meta = _FakeMeta(chunks=[], queries=["current python version"])
        stream = _response([_FakeChunkWithMeta("python 3.14.", meta)])
        list(stream.text_deltas())
        assert stream.citations() == []
        assert stream.search_queries() == ["current python version"]

    def test_no_metadata_is_not_an_error(self):
        stream = _response([_FakeChunkWithMeta("plain answer", None)])
        list(stream.text_deltas())
        assert stream.citations() == []
        assert stream.search_queries() == []


# --- T1-8: fixture labeller schema ------------------------------------------

class TestFixtureSidecar:
    """T1-8: the harness refuses malformed fixtures rather than scoring against a bad
    box, so validation must catch problems at labelling time."""

    @staticmethod
    def _labeller():
        """Import the script-only labeller.

        It lives under ``tools/`` and is deliberately never importable by a runtime
        module, so the path is extended here rather than packaging it.
        """
        import sys
        from pathlib import Path
        tools = str(Path(__file__).resolve().parent.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import label_fixtures
        return label_fixtures

    def _valid(self):
        return self._labeller().build_sidecar(
            "shot.png", (1920, 1080), {"width": 3840, "height": 2160},
            [{"query": "the save icon", "box": [100, 50, 140, 90]}],
        )

    def test_valid_sidecar_passes(self):
        assert self._labeller().validate_sidecar(self._valid()) == []

    def test_box_is_space_c_pixels(self):
        """Boxes are in capture-image pixels so the harness compares like with like -
        the model's normalised output converts into exactly this space."""
        data = self._valid()
        assert data["capture"] == {"width": 1920, "height": 1080}

    def test_no_targets_rejected(self):
        validate_sidecar = self._labeller().validate_sidecar
        data = self._valid()
        data["targets"] = []
        assert any("at least one target" in p for p in validate_sidecar(data))

    def test_empty_query_rejected(self):
        validate_sidecar = self._labeller().validate_sidecar
        data = self._valid()
        data["targets"][0]["query"] = "   "
        assert any("empty query" in p for p in validate_sidecar(data))

    def test_inverted_box_rejected(self):
        validate_sidecar = self._labeller().validate_sidecar
        data = self._valid()
        data["targets"][0]["box"] = [140, 90, 100, 50]
        assert any("inverted" in p for p in validate_sidecar(data))

    def test_out_of_bounds_box_rejected(self):
        validate_sidecar = self._labeller().validate_sidecar
        data = self._valid()
        data["targets"][0]["box"] = [100, 50, 5000, 90]
        assert any("outside the capture bounds" in p for p in validate_sidecar(data))

    def test_wrong_box_arity_rejected(self):
        validate_sidecar = self._labeller().validate_sidecar
        data = self._valid()
        data["targets"][0]["box"] = [1, 2, 3]
        assert any("x0, y0, x1, y1" in p for p in validate_sidecar(data))

    def test_non_integer_box_rejected(self):
        validate_sidecar = self._labeller().validate_sidecar
        data = self._valid()
        data["targets"][0]["box"] = ["a", "b", "c", "d"]
        assert any("integers" in p for p in validate_sidecar(data))


# --- T1-8: grounding harness scoring ----------------------------------------

class TestGroundingHarness:
    """T1-8: a measurement tool that is wrong is worse than no measurement, so the
    scorer is tested as carefully as the code it scores."""

    @staticmethod
    def _harness():
        import sys
        from pathlib import Path
        tools = str(Path(__file__).resolve().parent.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import bench_grounding
        return bench_grounding

    BOX = [100, 50, 200, 90]  # 100x40, centre (150, 70)

    def test_centre_is_a_hit(self):
        assert self._harness().is_hit((150, 70), self.BOX) is True

    @pytest.mark.parametrize("point", [(100, 50), (200, 90), (100, 90), (200, 50)])
    def test_corners_count_as_hits(self, point):
        """A click exactly on a button's edge activates it, so scoring the boundary as
        a miss would misrepresent real behaviour."""
        assert self._harness().is_hit(point, self.BOX) is True

    @pytest.mark.parametrize("point", [(99, 70), (201, 70), (150, 49), (150, 91)])
    def test_just_outside_is_a_miss(self, point):
        assert self._harness().is_hit(point, self.BOX) is False

    def test_no_point_is_a_miss(self):
        assert self._harness().is_hit(None, self.BOX) is False

    def test_box_centre(self):
        assert self._harness().box_centre(self.BOX) == (150.0, 70.0)

    def test_pixel_error_at_centre_is_zero(self):
        assert self._harness().pixel_error((150, 70), self.BOX) == 0.0

    def test_pixel_error_is_euclidean(self):
        """3-4-5 triangle from the centre."""
        assert self._harness().pixel_error((153, 74), self.BOX) == pytest.approx(5.0)

    def test_declining_to_point_is_infinite_error(self):
        """A provider that declines must rank worse than one that points badly -
        failing to help is still a failure."""
        import math
        assert math.isinf(self._harness().pixel_error(None, self.BOX))


class TestLocateQueryPhrasing:
    """T1-8: found by the harness self-test. A bare noun phrase contains no directional
    word, so classify_query marks it conceptual, the native client skips the geometry
    call, and every fixture scores a miss - measuring the classifier, not the grounding."""

    @staticmethod
    def _wrap(query):
        import sys
        from pathlib import Path
        tools = str(Path(__file__).resolve().parent.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        from bench_grounding import as_locate_query
        return as_locate_query(query)

    def test_bare_noun_phrase_becomes_directional(self):
        from ai import classify_query
        assert classify_query("the save icon") != "locate", "precondition"
        assert classify_query(self._wrap("the save icon")) == "locate"

    def test_already_directional_query_is_not_doubled(self):
        wrapped = self._wrap("where is the save icon")
        assert wrapped.lower().count("where is") == 1

    @pytest.mark.parametrize("query", [
        "the save icon", "the address bar", "that little gear symbol",
    ])
    def test_every_wrapped_query_classifies_as_locate(self, query):
        from ai import classify_query
        assert classify_query(self._wrap(query)) == "locate"

    def test_whitespace_trimmed(self):
        assert not self._wrap("   the save icon   ").endswith(" ")


class TestFixtureLoading:
    """One malformed file must not waste a whole benchmark run."""

    @staticmethod
    def _harness():
        import sys
        from pathlib import Path
        tools = str(Path(__file__).resolve().parent.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import bench_grounding
        return bench_grounding

    def test_missing_directory_returns_empty(self, tmp_path):
        assert self._harness().load_fixtures(tmp_path / "nope") == []

    def test_malformed_json_is_skipped_not_fatal(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        assert self._harness().load_fixtures(tmp_path) == []

    def test_fixture_without_image_is_skipped(self, tmp_path):
        import json
        (tmp_path / "a.json").write_text(json.dumps({
            "image": "missing.png",
            "capture": {"width": 100, "height": 100},
            "targets": [{"query": "q", "box": [1, 1, 10, 10]}],
        }), encoding="utf-8")
        assert self._harness().load_fixtures(tmp_path) == []

    def test_valid_fixture_loads(self, tmp_path):
        import json
        from PIL import Image
        Image.new("RGB", (100, 100), "black").save(tmp_path / "shot.png")
        (tmp_path / "shot.json").write_text(json.dumps({
            "image": "shot.png",
            "capture": {"width": 100, "height": 100},
            "targets": [{"query": "q", "box": [1, 1, 10, 10]}],
        }), encoding="utf-8")
        loaded = self._harness().load_fixtures(tmp_path)
        assert len(loaded) == 1 and loaded[0]["image"] == "shot.png"


class TestRunSummary:
    """Guards the report maths, including the divide-by-zero case the doc called out."""

    @staticmethod
    def _summary(results):
        import sys
        from pathlib import Path
        tools = str(Path(__file__).resolve().parent.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        from bench_grounding import RunSummary, TargetResult
        s = RunSummary(provider="p", model="m", strategy="s")
        for hit, err, latency, error in results:
            s.results.append(TargetResult(
                fixture="f", query="q", box=[0, 0, 10, 10], point=(1, 1),
                hit=hit, error_px=err, latency_s=latency, error=error,
            ))
        return s

    def test_hit_rate_with_no_results_is_zero_not_a_crash(self):
        """A provider that errors on every fixture must report cleanly."""
        assert self._summary([]).hit_rate == 0.0

    def test_all_errored_does_not_divide_by_zero(self):
        s = self._summary([(False, 0.0, 1.0, "boom"), (False, 0.0, 1.0, "boom")])
        assert s.hit_rate == 0.0
        assert s.errored == 2

    def test_hit_rate_excludes_errored_targets(self):
        """Scoring an API failure as a grounding miss would blame the model for a
        network problem."""
        s = self._summary([
            (True, 1.0, 1.0, None),
            (False, 9.0, 1.0, None),
            (False, 0.0, 1.0, "network"),
        ])
        assert s.hit_rate == pytest.approx(0.5)

    def test_infinite_errors_excluded_from_median(self):
        s = self._summary([(True, 3.0, 1.0, None), (False, float("inf"), 1.0, None)])
        assert s.finite_errors() == [3.0]

    def test_serialisable(self):
        import json
        s = self._summary([(True, 3.0, 1.2, None)])
        json.dumps(s.to_dict())  # must not raise
