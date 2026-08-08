# Invariants that must not break

Every entry has cost real debugging time or would. Before any change, identify which of these it
touches. If it touches one, the manual smoke test in `testing.md` is mandatory, not optional.

## Pipeline and threading

| # | Invariant | Enforced by | Symptom if broken |
|---|---|---|---|
| 1 | **Only `pyqtSignal` crosses a thread boundary.** A worker thread never calls an overlay or widget method directly. | 26 signals on `NimbusApp` | Random crashes and Qt "not thread-safe" aborts, not clean failures |
| 2 | **Overlays hide before every `mss.grab()`.** `sig_hide_overlay` -> `_wait_for_compositor()` -> grab -> `sig_show_overlay` in a `finally`. | `NimbusApp._capture_screens_guarded`, the single choke point | The model sees Nimbus's own pointer and points at it |
| 3 | **The chat panel is never captured.** `WDA_EXCLUDEFROMCAPTURE` (0x11) via `SetWindowDisplayAffinity`. | `chat_hud.exclude_from_capture`, re-applied on every `showEvent` | The model reads its own previous answer as if it were the application |
| 4 | **Cancel is honoured at all 11 checkpoints** in `_pipeline_worker`. | `if cancel.is_set(): return`, each with a comment naming its race | Stale pointer, stale annotations, or memory written for an abandoned turn |
| 5 | **`suppress=False` on the keyboard hook.** | `PushToTalkHotkey.start` | pynput's flag is global: `True` blocks *every* key on the system |
| 6 | **Single instance only**, via a named mutex acquired *before* `QApplication`. | `_acquire_single_instance_mutex` | N voices answer one question |
| 7 | **The press-time capture result is published as one atomic write** under `_press_lock`. | `_read_press_state` / `_write_press_state` | A reader sees captures without their matching memory context |

## Coordinates and rendering

| # | Invariant | Enforced by | Symptom if broken |
|---|---|---|---|
| 8 | **Per-screen `devicePixelRatio()`, never a cached global.** | `physical_to_local_logical` | Pointer lands wrong on one screen of a mixed-DPI pair |
| 9 | **One overlay window per physical monitor.** | `OverlayController.__init__` | Overlay renders at the wrong size |
| 10 | **Positions transform, lengths only scale.** | `annotations_to_local` | Circles and boxes the wrong size, or offset by a screen origin |
| 11 | **Win32 ex-styles are OR'd after `show()`, never assigned.** Bit pattern `0x080800A8`, with `SWP_FRAMECHANGED`. | `apply_clickthrough_styles`, pinned by `test_clickthrough_flags_bit_pattern` | Overlay eats mouse clicks and the app is unusable |
| 12 | **`scale_x == scale_y` in every `CaptureResult`.** | `capture._aspect_preserving_size` | An ultrawide is squashed and small targets are missed |

## Speech and text

| # | Invariant | Enforced by | Symptom if broken |
|---|---|---|---|
| 13 | **Coordinates are never spoken.** Tags are stripped before the tail flush, and stripping is fail-closed for truncated tags. | `parse_point_tag`, `parse_annotations`, `strip_non_speech`, the `"[" in sentence_buffer` streaming guard | Nimbus reads "open bracket POINT colon four hundred" aloud |
| 14 | **Nothing machine-shaped reaches TTS on the structured path.** Code fences, prose tool calls, LaTeX and markdown are stripped. | `ai.strip_non_speech` | "dollar f of x equals x caret three backslash sin" |
| 15 | **The speech call declares no tools.** Measured: a tool-enabled Gemini call returns a pointer and total silence. | `gemini_native._build_config(with_tools=False)` for speech | The user holds a hotkey, asks a question, and hears nothing |
| 16 | **A base prompt is only ever appended to, never replaced.** | `prompts.apply_app_addendum`, `GeminiNativeClient._select_system_prompt` prefix matching | The persona, the write-for-the-ear contract and the pointing rules vanish silently |

## Privacy

| # | Invariant | Enforced by | Symptom if broken |
|---|---|---|---|
| 17 | **A privacy-suppressed screenshot is never written to disk.** Checked first, before the settings check. | `sessions.SessionStore.save_screenshot` | The chat store silently undoes the Privacy Guard while the user believes they are protected |
| 18 | **The Privacy Guard fails *open*.** Blocking requires positive identification; `"unknown"` never blocks. | `privacy.should_skip_capture` | A transient Win32 hiccup makes Nimbus look randomly broken |
| 19 | **An empty capture list means "no screenshot", never "abort".** The turn continues voice-only, and no coordinate is placed. | `_capture_screens_guarded` and the `voice_only` branch | Either the user loses their answer, or a pointer is placed from pure invention |
| 20 | **A guard reason string is user-presentable** — never a regex, a path or an exe name. | `test_reason_string_is_user_presentable` | A toast leaks a blocklist into a screenshot |

## Shell, panel and licence

| # | Invariant | Enforced by | Symptom if broken |
|---|---|---|---|
| 21 | **Closing the window hides it. It never quits.** Quitting is the tray's Quit and the Account page, both through one shutdown path. | `MainWindow.closeEvent`, `sig_quit` | Closing a window stops push-to-talk on a background tool |
| 22 | **"New chat" clears `_history`, not just the view** — in place, in the same call. | `sessions.start_new_session` / `switch_session` | "Zero context" is a lie: the model still gets the last ten exchanges |
| 23 | **`_pipeline_worker` gains no UI dependency.** It must stay testable with no `QApplication`. | no `shell` or `chat_hud` import in the worker | The pipeline cannot be tested without the whole application |
| 24 | **The pipeline never blocks on the panel.** Every public HUD entry point is wrapped. | `chat_hud._never_raises` | A rendering bug costs the user their answer instead of their transcript |
| 25 | **Three views of push-to-talk state, one source.** Only `set_listening` writes `hotkey.enabled`. | `sig_listening_changed`, `PowerToggle.set_on` blocking signals | The window says on, the tray says paused, and neither is trustworthy |
| 26 | **The licence gate runs after `QApplication` and before the hotkey listener and the microphone.** | `__main__`, calling `_run_startup_licence_gate` | An unlicensed instance claims the user's devices |
| 27 | **A licence *evaluation* failure is not a lockout.** The gate's caller catches `Exception` and starts anyway, logging to a file. | `_record_licence_gate_failure` | A legitimate user is locked out by our bug |
| 28 | **`revalidate()` never clears a good licence on a network error.** Only a 4xx clears anything. | `licensing.revalidate` | A service outage becomes the user's lockout |

## The regression gate

| # | Invariant |
|---|---|
| 29 | **The fully-local path keeps working**: Ollama + faster-whisper + Kokoro, no keys, no network. Every model-layer change is measured against this. |
| 30 | **Any lazily-imported module is registered in BOTH `nimbus.spec` `hiddenimports` AND `app._run_selftest`'s `runtime_modules`.** A module behind a default-off toggle is invisible to PyInstaller's static graph *and* to the selftest, so it fails first in a frozen build on someone else's machine. `gemini_cache` and `gemini_live` both slipped through this exact gap. |
