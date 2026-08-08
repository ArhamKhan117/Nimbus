# Design Document

## Overview

The pipeline is a state machine driven by two events (press, release) and executed across five
threads, with **exactly one rule for crossing between them**: only a `pyqtSignal` may. `app.py`
owns all of it; every other module is either a pure function, an injectable class, or a Qt widget
touched only on the main thread.

The 1.5 s latency target is met by overlapping four things that would otherwise be sequential:
capture and memory recall happen while the user is still speaking; the release-time capture runs in
parallel with speech finalisation; the answer streams; and speech synthesis starts on the first
complete sentence rather than the whole response.

> Consolidated from `IMPROVEMENTS.md` §2.3 (threading), §2.4 (pipeline sequence) and §2.5 (latency
> budget), plus the Tier 0 items `T0-3`, `T0-5` and `T0-7`. Measurements live there.

## Architecture

```
STARTUP
  mutex ─→ QApplication ─→ licence gate ─→ settings/welcome if needed
        ─→ create ai/stt/tts clients ─→ tts.warmup (thread)
        ─→ stt.connect()   mic ~400-1000ms + socket ~800-1200ms, ONCE
        ─→ NimbusApp.start(): OverlayController, PushToTalkHotkey, audio-level callback
        ─→ revalidate licence (thread) ─→ watch for show-window requests (thread)

PRESS ─ Qt main thread, via sig_pressed
  cancel prior worker · hide spinner · clear annotations · clear caption
  tts.stop()
  stt.set_tts_grace_until(now + 200ms)        ← BEFORE the chime, deliberately
  chime (async) · chat state = "listening"
  get_foreground_app()                         ← the app the question is ABOUT
  stt.start_recording()                        ← <1ms
  press_cursor = get_cursor_position()
  ┌─ thread nimbus-press-capture ──────────────────────────────┐
  │  guarded capture (hide → DwmFlush → grab → show)           │
  │  memory.recall(app)                                        │
  │  publish (captures, memory, cursor) as ONE atomic write     │
  └────────────────────────────────────────────────────────────┘
  show waveform at cursor

  during the hold:  portaudio thread → RMS → sig_audio_level → waveform
                    websocket thread → partial → sig_caption → on-screen caption

RELEASE ─ Qt main thread, via sig_released
  hide waveform
  release_cursor = get_cursor_position()       ← SYNCHRONOUSLY, before any thread
  show spinner at cursor
  cancel prior worker (+ tts.stop + grace)
  fresh cancel_event · Queue(maxsize=1)
  ┌─ thread nimbus-release-capture ─┐   ┌─ thread nimbus-pipeline ─┐
  │ join press thread (0.5s)        │   │ stt.stop_recording()      │
  │ reuse if moved <= 150px         │   │ ...                       │
  │ else guarded re-capture         │   │ queue.get(timeout=5)      │
  │ put (captures, memory, reason)  │   │ ...                       │
  └─────────────────────────────────┘   └───────────────────────────┘
                                          wall clock = max(), not sum()

PIPELINE WORKER ─ 11 cancel checkpoints
  stop_recording → empty? abandon
  emit user message · state = "thinking"
  journal intent? answer locally, no model call, no screenshot
  read capture queue (5s) · save diagnostics
  build user_text: memory prefix + transcript
  kb.recall(app, query=transcript)
  voice_only = no captures → append "you cannot see the screen" to the prompt
  ai.ask_stream(...) ─ per delta: flush sentences → tts.speak_sentence
                       stop flushing at first '['  (tag guard)
  final_result() → strip tags FIRST → parse geometry
  optional refinement (crop | agentic | off)
  unscale_model_coords → hide spinner → sig_point_at
  sig_show_annotations (annotation mode)
  sig_record_memory · journal entry · history append + trim
  emit nimbus message · record turn · done chime
  finally: hide spinner · state = "idle" · close diagnostics
```

## Components and Interfaces

### `hotkey.PushToTalkHotkey`

```python
PushToTalkHotkey(
    on_press, on_release, hotkey="ctrl+alt+space",
    listener_class=None,          # DI seam: defaults to pynput.keyboard.Listener
    on_cancel=None,               # Escape, gated on is_in_flight()
    is_in_flight=None,            # cheap liveness read, called on every Escape
    shortcuts=None,               # {"h": ..., "n": ...} matched on virtual key code
)
```

Independent key-down flags rather than a sequence, so all six press orders work. `parse_hotkey`
returns a frozen `HotkeyCombo` and owns validation, the normalised display form and the three
conflict messages — the Settings capture widget calls into it rather than re-implementing any of it.

**`suppress=False` is load-bearing.** pynput's suppress flag is global and all-or-nothing: `True`
installs a `WH_KEYBOARD_LL` hook that blocks every key event on the system.

### `capture.py`

```python
pick_resolution(w, h) -> (target_w, target_h)
resize_for_model(img, tw, th) -> (resized, scale_x, scale_y)
unscale_model_coords(mx, my, sx, sy, left, top, tw, th) -> (px, py)
capture_all_screens() -> list[LabeledCapture]     # cursor screen first
monitor_containing(x, y, monitors) -> dict         # half-open, primary fallback
```

`pick_resolution` order is: reject non-positive, find the closest-aspect candidate, **return native
if it would upscale**, then fall back to a uniform aspect-preserving size if the drift exceeds 5%.
The native check comes before the aspect test on purpose — a 1280×1024 panel is 6% off 4:3 and should
still return native.

`unscale_model_coords` clamps **before** scaling. Space C is unclamped by contract, and this is the
single place clamping happens.

### `stt.STT` and `tts.TTS`

Both are ABCs with cloud and local implementations and a factory. Lifecycle is split so the
expensive part happens once: `connect()` at startup, `start_recording()` per press (<1 ms),
`stop_recording()` per release, `disconnect()` at shutdown.

The finalisation loop is the subtle part. A previous version broke out after the first 300 ms with no
event and returned a stale partial — *"How do I add—"* instead of *"How do I add an MCP server?"*.
The current loop waits the full 2 s deadline, and after the first event waits a further 100 ms for a
trailing one.

The speech-synthesis layer runs a prefetch/playback pair with a `Queue(maxsize=1)` between them. That
size is the backpressure: exactly one sentence stays warm. An integer epoch, captured *before*
generation and compared at playback, is what rejects audio that had already been generated when the user
cancelled.

### `NimbusApp` signals — 26

Grouped by what they drive: overlay lifecycle (`sig_hide_overlay`, `sig_show_overlay`), geometry
(`sig_point_at`, `sig_show_annotations`, `sig_clear_annotations`), interaction state
(`sig_show_waveform`, `sig_audio_level`, `sig_show_spinner`, `sig_caption`), the chat panel
(`sig_chat_message`, `sig_chat_delta`, `sig_chat_state`), shell state (`sig_listening_changed`,
`sig_chat_visible_changed`, `sig_show_window`), and lifecycle (`sig_cancel`,
`sig_licence_gate_required`, `sig_export_session_history`).

`sig_chat_message` carries `object` rather than a typed payload so `sessions` stays a lazy import —
the panel is optional and so is its cost.

### The provider factory — five implementations, one that needs no key

```python
STT: AssemblyAI "u3-rt-pro" streaming  |  faster-whisper (local)
TTS: Cartesia "sonic-3"  |  ElevenLabs "eleven_flash_v2_5"  |  Kokoro-82M via ONNX (local)
```

Requirements 4 and 5 name no provider on purpose: the ABC is the contract and the factory is the only
place a settings string becomes a concrete class. What matters at the design level is the one
combination that is a **gate rather than a fallback** — Ollama plus faster-whisper plus Kokoro, no key
and no network. Invariant 29 makes that a regression test on every model-layer change, because a change
that only works against a cloud provider is not finished.

Kokoro's weights are **downloaded on first use** into `~/.nimbus/kokoro/`, roughly 336 MB. They are not
bundled, because that is larger than everything else in the installer put together. The path is
documented so a user can delete it and get the space back.

Warmup runs on background threads at startup. It is pure optimisation: a failure is swallowed, and an
unwarmed provider still answers, just with its first-call cost charged to the user's first turn.

### The two speech-to-speech paths — deliberately not `AIClient`s

`gemini_live.py` and `realtime.py` collapse recognition, the model and synthesis into a single socket.
That is why they sit outside the `AIClient` hierarchy: there is no intermediate text, so there is
nothing to sentence-chunk and no transcript to hand onward. Forcing them into the interface would mean
inventing a transcript that never existed.

They expose `connect / start_turn / respond / stop / close`, which is enough for the orchestrator to
drive them with no new branching.

Three things reach into this path from outside, and each is specified in its own document rather than
here: the Privacy Guard's choke point, the Vertex backend switch, and cancellation. The one constraint
that belongs *here* is **device contention** — a speech-to-speech path and the streaming recogniser both
claim the same input device, and when both hold it neither works and neither produces an error worth
reading. So the recogniser is not opened while this path is active.

Both modules are named individually in the bundler's hidden-import list *and* the selftest's runtime
module list. `gemini_live` shipped broken through exactly that gap once: a function-local import behind
a default-off toggle is invisible to the static graph and to the selftest simultaneously, so the failure
surfaces on a user's machine at first use of the feature.

## Data Models

```python
@dataclass class LabeledCapture:
    image: Image; label: str; is_cursor_screen: bool; monitor: dict
    target_width: int; target_height: int; scale_x: float; scale_y: float
    source_image: Image | None      # native resolution, kept for the refinement crop
    cursor_physical: tuple[int, int] | None

@dataclass class PointParseResult:            # not frozen - see ai.py
    spoken_text: str
    coordinate: tuple[int, int] | None       # Space C, UNCLAMPED
    element_label: str | None
    screen_number: int | None
    malformed_tags: tuple[str, ...] = ()
```

## Correctness Properties

Executable statements of what must hold for *any* input, not just the tabled cases. Each names the
function it constrains and the generator that would exercise it.

### Property 1: Clamping is total

For any integers `(model_x, model_y)` and any positive `(target_w, target_h)`,
`unscale_model_coords` returns a point inside the target monitor's physical rectangle. Generator:
arbitrary signed integers, including values far outside the declared resolution, since Space C is
unclamped by contract.

**Validates: Requirements 8.1, 6.1**

### Property 2: Scale factors are equal on both axes

For any positive `(width, height)`, `pick_resolution` followed by `resize_for_model` yields
`scale_x == scale_y`. This is the property that fails for an ultrawide when the aspect fallback is
missing, and `scale_x != scale_y` is the diagnostic for the whole class of bug.

**Validates: Requirements 8.3, 8.4**

### Property 3: Capture never upscales

For any positive `(width, height)`, both components of `pick_resolution(width, height)` are less than
or equal to the corresponding input. Manufacturing pixels softens text while adding no visual
evidence for the model.

**Validates: Requirements 8.2**

### Property 4: Coordinate round trip is near-identity

For any point in Space C on any monitor at any device-pixel ratio, converting C → A → B lands within
one logical pixel of the original proportional position. Generator: the cross product of candidate
resolutions, monitor origins including negative ones, and ratios in {1.0, 1.25, 1.5, 2.0, 2.5}.

**Validates: Requirements 6.1, 8.4**

### Property 5: Sentence flushing is lossless

For any string, the concatenation of every sentence `flush_sentences` returns plus the remaining
buffer equals the input, modulo exactly the separator whitespace accounted for by
`already_flushed_chars`. Generator: arbitrary text with arbitrary runs of `.`, `!`, `?` and
whitespace, including a terminator at position zero and at the final index.

**Validates: Requirements 5.1, 5.3**

### Property 6: No coordinate survives stripping

For any text containing any mixture of well-formed, malformed, truncated and mixed-case coordinate or
shape tags, the returned spoken text matches none of the tag patterns. Generator: tags assembled from
arbitrary integers (including negative), arbitrary labels, arbitrary internal whitespace, and
arbitrary truncation points.

**Validates: Requirements 5.2, 5.3, 5.4**

### Property 7: Cancellation is absorbing

Once the cancel event is set, no subsequent pointer emission, annotation emission, memory write or
journal entry occurs for that turn, whichever checkpoint the worker had reached. Generator: the cancel
event set at each of the 11 checkpoints in turn.

**Validates: Requirements 7.5, 7.6**

### Property 8: Monitor resolution is total and exclusive

For any point on the virtual desktop and any monitor arrangement, `monitor_containing` returns exactly
one monitor, and returns the primary for a point in an inter-monitor dead zone. Generator: arbitrary
monitor rectangles including negative origins and non-contiguous arrangements.

**Validates: Requirements 8.7**

### Property 9: Every provider satisfies its interface identically

For each of the two recognition and three synthesis implementations, the abstract method set is fully
implemented and the factory returns an instance for every accepted settings string and raises a named
error for every other. Generator: the cross product of settings strings and providers, including
unknown values and mixed case.

**Validates: Requirements 12.1, 12.2, 12.3**

### Property 10: The local combination reads no key

For a configuration selecting the local model, the local recogniser and the local voice, no credential
lookup and no outbound request occurs during a whole turn. Asserted with the credential store and the
HTTP layer both stubbed to raise, because a passing test that quietly read a key would prove nothing.

**Validates: Requirements 12.4**

### Property 11: Warmup cannot prevent startup

For any exception raised by any warmup thread, the application reaches its running state and the
provider still answers when called. Generator: each provider's warmup raising at each stage.

**Validates: Requirements 12.7, 12.8**

### Property 12: Both speech-to-speech modules are registered twice

Static analysis finds `gemini_live` and `realtime` in both the bundler's hidden-import list and the
selftest's runtime module list. Asserted as a test rather than reviewed, because this is the exact gap
one of them already shipped through and a static graph cannot see a function-local import.

**Validates: Requirements 11.9**

### Property 13: The recogniser and the socket never both hold the device

For any sequence of mode switches, at most one of the streaming recogniser and a speech-to-speech
socket holds the input device. Generator: interleaved enable, disable and turn events across both
modes.

**Validates: Requirements 11.4**

### Property 14: A dropped socket returns to idle

For a socket closing at any point in a turn — before the first byte, mid-audio, after completion — the
state returns to idle within the deadline and the following turn behaves identically to one after a
clean turn.

**Validates: Requirements 11.7**

## Error Handling

The governing principle: **a failure in an optional stage costs the user that stage, never the
answer.**

| Failure | Response |
|---|---|
| Microphone will not start | Toast, error tone, abandon the turn. No exception escapes the press handler |
| Press-time capture throws | Publish `None`; the release path re-captures |
| Capture worker throws | Push an error tuple so the pipeline's `queue.get` cannot hang |
| Capture worker times out | Press-time fallback; abort only if that is also absent |
| Knowledge-base read throws | Log and continue with vision plus memory. KB files are user-controlled |
| Privacy Guard suppresses | Empty capture list. The turn proceeds voice-only; no coordinate is placed |
| Model request fails | `RuntimeError` with a three-point checklist: key, model access, connectivity |
| Refinement fails or lands out of bounds | Keep the original coordinate. An uncertain correction is worse than none |
| Speech synthesis prefetch fails | `None` in the queue; playback skips it without deadlocking |
| Overlay dispatch fails | Log a warning. The answer is still spoken |
| Diagnostics cannot be written | Substitute a no-op session with the same interface |
| Provider warmup throws | Swallow it. An unwarmed provider still answers, just slower on the first turn |
| Local voice weights missing or partial | Re-download on next use; fall back to a cloud voice if one is configured |
| Speech-to-speech socket closes mid-turn | Return to idle within the deadline. The next turn is unaffected |
| Both the recogniser and the socket claim the device | Cannot happen by construction: the recogniser is not opened while the socket is active |

## Testing Strategy

- **Pure maths**, exhaustively, with no Qt: `pick_resolution` (native, candidate and fallback paths),
  `unscale_model_coords` (clamp order), `flush_sentences`, `parse_hotkey` (every rejection message),
  `monitor_containing` (boundaries and the dead zone).
- **Threading and cancellation** with injected stubs: `tests/test_cancel.py` drives all 11
  checkpoints and asserts no side effect escapes; `tests/test_app.py::TestPressStateThreadSafety`
  asserts the release worker always publishes, even on error.
- **Constant drift guards** on the click-through bit pattern and the reuse threshold.
- **Integration** in `tests/test_integration.py`: a full turn with a fake model, fake audio and mock
  screens, asserting the signal sequence and that spoken text contains no tag.
- **Manual smoke test** is mandatory for any change touching an invariant. No automated test covers
  the real pipeline end to end, and pretending otherwise is how a stale build passes.
