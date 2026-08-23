"""The pointer has to arrive even when one request to the model goes nowhere.

Two reported symptoms turned out to be one measurement. Latency on an identical repeated geometry
request is bimodal: 24 live calls landed either near 1.4s or in a tight cluster near 22.5s, with
nothing in between, and spacing them twelve seconds apart did not change the split. The harvest gave
up after 8s, so a stalled call became "the pointer did nothing", and the turn also felt slow because
it had spent the whole timeout deciding that.

A stall in one request says nothing about the next, so waiting is the worst available strategy. These
tests pin the two things that follow from that: a second attempt goes out rather than waiting, and the
first reply wins.

No network here. The stall is simulated, because the real one is intermittent and a test that needs a
22-second coincidence is a test that fails for the wrong reasons.
"""
from __future__ import annotations

import threading
import time

import pytest


class FakeCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


def response_with(name: str, args: dict):
    """The shape ``_GeometryWorker`` reads: candidates -> content -> parts -> function_call."""
    part = type("Part", (), {"function_call": FakeCall(name, args)})()
    content = type("Content", (), {"parts": [part]})()
    candidate = type("Candidate", (), {"content": content})()
    return type("Response", (), {"candidates": [candidate]})()


class ScriptedModels:
    """``client.models`` whose call durations are scripted, one entry per attempt."""

    def __init__(self, durations: list[float], name: str = "point_at") -> None:
        self._durations = list(durations)
        self._name = name
        self.attempts = 0
        self._lock = threading.Lock()

    def generate_content(self, **kwargs):
        with self._lock:
            index = self.attempts
            self.attempts += 1
        duration = self._durations[min(index, len(self._durations) - 1)]
        if duration < 0:
            time.sleep(-duration)
            raise RuntimeError("simulated failure")
        time.sleep(duration)
        return response_with(self._name, {"y": 500, "x": 300, "label": f"attempt {index}"})


def worker(durations: list[float], **kwargs):
    from gemini_native import _GeometryWorker

    client = type("Client", (), {"models": ScriptedModels(durations, **kwargs)})()
    return _GeometryWorker(client=client, model_id="m", contents=[], config=None), client


class TestTheHedge:
    def test_a_fast_call_is_not_hedged(self, monkeypatch):
        """A healthy turn must not pay for a second request. Nearly every turn is healthy."""
        import gemini_native

        monkeypatch.setattr(gemini_native, "GEOMETRY_HEDGE_AFTER", 0.4)
        job, client = worker([0.05])
        job.start()

        assert job.result(timeout=2.0) == [("point_at", {"y": 500, "x": 300, "label": "attempt 0"})]
        assert client.models.attempts == 1
        assert "hedged" not in job.diagnostics()

    def test_a_stalled_call_is_overtaken_by_the_hedge(self, monkeypatch):
        """The reported bug. The first attempt outlives the ceiling; the second answers inside it."""
        import gemini_native

        monkeypatch.setattr(gemini_native, "GEOMETRY_HEDGE_AFTER", 0.3)
        # First attempt: longer than the whole test. Second: quick.
        job, client = worker([30.0, 0.05])
        job.start()

        harvested = job.result(timeout=3.0)

        assert harvested, "the hedge should have produced geometry the first attempt could not"
        assert client.models.attempts == 2
        assert "hedge" in job.diagnostics()

    def test_the_first_reply_wins_and_the_other_is_discarded(self, monkeypatch):
        """Two attempts at one question must not become two points on screen."""
        import gemini_native

        monkeypatch.setattr(gemini_native, "GEOMETRY_HEDGE_AFTER", 0.2)
        job, client = worker([0.6, 0.05])
        job.start()

        harvested = job.result(timeout=3.0)

        assert len(harvested) == 1, f"expected exactly one call, got {harvested}"
        # Both attempts ran, and the label proves which one was kept.
        assert harvested[0][1]["label"] == "attempt 1"

    def test_both_failing_stops_the_wait_early(self, monkeypatch):
        """A refusal is not a reason to hold the turn open to the ceiling."""
        import gemini_native

        monkeypatch.setattr(gemini_native, "GEOMETRY_HEDGE_AFTER", 0.2)
        job, _ = worker([-0.05, -0.05])
        job.start()

        started = time.monotonic()
        assert job.result(timeout=5.0) == []
        assert time.monotonic() - started < 3.0, "it waited for a ceiling it had no use for"
        assert "failed" in job.diagnostics()

    def test_diagnostics_names_what_happened(self, monkeypatch):
        """A turn that does not point must say which of the three reasons applied."""
        import gemini_native

        monkeypatch.setattr(gemini_native, "GEOMETRY_HEDGE_AFTER", 0.2)
        job, _ = worker([30.0, 30.0])
        job.start()
        job.result(timeout=0.6)

        note = job.diagnostics()
        assert "no reply" in note and "abandoned" in note


class TestForcingTheToolCall:
    """`locate` and teaching mode must not be allowed to answer in prose.

    A live turn classified as ``locate`` made the geometry call and got nothing back, while the
    speech call described the location in words: "it is that big orange button right in the middle of
    the screen". The model knew. Declaring a tool only invites a call, so the invitation is now a
    requirement for the cases where a refusal is certainly wrong.
    """

    @pytest.fixture
    def api(self):
        from gemini_native import GeminiNativeClient

        return GeminiNativeClient(api_key="unused", model_id="gemini-3-flash-preview")

    def _mode(self, config) -> str | None:
        tool_config = getattr(config, "tool_config", None)
        if tool_config is None:
            return None
        return str(getattr(tool_config.function_calling_config, "mode", "")).upper()

    def test_the_geometry_call_forces_a_function_call(self, api):
        config = api._build_config(
            "prompt", 1024, "where is the save button",
            annotation_mode=False, with_tools=True, force_tool_call=True)

        assert "ANY" in self._mode(config)

    def test_the_speech_call_never_forces_anything(self, api):
        """It declares no tools at all: their presence is what silences prose."""
        config = api._build_config(
            "prompt", 1024, "where is the save button",
            annotation_mode=False, with_tools=False, force_tool_call=True)

        assert self._mode(config) is None

    def test_a_diagnostic_question_is_left_discretionary(self, api):
        """"Why is my build failing" has nothing to point at, and a forced guess would move the
        cursor to whatever the model could find."""
        config = api._build_config(
            "prompt", 1024, "why is my build failing",
            annotation_mode=False, with_tools=True, force_tool_call=False)

        assert self._mode(config) is None


class TestWritingOnTheScreen:
    """Teaching mode has to be able to write, not only outline.

    Reported: asked to solve an equation on screen, Nimbus boxed part of the equation, said the
    answer aloud, and wrote nothing. `annotations.Label` existed, `overlay._draw_label_pill` drew it,
    and the tag grammar had `[LABEL:x,y:text]`. What was missing was a **tool**, so on the native
    provider the model had no means of producing one: geometry comes from function calls, and a tag
    written into the spoken channel is stripped before anything can render it.

    Verified live afterwards on a real equation: four `write_note` calls came back as
    "1) subtract 7 from both sides", "2) 3x = 15", "3) divide by 3", "x = 5", stacked in a column.
    """

    @pytest.fixture
    def api(self):
        from gemini_native import GeminiNativeClient

        return GeminiNativeClient(api_key="unused", model_id="gemini-3-flash-preview")

    def _names(self, tools):
        return [declaration.name
                for tool in tools
                for declaration in (tool.function_declarations or [])]

    def test_teaching_mode_can_write(self, api):
        assert "write_note" in self._names(api._build_tools(True))

    def test_the_pointer_path_cannot(self, api):
        """Outside teaching mode the only job is the cursor, and an unused tool is a tool the
        model can still choose, which would put text on the screen of someone who never asked."""
        assert "write_note" not in self._names(api._build_tools(False))

    def test_a_written_line_becomes_a_label(self):
        from annotations import Label
        from gemini_native import _GeminiNativeStreamingResponse

        stream = _GeminiNativeStreamingResponse(iter(()), 1000, 1000, None, owner=None)
        stream._calls = [("write_note", {"y": 300, "x": 500, "text": "x = 5"})]
        stream._geometry_collected = True

        shapes = stream.geometry()

        assert len(shapes) == 1
        assert isinstance(shapes[0], Label)
        assert shapes[0].text == "x = 5"

    def test_an_empty_line_is_dropped(self):
        """A pill with nothing in it is a smudge on the user's screen."""
        from gemini_native import _GeminiNativeStreamingResponse

        stream = _GeminiNativeStreamingResponse(iter(()), 1000, 1000, None, owner=None)
        stream._calls = [("write_note", {"y": 300, "x": 500, "text": "   "})]
        stream._geometry_collected = True

        assert stream.geometry() == []

    def test_several_lines_survive_as_several_labels(self):
        """A worked solution is a sequence, so nothing may collapse them into one."""
        from annotations import Label
        from gemini_native import _GeminiNativeStreamingResponse

        stream = _GeminiNativeStreamingResponse(iter(()), 1000, 1000, None, owner=None)
        stream._calls = [
            ("write_note", {"y": 300, "x": 500, "text": "1) subtract 7"}),
            ("write_note", {"y": 340, "x": 500, "text": "2) 3x = 15"}),
            ("write_note", {"y": 380, "x": 500, "text": "x = 5"}),
        ]
        stream._geometry_collected = True

        labels = [shape for shape in stream.geometry() if isinstance(shape, Label)]
        assert [label.text for label in labels] == [
            "1) subtract 7", "2) 3x = 15", "x = 5"]
        assert [label.y for label in labels] == sorted(label.y for label in labels)
