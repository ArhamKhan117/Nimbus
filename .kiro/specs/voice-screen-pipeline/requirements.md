# Requirements Document

## Introduction

The push-to-talk pipeline is Nimbus's core interaction: the user holds a global hotkey, speaks a
question about what is on their screen, and releases. Nimbus captures every monitor, transcribes the
speech, sends both to a vision model, streams the spoken answer back, and flies a pointer to the
control it is talking about.

Everything else in the product is downstream of this loop. The design target is **1.5 seconds from
key-release to first audible word** (`config.E2E_LATENCY_BUDGET_S`), which is only reachable because
the stages overlap rather than run in sequence.

> **Provenance.** This document was consolidated into Kiro's spec format from
> `IMPROVEMENTS.md` §2.3–2.5 and §3 (Tier 0), which is where the original planning, the per-item
> `⚠ VERIFY` blocks and the measurement tables live. Task IDs such as `T0-3` are preserved so they
> can be grepped against that document. Read it for the reasoning behind any decision here.

## Glossary

| Term | Meaning |
|---|---|
| **PTT** | Push-to-talk. Holding `Ctrl+Alt+Space` (configurable) to ask a question |
| **Turn** | One complete press → release → answer cycle |
| **Space A** | Physical pixels in virtual-desktop coordinates, owned by `capture.py` |
| **Space B** | Qt logical (DIP) pixels, per-screen local, owned by `overlay.py` |
| **Space C** | The model's declared screenshot resolution, returned by `ai.py` unclamped |
| **Cursor screen** | The monitor containing the mouse pointer. Always index 0 in a capture list |
| **Reuse threshold** | 150 px of cursor movement, under which press-time captures are reused |
| **TTS grace window** | 200 ms after `tts.stop()` during which microphone input is discarded |
| **S2S** | Speech-to-speech. One socket carrying audio in and audio out, replacing the STT → model → TTS chain |
| **Warmup** | A background thread that pays a provider's first-call cost before the user's first turn |
| **Provider factory** | The function that maps a settings string to a concrete STT or TTS implementation |

## Requirements

### Requirement 1: Global push-to-talk hotkey

**User Story:** As a user working in another application, I want to summon Nimbus with one chord
without leaving what I am doing, so that asking a question costs me nothing but the question.

#### Acceptance Criteria

1. THE hotkey listener SHALL install a global keyboard hook with `suppress=False`, so that no
   keystroke on the system is blocked.
2. WHEN all keys in the configured chord are held down THEN THE listener SHALL transition from `IDLE`
   to `RECORDING` and fire `on_press` exactly once, regardless of the order the keys were pressed in.
3. WHEN any key of the chord is released WHILE in `RECORDING` THEN THE listener SHALL fire
   `on_release` exactly once, clear all key state, and return to `IDLE`.
4. THE listener SHALL invoke every callback outside its internal lock.
5. IF an exception escapes a callback THEN THE listener SHALL swallow it, because an escaping
   exception terminates the listener thread and disables the hotkey for the rest of the session.
6. THE chord parser SHALL require at least one modifier and exactly one final key, and SHALL accept
   Space, Enter, Tab, A–Z, 0–9 and F1–F12 as that final key.
7. THE chord parser SHALL reject `Alt+Space`, `Ctrl+Space` and `Ctrl+Shift+Space` with messages naming
   the conflicting application, because each is already bound by Windows, VS Code or Excel.
8. WHERE the hotkey is disabled via `set_enabled(False)`, THE listener SHALL gate its callbacks
   without uninstalling the hook, so that toggling takes effect immediately and needs no restart.
9. WHEN a secondary shortcut key is pressed WHILE the chord's modifiers are held THEN THE listener
   SHALL match on virtual key code rather than character, so that a control character (`Ctrl+H`
   arriving as `\x08`) still resolves.

### Requirement 2: Overlapped capture and speech finalisation

**User Story:** As a user, I want the answer to start almost as soon as I stop talking, so that the
interaction feels like speaking to someone rather than submitting a form.

#### Acceptance Criteria

1. WHEN the hotkey is pressed THEN THE system SHALL start a background capture of every screen and a
   per-app memory recall, so that both overlap with the user still speaking.
2. THE press handler SHALL set the TTS grace window **before** playing the listening chime, because
   the chime's first invocation pays a 400–500 ms audio-device cold start that must not be counted
   against the grace window.
3. WHEN the hotkey is released THEN THE system SHALL snapshot the cursor position synchronously,
   before spawning any thread, so that mouse movement during finalisation cannot change the
   reuse-versus-recapture decision.
4. WHEN the hotkey is released THEN THE system SHALL start the capture worker **before** the pipeline
   worker, so that wall-clock time becomes `max(speech finalisation, capture)` rather than their sum.
5. IF the cursor has moved no more than 150 px between press and release AND press-time captures
   exist THEN THE system SHALL reuse those captures rather than grabbing again.
6. IF the capture worker does not deliver a result within 5 seconds THEN THE system SHALL fall back to
   the press-time captures, and SHALL abort the turn only if those are also absent.
7. THE press-time capture result SHALL be published as one atomic write, so that no reader can observe
   captures without their matching memory context.

### Requirement 3: The overlay is never in its own screenshot

**User Story:** As a user, I want Nimbus to point at my application, not at itself, so that its
answers are about the thing I asked about.

#### Acceptance Criteria

1. WHEN any screen capture is about to be taken THEN THE system SHALL hide the pointer overlay, wait
   for the compositor, grab, and then show the overlay again.
2. THE overlay SHALL be restored in a `finally` block, so that an exception during the grab cannot
   leave the user with a permanently invisible pointer.
3. THE compositor wait SHALL use `DwmFlush()`, which blocks until the next present completes, and
   SHALL fall back to a fixed 50 ms sleep only when `DwmFlush` is unavailable.
4. THE system SHALL route every capture through a single choke point, so that a call site added later
   inherits both this cycle and the Privacy Guard without being changed.
5. WHERE the chat panel is excluded from capture at the OS level, THE system SHALL NOT hide it, and
   WHERE exclusion is unavailable, THE system SHALL include it in the hide/show cycle.

### Requirement 4: Speech to text

**User Story:** As a user, I want Nimbus to hear what I actually said, including the end of my
sentence, so that I do not have to repeat myself.

#### Acceptance Criteria

1. THE system SHALL open the microphone and the transcription connection once at startup, so that a
   hotkey press costs under 1 ms rather than the ~6 s of connection setup.
2. WHEN recording stops THEN THE system SHALL request an immediate endpoint and SHALL wait up to
   2 seconds for the authoritative end-of-turn event.
3. WHEN a first end-of-turn event arrives THEN THE system SHALL wait a further 100 ms for a trailing
   event, so that a user who paused mid-hold gets both utterances.
4. IF no end-of-turn event arrives within the deadline THEN THE system SHALL return the latest partial
   transcript rather than an empty string.
5. WHILE within the TTS grace window, THE system SHALL discard microphone chunks, so that speaker
   decay from the previous answer cannot contaminate this transcript.
6. WHILE not recording or within the grace window, THE system SHALL still compute and report the audio
   level, so that the on-screen waveform keeps reacting instead of appearing frozen.
7. IF the transcription stream reports an error THEN THE system SHALL surface it rather than hang, and
   SHALL clear the stored error so that the next turn is unaffected.
8. WHEN the final transcript is empty or whitespace THEN THE system SHALL abandon the turn silently
   and return the interface to idle.

### Requirement 5: Streaming answer and spoken output

**User Story:** As a user, I want Nimbus to start talking while it is still thinking, so that a
four-second answer does not feel like a four-second wait.

#### Acceptance Criteria

1. WHILE the model response streams, THE system SHALL flush each complete sentence to text-to-speech
   at a `[.!?]` followed by whitespace.
2. IF the accumulating buffer contains a `[` AND the provider does not return structured geometry
   THEN THE system SHALL stop flushing for the remainder of the turn, so that a coordinate tag cannot
   be spoken.
3. WHEN the stream completes THEN THE system SHALL flush the remaining tail computed from the
   **tag-stripped** text, so that the tail contains no coordinates even if a later transform fails.
4. THE tag-stripping SHALL happen before the tail flush and outside any `try` block, because it is
   pure regex and cannot raise.
5. WHERE a provider returns geometry on a separate channel, THE system SHALL skip the bracket guard
   entirely, so that legitimate prose such as "the array index [0]" does not halt speech.
6. THE text-to-speech layer SHALL prefetch exactly one sentence ahead of the one playing, and SHALL
   reject any prefetched audio whose epoch is stale.
7. WHEN playback is stopped THEN THE system SHALL bump the epoch, drain both queues, set the cancel
   event, abort the audio stream, and close any open HTTP response.

### Requirement 6: Pointing at the answer

**User Story:** As a user, I want the pointer to land on the exact control being described, so that
there is no gap between the explanation and the thing explained.

#### Acceptance Criteria

1. WHEN the model returns a coordinate THEN THE system SHALL clamp it into the declared resolution,
   scale it to physical pixels, add the monitor origin, and emit it with that monitor's descriptor.
2. THE system SHALL hide the thinking spinner **before** emitting the pointer target, so that the
   overlay never paints a spinner and a flying pointer at the same time.
3. WHERE the question is conceptual, THE system SHALL place no pointer.
4. IF no screenshot was taken for this turn THEN THE system SHALL discard any coordinate the model
   returned, because a pointer placed from an unseen screen is invention.
5. THE pointer SHALL fly along a quadratic Bézier arc, dwell for 3 seconds, and then return to the
   mouse and resume following it.
6. WHERE grounding refinement is enabled, THE system SHALL verify a candidate coordinate against a
   native-resolution crop, and SHALL keep the original coordinate when verification returns nothing
   or falls outside the crop.

### Requirement 7: Cancellation

**User Story:** As a user, I want to abandon an answer I no longer want, so that a misheard question
does not cost me a full spoken response.

#### Acceptance Criteria

1. WHEN Escape is pressed WHILE a response is in flight THEN THE system SHALL abort the turn.
2. THE system SHALL treat a response as in flight WHILE the pipeline worker is alive OR
   text-to-speech is still speaking.
3. WHERE no response is in flight, THE system SHALL leave Escape entirely alone, because Escape is
   among the most-pressed keys on the keyboard.
4. WHEN a turn is cancelled THEN THE system SHALL stop speech, set the TTS grace window, hide the
   spinner, clear annotations, clear the caption, and return the state to idle.
5. WHEN a turn is cancelled THEN THE system SHALL write nothing to memory, because recording a
   rejected partial answer would pollute per-app history.
6. THE pipeline worker SHALL check for cancellation at every point where continuing would either
   spend money or produce a visible side effect, and SHALL NOT emit a pointer, annotations or a
   memory record after cancellation.
7. WHEN a new turn begins WHILE a previous worker is alive THEN THE system SHALL cancel that worker
   before clearing the overlay, so that it cannot repaint after the clear.

### Requirement 8: Capture geometry is faithful

**User Story:** As a user with an unusual monitor, I want Nimbus to point accurately on my hardware,
so that the product works on the screen I actually own.

#### Acceptance Criteria

1. THE system SHALL choose a capture resolution whose aspect ratio is closest to the source monitor's.
2. THE system SHALL never upscale, and SHALL check that before the aspect test, so that a 5:4 panel
   returns its native size rather than being routed through the fallback for a 6% drift.
3. IF the closest candidate's aspect ratio drifts more than 5% from the source THEN THE system SHALL
   compute an aspect-preserving size using one uniform scale factor.
4. THE returned scale factors SHALL be equal on both axes, so that geometry reaching the model is
   faithful and circular controls do not arrive as ellipses.
5. THE system SHALL sort captures so that the monitor containing the cursor is first.
6. THE system SHALL label each capture with its index, its total, whether it holds the cursor, and its
   exact pixel dimensions.
7. THE system SHALL resolve the monitor containing a point using half-open rectangles, and SHALL fall
   back to the primary monitor for a point in an inter-monitor dead zone.

### Requirement 9: Single instance and clean shutdown

**User Story:** As a user, I want one Nimbus, so that one hotkey press produces one answer.

#### Acceptance Criteria

1. THE system SHALL acquire a named mutex before constructing the Qt application.
2. IF the mutex already exists THEN THE system SHALL signal the running instance to show its window
   and SHALL exit with status 0.
3. THE system SHALL declare explicit ctypes argument and return types for every Win32 call, so that a
   64-bit handle is not truncated to an int.
4. THE system SHALL provide exactly one shutdown path, reached from both the tray menu and the
   Account page, which stops speech, transcription and the hotkey listener before quitting.

### Requirement 10: Per-interaction diagnostics

**User Story:** As the maintainer, I want to know what actually happened in a turn that went wrong,
so that a bug report is answerable.

#### Acceptance Criteria

1. WHERE diagnostic capture is enabled, THE system SHALL write one folder per turn containing a
   millisecond-stamped log and the screenshots the model was given.
2. THE system SHALL log the providers actually used for speech, model and voice on every turn, rather
   than a hardcoded label.
3. THE system SHALL log the capture decision and its reason, the recalled memory size, the knowledge
   base size, the parsed coordinate, and the moment the first audible chunk played.
4. WHERE diagnostic capture is disabled, THE system SHALL substitute a no-op session exposing the same
   interface, so that no caller needs an error path.
5. THE system SHALL delete diagnostic folders older than the retention window on startup, and SHALL
   treat a locked file as a skip rather than an error.
6. THE system SHALL log any stripped malformed coordinate tags, so that a model drifting off-format
   stays diagnosable rather than failing silently.

### Requirement 11: Speech-to-speech is a parallel pipeline, not a client

**User Story:** As a user who wants a conversation rather than a query, I want a mode where Nimbus
answers with no transcription step in the middle, so that the reply comes back at speaking speed.

> **Specified after the fact.** `gemini_live.py` and `realtime.py` shipped behind default-off toggles
> before this requirement was written. It records the contract they already satisfy, because two live
> runtime modules with no owning requirement is how a lifecycle decision gets reversed by accident.

#### Acceptance Criteria

1. THE speech-to-speech paths SHALL NOT implement the `AIClient` interface, and the reason SHALL be
   recorded: they collapse speech recognition, the model and speech synthesis into one socket, so there
   is no intermediate text to sentence-chunk and no separate transcript to hand onward.
2. THE paths SHALL expose exactly `connect`, `start_turn`, `respond`, `stop` and `close`, so that the
   orchestrator needs no new branching to drive them.
3. THE paths SHALL be off by default, and SHALL be selectable at runtime through a setting rather than
   a rebuild.
4. WHEN a speech-to-speech path is active THEN THE system SHALL NOT open the streaming recogniser, and
   the reason SHALL be recorded: both grab the same input device, and when both hold it neither works
   and neither reports a useful error.
5. THE Privacy Guard SHALL apply to this path through the same choke point as every other capture, so
   that a voice turn cannot send a password manager to a cloud provider.
6. THE Vertex backend switch SHALL apply identically here, so that voice and text cannot land on
   different endpoints.
7. IF the socket closes mid-turn THEN THE system SHALL return to idle rather than wait indefinitely,
   and SHALL leave the next turn unaffected.
8. WHEN a turn is cancelled THEN THE system SHALL stop the socket's playback on the same path as
   text-to-speech cancellation, so that Escape means the same thing in both modes.
9. Both modules SHALL appear in **both** the bundler's hidden-import list **and** the selftest's
   runtime module list, and the reason SHALL be recorded: `gemini_live` shipped broken through exactly
   this gap, because a module imported inside a function behind a default-off toggle is invisible to
   the static graph and to the selftest at once.

### Requirement 12: The speech provider stack is a factory, and one path needs no key

**User Story:** As a user with no API keys and no reliable connection, I want the whole turn to run on
my own machine, so that Nimbus is usable before I have signed up to anything.

> **Specified after the fact.** Requirements 4 and 5 define what speech in and speech out must *do*
> and deliberately name no provider. This records the concrete stack behind those interfaces, which
> shipped across Tier 0 and Tier 1.

#### Acceptance Criteria

1. THE system SHALL resolve both the speech-recognition and the speech-synthesis implementation from a
   settings string through a factory, so that adding a provider touches one function.
2. THE system SHALL support streaming cloud recognition and a fully local recogniser behind the same
   interface, with no call-site difference.
3. THE system SHALL support two streaming cloud voices and one fully local voice behind the same
   interface.
4. THE fully local combination — local model, local recogniser, local voice — SHALL require no API key
   and no network, and SHALL be treated as a **regression gate** on every model-layer change rather
   than as a fallback.
5. THE local voice's model files SHALL be downloaded on first use rather than bundled, and the reason
   SHALL be recorded: they are roughly 336 MB, which is larger than the rest of the installer.
6. THE download location SHALL be a documented path under the user's Nimbus directory, so that a user
   can delete it and reclaim the space.
7. WHERE a provider pays a first-call cost, THE system SHALL warm it on a background thread at
   startup, so that the cost is not charged to the user's first turn.
8. A warmup failure SHALL be silent and SHALL NOT prevent startup, because a warmed provider is an
   optimisation and an unwarmed one still works.
9. THE system SHALL report the provider actually used on every turn rather than a hardcoded label, so
   that "which voice was that" is answerable from the diagnostic log.

### Requirement 13: Capture resolutions are 4K-aware

**User Story:** As a user on a 4K monitor, I want small icons to survive the trip to the model, so
that the pointer lands on the control rather than near it.

> **Not built — `T4-6`.** The candidate resolution list caps at 1920×1200, so a 3840×2160 panel is
> downscaled by roughly 2× and small-icon detail is lost. This is the reason the native-resolution
> refinement crop (Requirement 6.6) exists at all. Recorded risk: raising the cap costs tokens on
> every turn, so the decision waits on `T1-8` measurement data rather than being taken on intuition.

#### Acceptance Criteria

1. THE candidate resolution list SHALL include at least one size that does not downscale a 3840×2160
   panel by more than 1.5×.
2. THE choice SHALL continue to honour Requirement 8: never upscale, aspect ratio closest first, and
   equal scale factors on both axes.
3. THE additional cost SHALL be measured before the larger candidate becomes a default, and the
   measurement SHALL be recorded next to the decision.
4. WHERE the larger candidate is not selected, behaviour SHALL be byte-identical to the current
   selection, so that this change cannot regress any existing monitor.
5. THE refinement crop SHALL remain in place regardless, because a higher capture ceiling reduces the
   need for it without removing it.

### Requirement 14: Language is detected, and the voice matches it

**User Story:** As a user who switches language mid-sentence, I want the answer to come back in the
language I asked in, so that I do not have to change a setting to be understood.

> **Not built — `T4-2`.** Recorded risks: the detection library's licence has to be checked before it
> becomes a dependency, and voice identifiers are per-provider, so a detected language maps to a
> different string on each of the three synthesis backends.

#### Acceptance Criteria

1. THE system SHALL detect the language of the final transcript rather than requiring the user to
   declare it.
2. THE system SHALL select a voice matching the detected language, per provider.
3. IF no voice exists for a detected language on the active provider THEN THE system SHALL fall back
   to the configured default voice and SHALL log the substitution, rather than failing the turn.
4. THE detection dependency's licence SHALL be recorded in the technology notes before it is added.
5. THE voice mapping SHALL live in one place per provider, so that a missing language is visible as a
   missing entry.
6. WHERE detection is unavailable or disabled, THE system SHALL behave exactly as it does today.

### Requirement 15: Tiny text has an optical fallback

**User Story:** As a user asking about fine print, I want Nimbus to read text the vision model cannot
resolve, so that "what does this say" works at any size.

> **Not built — `T4-4`.** Recorded risk: this may be obviated entirely by `T1-3` agentic vision, so
> the instruction on it is **measure first**. Building both would be paying twice for one capability.

#### Acceptance Criteria

1. THE system SHALL be able to extract text from a capture region without a model call.
2. THE fallback SHALL be attempted only when the model's own answer indicates it could not resolve the
   text, so that the common case pays nothing.
3. THE extracted text SHALL be attributed as machine-read in the answer, so that a misread is
   distinguishable from a model claim.
4. THE decision to build this SHALL be taken against measured evidence that agentic vision does not
   already cover it, and that measurement SHALL be recorded.
5. WHERE the fallback is disabled, THE system SHALL behave exactly as it does today.
