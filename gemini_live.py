"""Gemini Live speech-to-speech session for Nimbus (T1-4).

A parallel pipeline, not an ``AIClient``: the Live API collapses speech-to-text, the
model, and text-to-speech into one bidirectional WebSocket. Audio goes in, audio comes
back, and there is no intermediate text for Nimbus to chunk into sentences — so it cannot
sit behind the ``AIClient`` interface, exactly as ``realtime.py`` cannot.

Selected by the ``GEMINI_LIVE`` experimental toggle. Every other provider path is
untouched, and any failure here leaves ``app.py``'s normal pipeline running.

Interaction model mirrors ``realtime.py`` deliberately, so ``app.py`` needs no new
branching:

    press   -> start_turn()      open the mic, stream PCM in
    release -> respond(jpeg_b64) attach the screenshot, ask for a reply
    stop()                       abort playback, reset turn state
    close()                      tear down on shutdown

Audio format is 16 kHz PCM16 mono in, 24 kHz PCM16 mono out — the Live API's native
rates. Note the asymmetry: it differs from ``realtime.py``'s 24 kHz input, which matters
because a mismatched input rate produces audio the model hears as the wrong pitch.

**The two-mic hazard applies here too.** ``app._should_connect_stt`` exists because the
realtime path opens its own microphone; if the AssemblyAI STT mic is also opened, both
grab the input device and neither works. This class is covered by the same guard.

Testability follows ``stt.py`` / ``realtime.py``: the connection, microphone, and speaker
are all injectable, so the whole class unit-tests with no audio hardware and no network.
"""
from __future__ import annotations

import base64
import json
import threading
from typing import Callable, Optional

import numpy as np


DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"
"""Default Live model.

**Verified against `models.list()` (2026-08-09):** only a small, separate set of models
serves `bidiGenerateContent`, and it does *not* overlap with the `generateContent` models
used everywhere else in Nimbus. A plausible-looking name is not enough — the previous
default here, ``gemini-live-2.5-flash-preview``, does not exist and failed at connect with
*"not found for API version v1beta, or is not supported for bidiGenerateContent"*.

Confirmed to accept Nimbus's exact session config (AUDIO response modality plus the
``point_at`` function declaration). Known alternatives at time of writing:
``gemini-2.5-flash-native-audio-latest`` (also verified working),
``gemini-2.5-flash-native-audio-preview-12-2025``.

Override with the ``GEMINI_LIVE_MODEL`` setting. Do **not** point it at a normal chat
model such as ``gemini-3-flash-preview`` — those do not serve the Live protocol at all."""

LIVE_INPUT_SAMPLE_RATE = 16_000
"""Live API native input rate. Differs from realtime.py's 24 kHz — do not copy that value."""

LIVE_OUTPUT_SAMPLE_RATE = 24_000
"""Live API native output rate."""

LIVE_CHUNK_FRAMES = 1024
"""Mic blocksize, matching the rest of Nimbus's audio pipeline."""

_LIVE_INSTRUCTIONS = (
    "You are Nimbus, a friendly screen-aware companion that helps people use software. "
    "You can see the user's screen. Keep spoken replies to one or two short sentences "
    "unless asked to go deeper. Speak casually and warmly, the way you would to someone "
    "sitting next to you. "
    "When the user asks where something is, ALWAYS say where it is in words first, then "
    "call the point_at function with its location. Never read coordinates aloud and never "
    "spell out function names — everything you say is heard, not read. "
    "You never click for the user; you point and explain."
)

_POINT_AT_DECLARATION = {
    "name": "point_at",
    "description": (
        "Point the on-screen cursor at a UI element the user asked about. Call this "
        "whenever pointing would help. Coordinates are normalised 0-1000."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "y": {"type": "integer", "description": "normalised 0-1000, top to bottom"},
            "x": {"type": "integer", "description": "normalised 0-1000, left to right"},
            "label": {"type": "string", "description": "short 1-3 word element name"},
        },
        "required": ["y", "x", "label"],
    },
}


def pcm16_to_float32(chunk: bytes) -> np.ndarray:
    """Decode PCM16 little-endian bytes to float32 in [-1, 1] for sounddevice.

    Same conversion the ElevenLabs TTS path and ``realtime.py`` already use; int16 full
    scale is 32768.
    """
    return np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0


class GeminiLiveSession:
    """One Gemini Live push-to-talk session (T1-4).

    Public surface is intentionally identical to ``realtime.RealtimeSession`` so
    ``app.py`` selects between them without new branching.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_LIVE_MODEL,
        on_coordinate: Optional[Callable[[int, int, str], None]] = None,
        on_audio_start: Optional[Callable[[], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        connection_factory: Optional[Callable[..., object]] = None,
        mic_stream_factory: Optional[Callable[..., object]] = None,
        speaker_factory: Optional[Callable[..., object]] = None,
        vertex_project: str = "",
        vertex_location: str = "global",
    ) -> None:
        self._api_key = api_key
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location or "global"
        self._model = model
        self._on_coordinate = on_coordinate
        self._on_audio_start = on_audio_start
        self._on_transcript = on_transcript
        self._connection_factory = connection_factory or self._default_connection_factory
        self._mic_stream_factory = mic_stream_factory or self._default_mic_stream_factory
        self._speaker_factory = speaker_factory or self._default_speaker_factory

        self._client = None
        self._session = None
        self._session_cm = None
        self._mic = None
        self._speaker = None
        self._recv_thread: Optional[threading.Thread] = None
        self._recording = False
        self._stop_flag = threading.Event()
        self._audio_started_this_turn = False
        self.last_error: Exception | None = None

    # -- DI factory defaults --------------------------------------------------

    def _default_connection_factory(self):
        """Open a real Live API session.

        The SDK exposes this as an async context manager, so it is driven from a
        dedicated event loop on a background thread. Kept behind a factory so tests
        never touch asyncio.

        Backend selection matches the non-Live path exactly: a configured Google Cloud
        project means Vertex AI on Application Default Credentials, otherwise the Gemini
        API on an AI Studio key. Keeping both paths on one backend matters here, because
        an institution that routes text through its own Cloud project and its voice
        through a personal key has a data-residency hole it does not know about.
        """
        from google import genai

        if self._vertex_project:
            self._client = genai.Client(
                vertexai=True,
                project=self._vertex_project,
                location=self._vertex_location,
            )
        else:
            self._client = genai.Client(api_key=self._api_key)
        return _AsyncLiveBridge(
            client=self._client,
            model=self._model,
            config={
                "response_modalities": ["AUDIO"],
                "system_instruction": _LIVE_INSTRUCTIONS,
                "tools": [{"function_declarations": [_POINT_AT_DECLARATION]}],
            },
        )

    def _default_mic_stream_factory(self, callback):
        import sounddevice as sd

        return sd.RawInputStream(
            samplerate=LIVE_INPUT_SAMPLE_RATE,
            blocksize=LIVE_CHUNK_FRAMES,
            dtype="int16",
            channels=1,
            callback=callback,
        )

    def _default_speaker_factory(self):
        import sounddevice as sd

        stream = sd.OutputStream(
            samplerate=LIVE_OUTPUT_SAMPLE_RATE, channels=1, dtype="float32",
        )
        stream.start()
        return stream

    # -- Lifecycle ------------------------------------------------------------

    def connect(self) -> None:
        """Open the session and start the receive loop."""
        self._session = self._connection_factory()
        connect = getattr(self._session, "connect", None)
        if callable(connect):
            connect()
        self._speaker = self._speaker_factory()
        self._stop_flag.clear()
        self._recv_thread = threading.Thread(
            target=self._consume, daemon=True, name="gemini-live-recv",
        )
        self._recv_thread.start()

    def start_turn(self) -> None:
        """Hotkey press: open the mic and stream PCM in. Idempotent within a turn."""
        if self._recording:
            return
        self._audio_started_this_turn = False
        self._recording = True
        self._mic = self._mic_stream_factory(self._on_mic_chunk)
        start = getattr(self._mic, "start", None)
        if callable(start):
            start()

    def respond(self, screenshot_jpeg_b64: str, query: str = "") -> None:
        """Hotkey release: close the mic, attach the screenshot, request a reply."""
        self._recording = False
        self._close_mic()
        if self._session is None:
            return
        try:
            self._session.send_image(screenshot_jpeg_b64)
            self._session.commit()
        except Exception as exc:
            self.last_error = exc

    def stop(self) -> None:
        """Abort playback and reset turn state. Mirrors ``tts.stop()``."""
        if self._speaker is not None:
            abort = getattr(self._speaker, "abort", None)
            if callable(abort):
                try:
                    abort()
                except Exception:
                    pass
        self._audio_started_this_turn = False

    def close(self) -> None:
        """Tear everything down on shutdown."""
        self._stop_flag.set()
        self._recording = False
        self._close_mic()
        if self._speaker is not None:
            for name in ("stop", "close"):
                fn = getattr(self._speaker, name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
            self._speaker = None
        if self._session is not None:
            close = getattr(self._session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            self._session = None

    def _close_mic(self) -> None:
        if self._mic is None:
            return
        for name in ("stop", "close"):
            fn = getattr(self._mic, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        self._mic = None

    # -- Internals ------------------------------------------------------------

    def _on_mic_chunk(self, indata, frames, time_info, status) -> None:
        """sounddevice callback: forward raw PCM to the session.

        Runs on the portaudio thread, so it must be fast and must never raise — an
        exception here kills the audio thread and the mic goes dead for the session.
        """
        if not self._recording or self._session is None:
            return
        try:
            self._session.send_audio(bytes(indata))
        except Exception:
            pass

    def _consume(self) -> None:
        """Background loop: play audio, surface transcripts, capture point_at calls."""
        try:
            for message in self._session:
                if self._stop_flag.is_set():
                    break
                self._handle(message)
        except Exception as exc:
            # Connection closed or errored. The app keeps running on its normal path.
            self.last_error = exc

    def _handle(self, message) -> None:
        """Dispatch one server message. Accepts SDK objects or plain dicts (tests)."""
        audio = self._field(message, "audio")
        if audio:
            self._play(audio)

        text = self._field(message, "text")
        if text and self._on_transcript:
            try:
                self._on_transcript(text)
            except Exception:
                pass

        for call in (self._field(message, "tool_calls") or []):
            name = self._field(call, "name")
            if name != "point_at":
                continue
            args = self._field(call, "args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            self._fire_coordinate(args)

    def _fire_coordinate(self, args: dict) -> None:
        """Hand normalised coordinates to the callback.

        Values stay normalised 0-1000: ``app.py`` owns the conversion to Space C, because
        only it knows the capture dimensions for the turn.
        """
        try:
            y, x = int(args["y"]), int(args["x"])
            label = str(args.get("label", ""))
        except (KeyError, TypeError, ValueError):
            return
        if self._on_coordinate:
            try:
                self._on_coordinate(y, x, label)
            except Exception:
                pass

    def _play(self, chunk: bytes) -> None:
        if self._speaker is None:
            return
        try:
            samples = pcm16_to_float32(chunk)
            if samples.size == 0:
                return
            if not self._audio_started_this_turn:
                self._audio_started_this_turn = True
                if self._on_audio_start:
                    try:
                        self._on_audio_start()
                    except Exception:
                        pass
            self._speaker.write(samples)
        except Exception:
            pass

    @staticmethod
    def _field(obj, name):
        """Read a field from an SDK object or a dict."""
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)


class _AsyncLiveBridge:
    """Drives the async Live API from Nimbus's synchronous threading model.

    The SDK's Live session is async-only, while every other audio path in Nimbus is
    synchronous and thread-based. Rather than push asyncio up into ``app.py`` — where it
    would collide with the Qt event loop — the loop is confined here: it owns a private
    event loop on its own thread and exposes a blocking, iterable façade.

    Isolated in its own class so ``GeminiLiveSession`` stays testable with a simple fake
    and no asyncio at all.
    """

    def __init__(self, client, model: str, config: dict) -> None:
        self._client = client
        self._model = model
        self._config = config
        self._loop = None
        self._session = None
        self._cm = None
        self._thread = None
        self._ready = threading.Event()
        self._inbox: "list" = []
        self._inbox_cv = threading.Condition()
        self._closed = False
        self._error: Exception | None = None

    def connect(self) -> None:
        import asyncio

        def runner() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._main())
            except Exception as exc:
                self._error = exc
            finally:
                self._ready.set()

        self._thread = threading.Thread(
            target=runner, daemon=True, name="gemini-live-loop")
        self._thread.start()
        # Wait for the session to open, but never block startup indefinitely: a hung
        # connect must not prevent Nimbus from running on its normal pipeline.
        self._ready.wait(timeout=15.0)
        if self._error is not None:
            raise self._error

    async def _main(self) -> None:
        self._cm = self._client.aio.live.connect(
            model=self._model, config=self._config)
        self._session = await self._cm.__aenter__()
        self._ready.set()
        try:
            async for message in self._session.receive():
                self._deliver(message)
                if self._closed:
                    break
        finally:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass

    def _deliver(self, message) -> None:
        """Translate one SDK message into the flat dict shape the session expects."""
        payload: dict = {}
        data = getattr(message, "data", None)
        if data:
            payload["audio"] = data
        text = getattr(message, "text", None)
        if text:
            payload["text"] = text
        tool_call = getattr(message, "tool_call", None)
        if tool_call is not None:
            calls = []
            for fc in (getattr(tool_call, "function_calls", None) or []):
                calls.append({
                    "name": getattr(fc, "name", None),
                    "args": dict(getattr(fc, "args", None) or {}),
                })
            if calls:
                payload["tool_calls"] = calls
        if payload:
            with self._inbox_cv:
                self._inbox.append(payload)
                self._inbox_cv.notify()

    def _submit(self, coro_factory) -> None:
        import asyncio

        if self._loop is None or self._session is None:
            return
        asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)

    def send_audio(self, pcm: bytes) -> None:
        self._submit(lambda: self._session.send_realtime_input(
            audio={"data": pcm, "mime_type": f"audio/pcm;rate={LIVE_INPUT_SAMPLE_RATE}"}
        ))

    def send_image(self, jpeg_b64: str) -> None:
        self._submit(lambda: self._session.send_realtime_input(
            video={"data": base64.b64decode(jpeg_b64), "mime_type": "image/jpeg"}
        ))

    def commit(self) -> None:
        """Signal end of the user's turn so the model starts replying."""
        self._submit(lambda: self._session.send_realtime_input(audio_stream_end=True))

    def __iter__(self):
        while not self._closed:
            with self._inbox_cv:
                while not self._inbox and not self._closed:
                    self._inbox_cv.wait(timeout=0.25)
                if self._closed:
                    return
                message = self._inbox.pop(0)
            yield message

    def close(self) -> None:
        self._closed = True
        with self._inbox_cv:
            self._inbox_cv.notify_all()
