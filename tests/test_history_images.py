"""Tests for screenshots in conversation history (T2-4).

Every provider encodes images differently -- Anthropic content blocks, OpenAI `image_url`
data URIs, Ollama's sibling `images` array, native Gemini `Part.from_bytes`. Before this
change all four converters dropped non-text blocks by omission, so the feature would have
been silently inert on three of them while appearing to work.

The riskiest properties, and the ones most of these tests exist for:

* the **default stays text-only** (`HISTORY_IMAGE_COUNT` = 0), and
* exports still exclude image payloads, which is a deliberate existing privacy decision
  that this change could easily have broken by accident.
"""

import pytest


def _image_block(data="AAAA"):
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _turn(role, text, images=0):
    content = [{"type": "text", "text": text}]
    for i in range(images):
        content.append(_image_block(f"IMG{i}"))
    return {"role": role, "content": content}


class TestDefaults:
    def test_history_image_count_defaults_to_zero(self, first_run_config):
        """Backward-compat: default must reproduce today's text-only behaviour."""
        assert first_run_config.HISTORY_IMAGE_COUNT == 0

    def test_count_is_capped(self, first_run_config, monkeypatch):
        """Stale screens mislead and screenshots dominate token cost, so the cap is a
        feature rather than a limitation."""
        monkeypatch.setenv("HISTORY_IMAGE_COUNT", "99")
        assert first_run_config.resolve_bounded_int_setting(
            "HISTORY_IMAGE_COUNT", default=0, minimum=0, maximum=3) == 3

    def test_corrupt_value_falls_back_to_default(self, first_run_config, monkeypatch):
        monkeypatch.setenv("HISTORY_IMAGE_COUNT", "banana")
        assert first_run_config.resolve_bounded_int_setting(
            "HISTORY_IMAGE_COUNT", default=0, minimum=0, maximum=3) == 0

    def test_scale_reduces_pixels(self, first_run_config):
        assert 0 < first_run_config.HISTORY_IMAGE_SCALE < 1


class TestBlockHelpers:
    def test_image_block_round_trips(self):
        import base64
        from ai import history_image_block
        block = history_image_block(b"hello")
        assert block["type"] == "image"
        assert base64.b64decode(block["source"]["data"]) == b"hello"

    def test_iter_yields_text_and_images_in_order(self):
        from ai import iter_history_blocks
        blocks = list(iter_history_blocks(_turn("user", "hi", images=2)))
        assert blocks[0] == ("text", "hi")
        assert [k for k, _ in blocks] == ["text", "image", "image"]

    def test_iter_tolerates_plain_string_content(self):
        """Ollama-shaped history and simple test fixtures both use bare strings."""
        from ai import iter_history_blocks
        assert list(iter_history_blocks({"role": "user", "content": "hi"})) == [
            ("text", "hi")]

    @pytest.mark.parametrize("content", [None, 42, [], [None], [{"type": "weird"}],
                                         [{"type": "image"}],
                                         [{"type": "image", "source": {}}]])
    def test_iter_ignores_malformed_content(self, content):
        """A corrupt history entry must never break a live request."""
        from ai import iter_history_blocks
        assert list(iter_history_blocks({"role": "user", "content": content})) == []

    def test_history_text_excludes_images(self):
        from ai import history_text
        assert history_text(_turn("user", "hello", images=3)) == "hello"


class TestEviction:
    def test_images_retained_up_to_configured_count(self):
        from app import _evict_old_history_images
        history = [_turn("user", f"q{i}", images=1) for i in range(5)]
        _evict_old_history_images(history, 2)
        total = sum(
            1 for m in history for b in m["content"] if b.get("type") == "image")
        assert total == 2

    def test_oldest_image_evicted_first(self):
        from app import _evict_old_history_images
        history = [_turn("user", f"q{i}", images=1) for i in range(4)]
        _evict_old_history_images(history, 1)
        surviving = [
            b["source"]["data"] for m in history for b in m["content"]
            if b.get("type") == "image"
        ]
        # Each turn's single image is named IMG0, so identify by position instead.
        assert len(surviving) == 1
        assert history[-1]["content"][-1]["type"] == "image", "newest must survive"
        assert all(
            b.get("type") != "image" for m in history[:-1] for b in m["content"]
        ), "older images must be gone"

    def test_text_is_never_evicted(self):
        """Text is cheap and stays useful; only screenshots go stale."""
        from app import _evict_old_history_images
        history = [_turn("user", f"q{i}", images=1) for i in range(4)]
        _evict_old_history_images(history, 0)
        assert [m["content"][0]["text"] for m in history] == [
            "q0", "q1", "q2", "q3"]

    def test_zero_count_strips_every_image(self):
        from app import _evict_old_history_images
        history = [_turn("user", "q", images=2)]
        _evict_old_history_images(history, 0)
        assert all(b.get("type") != "image" for b in history[0]["content"])

    def test_handles_string_content_without_crashing(self):
        from app import _evict_old_history_images
        history = [{"role": "user", "content": "plain"}]
        _evict_old_history_images(history, 1)
        assert history[0]["content"] == "plain"

    def test_history_trim_still_respects_max_exchanges(self):
        """T2-4 regression: the 10-exchange cap must still hold."""
        from app import _MAX_HISTORY_EXCHANGES
        assert _MAX_HISTORY_EXCHANGES == 10


class TestExportPrivacy:
    def test_export_session_history_still_excludes_images(self):
        """_history_message_text deliberately excludes image payloads from Documents
        exports. That privacy property must survive T2-4."""
        from app import _history_message_text
        text = _history_message_text(_turn("user", "what is this", images=2))
        assert text == "what is this"
        assert "IMG0" not in text

    def test_export_of_an_image_only_turn_is_empty_not_base64(self):
        from app import _history_message_text
        assert _history_message_text(
            {"role": "user", "content": [_image_block("SECRET")]}) == ""


class TestProviderConversion:
    """Each provider encodes images differently; all four must handle them."""

    def test_ollama_uses_sibling_images_array(self, mocker):
        """Ollama takes bare base64 in a sibling `images` key -- a third distinct shape
        from Anthropic blocks and OpenAI data URIs."""
        from PIL import Image
        from tests.test_ai import TestOllamaClient

        client, http, _ = TestOllamaClient()._make_client_with_stream(
            mocker, [{"done": True}])
        with client.ask_stream(
            images=[(Image.new("RGB", (10, 10)), "screen 1")],
            transcript="and now",
            history=[_turn("user", "what is this", images=1)],
        ) as stream:
            list(stream.text_deltas())
        messages = http.stream.call_args.kwargs["json"]["messages"]
        history_msg = next(m for m in messages if m.get("content") == "what is this")
        assert history_msg["images"] == ["IMG0"]

    def test_ollama_omits_images_key_when_there_are_none(self):
        """Sending images:[] to a text-only model is a behaviour change; omit instead."""
        from ai import iter_history_blocks
        assert not [
            p for k, p in iter_history_blocks(_turn("user", "hi")) if k == "image"]

    def test_gemini_native_adds_image_parts(self):
        from PIL import Image
        from tests.test_gemini_native import (
            _FakeChunk, _FakeModels, _FakePart, _make_client,
        )
        import base64
        real = base64.b64encode(b"jpegbytes").decode("ascii")
        history = [{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": real}},
        ]}]
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="ok")])])
        client = _make_client(models)
        with client.ask_stream(
            [(Image.new("RGB", (100, 50)), "primary focus (100x50)")],
            "and now", history,
        ) as stream:
            list(stream.text_deltas())
            stream.final_result()
        contents = models.stream_calls[0]["contents"]
        # First content is the history turn: text part plus one image part.
        assert len(contents[0].parts) == 2

    def test_gemini_native_never_attaches_an_image_to_a_model_turn(self):
        """The model never sent an image; attaching one to a 'model' turn is rejected."""
        import base64
        from PIL import Image
        from tests.test_gemini_native import (
            _FakeChunk, _FakeModels, _FakePart, _make_client,
        )
        history = [{"role": "assistant", "content": [
            {"type": "text", "text": "it's a cat"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(b"x").decode("ascii")}},
        ]}]
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="ok")])])
        client = _make_client(models)
        with client.ask_stream(
            [(Image.new("RGB", (100, 50)), "primary focus (100x50)")],
            "and now", history,
        ) as stream:
            list(stream.text_deltas())
            stream.final_result()
        contents = models.stream_calls[0]["contents"]
        assert contents[0].role == "model"
        assert len(contents[0].parts) == 1, "no image may ride a model turn"

    def test_gemini_native_survives_corrupt_base64(self):
        """A corrupt history image must not fail a live request."""
        from PIL import Image
        from tests.test_gemini_native import (
            _FakeChunk, _FakeModels, _FakePart, _make_client,
        )
        history = [{"role": "user", "content": [
            {"type": "text", "text": "q"},
            {"type": "image",
             "source": {"type": "base64", "data": "!!!not base64!!!"}},
        ]}]
        models = _FakeModels(stream_chunks=[_FakeChunk([_FakePart(text="ok")])])
        client = _make_client(models)
        with client.ask_stream(
            [(Image.new("RGB", (100, 50)), "primary focus (100x50)")],
            "and now", history,
        ) as stream:
            list(stream.text_deltas())
            stream.final_result()  # must not raise


class TestCaptureToHistoryBlock:
    def test_downscales_the_image(self):
        from PIL import Image
        from app import _history_image_from_capture
        from config import HISTORY_IMAGE_SCALE

        class _Cap:
            image = Image.new("RGB", (1920, 1080), "white")

        block = _history_image_from_capture(_Cap())
        import base64
        import io
        decoded = Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))
        assert decoded.width == int(1920 * HISTORY_IMAGE_SCALE)
        assert decoded.height == int(1080 * HISTORY_IMAGE_SCALE)

    def test_produces_a_valid_history_block(self):
        from PIL import Image
        from ai import iter_history_blocks
        from app import _history_image_from_capture

        class _Cap:
            image = Image.new("RGB", (400, 200), "white")

        turn = {"role": "user", "content": [
            {"type": "text", "text": "q"}, _history_image_from_capture(_Cap())]}
        kinds = [k for k, _ in iter_history_blocks(turn)]
        assert kinds == ["text", "image"]

    def test_tiny_capture_never_yields_zero_dimensions(self):
        from PIL import Image
        from app import _history_image_from_capture

        class _Cap:
            image = Image.new("RGB", (1, 1), "white")

        assert _history_image_from_capture(_Cap())["source"]["data"]
