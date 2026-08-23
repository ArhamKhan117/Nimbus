# Nimbus — Improvements Backlog & Implementation Plan

> **Purpose.** Canonical context anchor for extending Nimbus. Records the current
> architecture as verified by reading every source file, the strategic decision driving
> this work, engineering standards every change must meet, and a tiered, ID'd backlog where
> **each item carries its own pre-flight verification, exact change list, and test plan.**
>
> Reference items by ID (`T0-1`, `T1-3`, …) in branches, commits, and conversations.
>
> **Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[-]` rejected/deferred
> **Verification legend:** `⚠ VERIFY` = must be confirmed against live sources *immediately
> before* implementing. Do not code from the assumption written here.
>
> **Baseline at last full audit (2026-08-08):** 477 tests passing · `--selftest` OK ·
> ~7,500 LOC across 19 runtime modules · 225 git objects at clone.
>
> **After Tier 0:** 558 tests passing (+81) · `--selftest` OK · zero regressions · complete.
>
> **Tier 1 closed (2026-08-09):** **757 tests passing** (+199 over Tier 0) · `--selftest` OK ·
> zero regressions · native Gemini path live-verified end to end.
>
> Every Tier 1 capability is now **built and reachable from the Settings dialog** (§4.3),
> all defaulting OFF. Nothing is left as an unreachable scaffold.
>
> - **Built, on by default:** `T1-1` native client, `T1-2` structured geometry,
>   `T1-6a` KB context caching, `T1-7` thinking budgets, `T1-9` split speech/geometry calls.
> - **Built, user-togglable, OFF by default:** `T1-3` Agentic Vision (live-verified),
>   `T1-4` Gemini Live, `T1-5` Search grounding, `T1-6b` code execution.
> - **Not built:** `T1-6c` Files API — no consumer until `T3-2`.
> - **Skipped by decision:** `T1-8` grounding harness — tooling complete, measurement
>   judged not worth the time (see item).
>
> `T1-5` is built but **not recommended**: it measured *worse* under Nimbus's own prompt
> (§4.1). Its tooltip says so.
>
> **Tier 2 closed (2026-08-12):** **988 tests passing** (+231 over Tier 1) · `--selftest` OK ·
> zero regressions · verified on real hardware.
>
> - **Done:** `T2-8` aspect-correct capture (unplanned, largest accuracy win — §5.0),
>   `T2-5` Code Mode, `T2-2` Esc to cancel, `T2-1` Privacy Guard (**ON** by default),
>   `T2-4` history screenshots (off by default), `T2-7` hotkey capture widget.
> - **Deferred by decision:** `T2-6` overlay flicker (cosmetic payoff, highest-risk code),
>   `T2-3` multi-step lessons (Tier 3 effort). Both specified in full — see §5.9.
>
> Two bugs were caught by *the new tests themselves* before shipping: a Privacy Guard rule
> that blocked any page merely mentioning passwords, and a `.env` pattern that missed
> `config.env`. One was caught by pre-flight reasoning: appending a Code Mode addendum would
> have silently disabled pointing on the native Gemini path (§5.5).
>
> **Tier 3 / Tier 4 partial (2026-08-17):** **1174 tests passing** (+96) · `--selftest` OK ·
> zero regressions.
>
> - `T4-5` **Live captions** — done, ON by default (§7.1). The audit's "consumed by nothing"
>   was half wrong: the callback was wired to a `print()` reaching a console a windowed build
>   does not have.
> - `T3-5` **Richer annotations** — done. `Rect`, `Highlight`, `StepBadge` on both the tag and
>   tool paths. **Uncovered a latent bug:** `Rect` from `T1-2` was silently discarded by both
>   coordinate transforms, so `draw_box` had never rendered anything.
> - `T3-3` **Knowledge Journal** — done, ON by default. Spaced repetition with
>   *positional* review items, which is the part no flashcard app can do.
> - `T4-7` **Restart labels** — done, labelling half only. Live reload stays open as `T4-7b`.
> - `T4-1` **Free TTS** — **already complete before the audit was written.** Kokoro is local
>   and keyless; the fully-keyless path already shipped. Removed from the backlog.
> - `T3-1` **Gated Computer Use** — 🚩 **skipped outright**, now a §8 non-goal.
> - `T3-6` deferred (a different approach is planned); `T3-4`, `T3-7`, `T3-8` deferred with
>   reasons at their items.
>
> **`T3-2` Knowledge base expansion — done (2026-08-18).** Folders plus PDF/DOCX via local
> text extraction, and relevance ranking instead of blind tail-truncation. The verify step
> found a real sanitiser drift between `kb.py` and `memory.py` (7 of 15 inputs disagreed).
> `T1-6c` Files API turned out **unnecessary** rather than outstanding. Follow-up work closed
> the discoverability gap (the guide is now seeded from code and reachable from Settings)
> and fixed a UI regression from earlier tiers: the Settings dialog had grown past a
> 1366x768 laptop, putting Save off-screen on a first-launch modal.
>
> Remaining: `T4-7b` live reload, and the low-priority tail of Tier 4 (`T4-2`, `T4-3`,
> `T4-4`, `T4-6`, `T4-8`). No substantial feature work outstanding.

---

## Table of contents

| § | Section | Read when |
|---|---|---|
| [0](#0-strategic-decision--read-first) | **Strategic decision** | Before anything else |
| [1](#1-engineering-standards) | **Engineering standards** | Before writing any code |
| [2](#2-architecture-reference) | Architecture reference | When touching unfamiliar modules |
| [3](#3-tier-0--stabilise) | **Tier 0 — Stabilise** (full specs) | Starting now |
| [4](#4-tier-1--model-layer) | Tier 1 — Model layer | After Tier 0 |
| [5](#5-tier-2--product-wins) | Tier 2 — Product wins | After Tier 1 |
| [6](#6-tier-3--depth--differentiation) | Tier 3 — Depth | Later |
| [7](#7-tier-4--polish) | Tier 4 — Polish | Opportunistic |
| [8](#8-explicit-non-goals) | Explicit non-goals | When tempted |
| [9](#9-roadmap) | Roadmap & sequencing | Planning |
| [10](#10-appendix--test-infrastructure) | **Test infrastructure + known conflicts** | Before every PR |

---

## 0. Strategic decision — READ FIRST

### 0.1 The decision

**Keep the multi-provider architecture. Add native Gemini as a genuinely first-class
provider. Spend the majority of remaining effort on Tier 2 and Tier 3, because those
improvements survive any model swap.**

The `AIClient` / `STT` / `TTS` abstract bases plus the `create_*_client()` factories are
Nimbus's most valuable asset. They make a new provider *one class and one factory branch*.
Every model-layer item in Tier 1 is cheap **because** of that seam. Do not bypass it, do not
special-case a vendor inside `app.py`, and do not let any provider become load-bearing for
core behaviour.

### 0.2 What this means concretely

| Do | Don't |
|---|---|
| Add `GeminiNativeClient` alongside existing clients | Replace `GeminiClient`/OpenRouter — keep both paths |
| Put vendor-specific logic behind the `AIClient` interface | Put `if provider == "gemini"` in `_pipeline_worker` |
| Make native capabilities *optional enhancements* with graceful fallback | Make any provider mandatory for a core feature |
| Measure before adopting a vendor-specific feature (`T1-8`) | Adopt on the strength of a vendor blog post |
| Invest in Tier 2/3 (privacy, cancel, lesson state, spaced repetition) | Treat model swapping as the product |

**Every Tier 1 item must degrade gracefully.** If a user runs Ollama offline with
`faster-whisper` + Kokoro, Nimbus must still work exactly as it does today. That fully-local
path is a genuine differentiator and is a regression gate on all model-layer work.

### 0.3 Honest accounting of the Gemini recommendations

Recorded so this is not mistaken for vendor enthusiasm. Roughly **60% genuine improvement,
40% positioning.**

| Item | Genuinely better? | Honest note |
|---|---|---|
| `T1-2` Structured output | **Yes, strongly — provider-agnostic** | The real insight. Coordinates should never share a channel with speech. OpenAI strict function calling and Anthropic tool use do this too. Gemini's edge is that spatial pointing is a *trained capability*, not just a serialisation format. |
| `T1-8` Measure, don't cite | **Yes — provider-agnostic** | Prerequisite for honest decisions anywhere. |
| `T1-9` Split grounding/conversation | **Yes — provider-agnostic** | Possibly the most interesting architectural idea here. Nothing to do with any vendor. |
| `T1-1` Native SDK | **Yes** | Two hops through a translation layer exposing only the API intersection is worse engineering regardless. |
| `T1-7` Thinking budgets | **Yes — provider-agnostic idea** | Anthropic has extended thinking; OpenAI has reasoning effort. Tiering compute by query class is the valuable part. |
| `T1-3` Agentic Vision | **Still unproven** | Built and working, ships off. It genuinely *replaces* the hand-rolled crop pass — but "replaces" only becomes "improves" with measurement, and `T1-8` was skipped. Rating unchanged from *probably*; do not upgrade it on the strength of it merely functioning. |
| `T1-4` Live API | **Neutral** | Built, ships off, audio not yet verified. Comparable to OpenAI Realtime. The real argument is that `realtime.py` is already broken, so a rewrite costs the same either way. Being built does not make it more valuable than it was. |
| `T1-5` Search grounding | **Convenience, not exclusivity** | Anthropic has web search; any search API works. Gemini bundles grounding + citations in one request. |
| `T1-6a` Context caching | **Parity, not advantage** | Anthropic's breakpoint model is arguably more elegant and is already implemented. This is about not regressing on Gemini. |

### 0.4 Positioning that is both true and competitive

> *Nimbus uses native structured spatial grounding because it is the right tool for
> pixel-accurate UI targeting — and ships the benchmark harness that proves it on real
> Windows screens at real DPI.*

Measured, not asserted. `T1-8` is what makes that sentence honest, which is why it sits in
Phase 2 ahead of every expensive model-layer decision.

---

## 1. Engineering standards

**These apply to every item in this document. No exceptions.**

### 1.1 Branching and commits

```powershell
git checkout -b t0-1-anthropic-model-id     # one branch per item ID
```

- **One item per branch, one item per PR.** `T0-*` items may be batched into a single
  `tier-0-stabilise` branch since they are small and interdependent — but each gets its own
  commit.
- Commit message format: `T0-3: accept signed coords, drop $ anchor, fail-closed strip`
- Never mix a bug fix and a feature in one commit.
- Never commit `.env`, `debug_*.jpg`, `exports/`, or anything under `~/.nimbus/`.
  `.gitignore` already covers these — verify with `git status` before every commit.

### 1.2 Definition of Done

An item is **not** done until every box is checked:

- [ ] Pre-flight verification steps for the item completed (see item's `⚠ VERIFY` block)
- [ ] Implementation matches the item's documented change list
- [ ] New tests written **and failing before the fix, passing after** (prove they test something)
- [ ] Any listed "tests to update" updated, with a comment explaining *why* the expectation changed
- [ ] Full suite green: `1252 + N` passing, **zero** regressions (1252 is the current count)
- [ ] `python -m app --selftest` prints `SELFTEST OK`
- [ ] **Any lazily-imported new module registered in BOTH `nimbus.spec` `hiddenimports` and
      `app.py::_run_selftest()`'s `runtime_modules`.** A module imported only behind a
      default-off toggle is invisible to PyInstaller's static graph *and* to the selftest, so it
      fails first in a user's frozen build. `gemini_cache` and `gemini_live` both slipped through
      this exact gap in Tier 1.
- [ ] Manual smoke test performed (see §1.5)
- [ ] Item's status flipped to `[x]` in this document, in the same commit
- [ ] If behaviour changed in a user-visible way: `README.md` updated

### 1.3 Backward-compatibility rules

Nimbus is a shipped app with users who have existing keyring entries, memory files, and
knowledge-base folders. Breaking any of these is a defect, not a migration.

| Rule | Why |
|---|---|
| **Never rename an existing keyring slot** | Silently loses the user's API key. Add new slots; read old ones as fallback. |
| **Never change the memory Markdown block shape** | `memory.py` parses from the first `## ` heading. A shape change orphans existing history. |
| **Never change the KB filename convention** | Users have hand-authored `<app>.exe.md` files. `kb._sanitize_app_name` must stay byte-identical to `memory._sanitize_app_name`. |
| **New settings must have a safe default** | An existing install has no keyring entry for a new key. Default must reproduce current behaviour. |
| **New `AIClient` methods must be optional** | Add to the ABC with a concrete default that delegates to existing behaviour, or `OllamaClient` and every future provider break. |
| **The fully-local path must keep working** | Ollama + faster-whisper + Kokoro, no network, no keys. This is a regression gate. |

### 1.4 Test conventions (match the existing suite exactly)

Verified against the then-current 1252 tests. There is **no `pyproject.toml`**, and there was no
`pytest.ini` either when this was written — pytest ran on defaults, and `tests/__init__.py` makes
`tests` a package so the repo root lands on `sys.path`.

> **Updated since.** A `pytest.ini` exists now, and it does one job: collection scope. It sets
> `testpaths = tests` and a `norecursedirs` that excludes `service`, because `service/` is a separate
> deployable whose package is *also* called `app` and would collide head-on with this repo's `app.py`
> on the same import path. The service suite is run from its own directory instead. No option below
> changed — everything else still runs on pytest's defaults.

`tests/conftest.py` exists but holds exactly **one** fixture, `first_run_config`, added in
Tier 1 for a specific reason documented in §4.3: settings that persist to the keyring cannot be
asserted by reading the imported `config` module, because that tests the machine rather than the
code. Keep it minimal — the suite's convention is self-contained tests with imports inside the
test body, and a growing conftest works against that.

```python
class TestParsePointTag:                        # Test<SubjectUnderTest>, grouped by unit
    """One-line statement of what this group guards."""

    def test_negative_x_coordinate_parses(self):
        from ai import parse_point_tag          # import INSIDE the test, not at module top
        result = parse_point_tag("here. [POINT:-3,50:save]")
        assert result.coordinate == (-3, 50)
```

Established patterns to follow:

- **Imports inside test methods.** Keeps import side effects (`config.py` runs
  `load_dotenv()` and touches keyring at import) contained and mockable.
- **`mocker` fixture** from `pytest-mock` for all patching. Patch at the *use* site
  (`mocker.patch("app.resolve_setting", ...)`), never the definition site.
- **Dependency injection over patching** wherever the code offers a factory hook:
  `client_factory`, `audio_stream_factory`, `overlay_factory`, `screens`, `cursor_pos_fn`,
  `connection_factory`, `mic_stream_factory`, `speaker_factory`, `listener_class`,
  `model_factory`, `player_factory`. **Any new external dependency must get a factory hook.**
- **No test touches** a real audio device, a real network call, a real `QWidget`, or the
  real `~/.nimbus/` directory. Use `tmp_path` for filesystem tests — `test_memory.py` and
  `test_kb.py` show the pattern.
- **Pure functions get direct unit tests.** Extract new math as a module-level pure function
  so it is testable without a `QApplication`. Existing precedent: `pick_resolution`,
  `unscale_model_coords`, `physical_to_local_logical`, `annotations_to_local`,
  `_bezier_position`, `_smoothstep`, `_waveform_bar_height`, `_spinner_angle_deg`,
  `parse_hotkey`, `parse_point_tag`, `parse_annotations`, `is_newer_version`.
- **Constants guarded against drift.** `test_overlay.py` asserts the exact
  `_CLICKTHROUGH_FLAGS` bit pattern `0x080800A8` so a typo in one Win32 constant cannot
  silently break click-through. Apply the same technique to any new bit-flag or magic number.
- **Regression tests name the bug** in their docstring. `TestResolveLLMCredentials` and
  `TestOllamaClientReviewerFixes` both do this. It is why the suite is maintainable.

### 1.5 Verification commands

Run all of these before declaring an item done.

```powershell
# 1. Full suite with .env neutralised — matches CI exactly
.\.venv\Scripts\python.exe -c "import dotenv,pytest,sys; dotenv.load_dotenv=lambda *a,**k:False; sys.exit(pytest.main(['-q']))"

# 2. Targeted run while iterating
.\.venv\Scripts\python.exe -m pytest tests/test_ai.py -v -k "point_tag"

# 3. Frozen-import check — catches a missing module long before a release build
.\.venv\Scripts\python.exe -m app --selftest        # must print: SELFTEST OK

# 4. Confirm test count went UP, never down
.\.venv\Scripts\python.exe -m pytest -q --collect-only 2>$null | Select-String "tests collected"
```

**Manual smoke test** (no automated test covers the real pipeline end to end):

1. Launch: `.\.venv\Scripts\python.exe -m app` — confirm tray icon appears, log shows
   `Listening for ctrl+alt+space...`
2. Hold the hotkey over a browser, ask *"where's the address bar?"* — confirm: chime plays,
   waveform shows, spinner shows, speech plays, blue cursor flies to the target and dwells.
3. Ask a conceptual question (*"what is HTTP?"*) — confirm speech, no pointer.
4. Re-press mid-response — confirm the old response is cancelled cleanly, no double audio.
5. Right-click tray → Settings opens; Quit exits with no orphaned `python.exe` in Task Manager.

### 1.6 Blast-radius discipline — what must never regress

Before any change, identify which of these it touches. If it touches one, the manual smoke
test is mandatory, not optional.

| Invariant | Enforced by | Symptom if broken |
|---|---|---|
| Coordinates never reach TTS | `parse_point_tag`, `parse_annotations`, tag-safety streaming guard | Nimbus speaks "open bracket POINT colon…" |
| Only `pyqtSignal` crosses thread boundaries | 18 signals on `NimbusApp` | Random crashes, Qt "not thread-safe" aborts |
| Overlays hide before every `mss.grab()` | `sig_hide_overlay` + 50 ms wait | Model points at Nimbus's own cursor |
| Per-screen `devicePixelRatio()`, never cached globally | `physical_to_local_logical` | Pointer lands wrong on mixed-DPI multi-monitor |
| One overlay window per physical monitor | `OverlayController.__init__` | Overlay renders at wrong size |
| Win32 ex-styles OR'd after `show()`, never overwritten | `apply_clickthrough_styles` | Overlay eats mouse clicks; app unusable |
| Single instance only | Named mutex before `QApplication` | N voices answer one question |
| `suppress=False` on the keyboard hook | `PushToTalkHotkey.start` | Every keystroke system-wide is blocked |
| Cancel is honoured at all 11 checkpoints | `_pipeline_worker` | Stale pointer/annotations/memory from an aborted turn |
| Fully-local path works with no keys | Ollama + faster-whisper + Kokoro | Offline users lose the app |

---

## 2. Architecture reference

### 2.1 Module map

Dependency order, top to bottom. Nothing below imports anything above it except through the
documented seams.

| Module | LOC | Responsibility | Key seam |
|---|---|---|---|
| `config.py` | 442 | Settings resolution `env → keyring → default`. All tunables. | `resolve_setting()`, `resolve_api_key()` |
| `version.py` | 6 | Build version, rewritten by release CI. | — |
| `capture.py` | 410 | mss grab, DPI awareness, resolution pick, coordinate unscaling. | `capture_all_screens()`, `unscale_model_coords()` |
| `annotations.py` | 95 | Shape-tag grammar + fail-closed stripping. | `parse_annotations()` |
| `locator.py` | 402 | Two-stage grid fallback, native-res crop refinement. | `locate_via_grid()`, `refine_point_via_crop()` |
| `ai.py` | 988 | `AIClient` ABC + 4 providers, prompts, `[POINT]` parse. | `create_ai_client()`, `ask_stream()` |
| `kb.py` | 93 | Drop-in per-app Markdown knowledge files. | `recall()` |
| `memory.py` | 433 | Per-app Markdown log + SQLite index. | `MemoryStore.recall()/record()` |
| `stt.py` | 676 | AssemblyAI streaming WS; local faster-whisper. | `STT` ABC, `create_stt_client()` |
| `tts.py` | 826 | Cartesia / ElevenLabs / Kokoro, double-buffered playback. | `TTS` ABC, `create_tts_client()` |
| `overlay.py` | 1244 | Per-monitor click-through overlays, all visuals. | `OverlayController` |
| `hotkey.py` | 384 | pynput observe-only global chord listener. | `PushToTalkHotkey`, `parse_hotkey()` |
| `realtime.py` | 345 | Parallel OpenAI Realtime speech-to-speech path. | `RealtimeSession` |
| `settings_dialog.py` | 794 | Progressive-disclosure BYOK UI. | `_PROVIDER_CATEGORIES` |
| `tray.py` | 143 | System tray icon + menu (only clean exit path). | `NimbusTray` |
| `onboarding.py` | 28 | One-time welcome dialog. | `WelcomeDialog` |
| `updates.py` | 56 | GitHub Releases check. | `check_for_update()` |
| `debug_log.py` | 99 | Opt-in per-interaction diagnostics with retention. | `DebugSession` |
| `ollama_health.py` | 118 | Ollama version/model compatibility probe. | `check_model_compatibility()` |
| `app.py` | 1925 | Orchestrator: signals, threads, pipeline worker. | `NimbusApp` |

Support: `tools/bench.py` (192 LOC, scipy Mann-Whitney U + bootstrap CI — **not** bundled
in the frozen build), `nimbus.spec` (PyInstaller), `installer/nimbus.iss` (Inno Setup,
per-user, no UAC), `.github/workflows/{tests,release}.yml`.

### 2.2 The three coordinate spaces

The intellectual core of the codebase, and implemented correctly.
**Any change touching coordinates must preserve these boundaries.**

| Space | Units | Origin | Owned by |
|---|---|---|---|
| **A** | Physical pixels, virtual desktop | Multi-monitor union top-left | `capture.py` |
| **B** | Qt logical / DIP pixels, **per-screen** | Each screen's own top-left | `overlay.py` |
| **C** | Model's declared resolution | Screenshot top-left | `ai.py` |

- `capture.py` owns **A ↔ C** via `unscale_model_coords()`: `clamp → × scale → + monitor origin`
- `overlay.py` owns **A ↔ B** via `physical_to_local_logical()`: `− screen origin → ÷ dpr`
- `ai.py` returns Space C coordinates **unclamped**

**Load-bearing invariants:**

1. **Never cache a global DPI ratio.** Always `screen.devicePixelRatio()`, per screen.
2. **One overlay window per physical monitor.** A spanning window renders wrong on mixed DPI in Qt 6.
3. **Overlays hide before every `mss.grab()`.** Otherwise the model sees Nimbus's own cursor.
4. **Lengths scale, positions transform.** Radius/width divide by the ratio only — no origin
   subtraction. See `annotations_to_local()`.

### 2.3 Threading model

- **Qt main thread** — all `overlay.py` calls, all `MemoryStore` writes, all dialogs.
- **pynput listener thread** — hotkey callbacks; only ever `pyqtSignal.emit()`.
- **portaudio callback thread** — mic chunks; touches only floats and byte buffers.
- **AssemblyAI WS thread** — transcript events; touches only `_final_transcript` / `_final_event`.
- **Worker threads** — `nimbus-pipeline`, `nimbus-press-capture`, `nimbus-release-capture`,
  `nimbus-update-check`, `stt-teardown`, TTS prefetch/playback pair.

**The rule: only `pyqtSignal` crosses thread boundaries.** Worker threads never call overlay
methods directly.

### 2.4 Pipeline sequence

```
PRESS (Qt main thread)
 ├─ cancel prior worker · clear spinner · clear annotations · tts.stop()
 ├─ stt.set_tts_grace_until(now + 200ms)      ← acoustic feedback guard
 ├─ chime (numpy-generated, async)
 ├─ get_foreground_app()  (ctypes Win32)
 ├─ stt.start_recording()                     ← <1ms; mic+WS pre-opened at startup
 ├─ spawn nimbus-press-capture ──► hide overlays · 50ms · grab all screens ·
 │                                 show overlays · memory.recall()
 └─ show waveform at cursor

RELEASE (Qt main thread)
 ├─ hide waveform · show spinner
 ├─ snapshot release cursor SYNCHRONOUSLY   ← so mouse motion can't flip reuse decision
 ├─ spawn nimbus-release-capture ──► reuse-vs-recapture (150px) ──► Queue(1)
 └─ spawn nimbus-pipeline

PIPELINE WORKER
 ├─ stt.stop_recording()          ~500ms  ─┐ parallel: wall clock = max(), not sum()
 ├─ capture_queue.get(timeout=5)           ─┘
 ├─ kb.recall() + memory prefix into user message
 ├─ ai.ask_stream()
 │    └─ per delta: flush complete sentences at [.!?]\s → tts.speak_sentence()
 │       STOP flushing the moment '[' appears          ← tag-safety guard
 ├─ final_result() → parse [POINT] / parse_annotations()
 ├─ optional refine_point_via_crop()   ← 2nd LLM call, 900px native-res crop
 ├─ unscale_model_coords() → sig_point_at.emit() → bezier flight + 3s dwell
 ├─ sig_record_memory.emit()
 └─ history append, trimmed to 10 exchanges
```

**Cancel is checked at 11 distinct points**, each with a comment naming the race it prevents.
Preserve every one.

### 2.5 Latency budget

Target: `config.E2E_LATENCY_BUDGET_S = 1.5` s to first audible word. Four implemented
optimisations:

1. **Press-time prefetch** — capture + memory recall overlap with the user still speaking.
2. **Sentence-level TTS streaming** — sentence 1 plays while the model generates sentence 3.
   Largest single win (~2 s perceived).
3. **Release-time parallel capture** — `max(STT, capture)` not `STT + capture`.
4. **Double-buffered TTS** — prefetch + playback workers, `Queue(1)`, epoch counter rejects
   stale responses that slip past a `stop()` drain.

Startup pays a one-time ~6 s cost pre-opening mic + WebSocket so press is <1 ms.

### 2.6 Data locations

| Path | Contents |
|---|---|
| Credential Manager, service `nimbus` | All API keys + settings + first-run flags |
| `~/.nimbus/memory/<app>.exe.md` | Per-app interaction log, human-readable |
| `~/.nimbus/index.db` | SQLite (WAL): app, first_seen, last_seen, count, md_path |
| `~/.nimbus/debug/<ts>_<app>/` | Opt-in diagnostics, retention-pruned (default 7 d) |
| `~/.nimbus/kokoro/` | Local TTS models (~336 MB, first use) |
| `~/Documents/Nimbus Wiki/<app>.exe.md` | User knowledge base, injected as authoritative |
| `~/Documents/nimbus-session-*.md` | Session history exports |

---

## 3. Tier 0 — Stabilise

**STATUS: ✅ COMPLETE.** All seven items implemented, tested, and verified.
477 → **558 tests passing**, zero regressions, `--selftest` OK, app launches and runs.
Actual effort matched the revised 1-day estimate.

### 3.0 What verification changed

The `⚠ VERIFY` discipline earned its place — it corrected the audit in four material ways
**before** any code was written. Recorded here because each correction is a lesson about the
audit process, not just about the code.

| # | Audit said | Verification found | Consequence |
|---|---|---|---|
| 1 | `T0-2`: **two** `LLM_PROVIDER` call sites | **Three** — `config.py:312`, `app.py:506`, `app.py:1796`. The audit missed the realtime check in `NimbusApp.__init__`. | Fixing only the two known sites would have left the divergence alive. A drift-guard test now counts call sites. |
| 2 | `T0-4`: `gpt-5.4` and `gpt-realtime-2` are **fictional** | **Both are real, live models**, confirmed against the account's own `models.list()`. `google/gemini-3.1-pro-preview` is also live on OpenRouter. | **The audit was wrong.** `T0-4` collapsed from "replace four fictional defaults" to "improve the startup log". Coding from the audit would have replaced three *working* defaults. |
| 3 | `T0-1`: root cause is a placeholder model id | Placeholder **plus** a deeper defect: `AnthropicClient` stored `model_id` verbatim while the two endpoints demand *different formats* (native `claude-sonnet-4-6` vs OpenRouter `anthropic/claude-sonnet-4.6`). **Both** key types were broken, not just the default. | Fixing only the default would have left Anthropic broken for every user. |
| 4 | **Two** existing tests encode the bugs | **Three.** `test_anthropic_provider_has_no_model_picker` asserted Anthropic must have *no* model picker — the direct cause of `ANTHROPIC_MODEL` being unwritable from the UI. | A documented "minimal UX" decision had to be consciously reversed, not silently broken. |

Corollary worth keeping: **the scrub hypothesis was confirmed.** `model-sonnet-4-6` and
`mid.startswith("model")` were both `claude` before a find-and-replace. Anywhere else the
literal `model` appears as a prefix test, suspect the same origin.

### 3.1 Order actually used

```
T0-5 (dead code) → T0-6 (docstring) → T0-2 (shared default) → T0-7 (race)
   → T0-3 (regex, 3 failure modes) → T0-1 (Anthropic) → T0-4 (startup log)
```

`T0-8` (rotate the exposed OpenAI key) has been **removed from this document** — it was an
operational task for the maintainer, not a code change, and is tracked outside this backlog.

---

### `[x] T0-1` — Anthropic provider is broken as shipped

> **DONE.** Root cause was deeper than the audit found: the placeholder model id *and* a
> slug-format mismatch that broke both key types. Shipped:
> - `config.DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"` (native dash-versioned form),
>   verified against OpenRouter's live list and Anthropic's dateless 4.6-generation format
> - New pure helper `ai._anthropic_model_for_endpoint()` adapting the slug per endpoint —
>   bare + dashes for `api.anthropic.com`, namespaced + dots for OpenRouter
> - `AnthropicClient.__init__` now routes through it, reaching parity with `GeminiClient`
> - Deleted the `startswith("model")` fossil; accepts `claude…` instead
> - Settings dialog gained the Anthropic model picker (`models` + `model_setting`),
>   so `ANTHROPIC_MODEL` is finally writable from the UI
>
> **Tests:** +7 `TestAnthropicModelForEndpoint`, +3 factory routing tests, +2 settings tests.
> **Updated:** 4 tests (2 factory, 1 credentials, 1 settings) with rationale comments.
> **Caveat, as the item required:** no Anthropic API key was available, so this is verified
> by construction and unit test, **not** against the live API. First live Anthropic call
> should be treated as unproven.

**Severity:** Critical — selecting Anthropic produces a failed request on every interaction.
**Blast radius:** `_resolve_llm_credentials`, `create_ai_client` routing, Settings dialog.
Does not touch the pipeline, overlay, or coordinate math.

#### ⚠ VERIFY before implementing

1. **Current Anthropic model IDs** — fetch <https://docs.anthropic.com/en/docs/about-claude/models>
   and note the exact current model string. Do **not** code from memory; these rotate.
2. **Whether an OpenRouter slug or a native slug is needed.** `create_ai_client` routes an
   `sk-or-` key to `https://openrouter.ai/api` (needs the namespaced `anthropic/…` slug) and
   an `sk-ant-` key to the SDK default (needs the **bare** model name). Confirm
   `AnthropicClient.__init__` does not strip the prefix — as read, it does **not**, unlike
   `GeminiClient`. This asymmetry is the likely root cause and must be resolved deliberately.
3. **Whether the user has an Anthropic key available** for an end-to-end confirmation. If
   not, the fix is still correct but ships unverified against the live API — note that in
   the PR.

#### Problem

`app.py::_resolve_llm_credentials()`:

```python
ant_model = resolve_setting("ANTHROPIC_MODEL", default="model-sonnet-4-6")
return f"anthropic/{ant_model}", ant_key
```

`model-sonnet-4-6` is a placeholder left behind when the repo was scrubbed of vendor model
names. Compounding it, `settings_dialog.py::_PROVIDER_CATEGORIES` defines the Anthropic
`_Provider` with **no `models` tuple and no `model_setting`** — so the dropdown never renders
for Anthropic, `ANTHROPIC_MODEL` is never written from the UI, and the broken default is the
only value ever used.

**Related fossil** in `ai.py::create_ai_client()`:

```python
if mid.startswith("anthropic/") or mid.startswith("model"):
```

`startswith("model")` is only coherent if it originally read `startswith("claude")`. As
written, any model ID beginning with the literal string `model` routes to `AnthropicClient`.

#### Change list

1. `app.py` — replace the default with the verified model ID from step 1.
2. `settings_dialog.py` — add to the Anthropic `_Provider`:
   `models=(...)`, `model_setting="ANTHROPIC_MODEL"`. Mirror the Ollama provider's shape.
3. `ai.py` — delete the `or mid.startswith("model")` branch. Add an explicit
   `mid.startswith("claude")` branch **only if** verification shows bare slugs reach the
   factory; otherwise `anthropic/` alone is sufficient.
4. `ai.py::AnthropicClient.__init__` — if step 2 of verification shows the prefix must be
   stripped for native keys, add the same dual-routing logic `GeminiClient` uses, and add a
   docstring noting the parity.

#### Tests to add — `tests/test_app.py::TestResolveLLMCredentials`

```python
def test_anthropic_default_model_is_resolvable(self, mocker):
    """T0-1: the default must be a real model ID, not the scrubbed placeholder."""
    # assert the returned model_id does NOT contain "model-sonnet"
    # assert it matches the verified current ID

def test_anthropic_model_setting_is_honoured(self, mocker):
    """T0-1: Settings dropdown must not be cosmetic — mirrors the existing
    ollama regression test that caught exactly this class of bug."""
```

`tests/test_ai.py::TestCreateAIClient`:

```python
def test_bare_model_prefix_no_longer_routes_to_anthropic(self):
    """T0-1: 'model...' must NOT silently route to AnthropicClient."""
    # expect ValueError for an unrecognised prefix like "modelfoo/bar"
```

`tests/test_settings_dialog.py`:

```python
def test_anthropic_provider_exposes_model_picker(self):
    """T0-1: Anthropic _Provider must define models + model_setting."""
```

#### Tests to update

`tests/test_app.py::TestResolveLLMCredentials::test_defaults_to_anthropic_path` — change the
asserted model ID. Add a comment: `# T0-1: was "anthropic/model-sonnet-4-6" (a scrubbed
placeholder that 404'd); now asserts the real default.`

#### Done when

Anthropic is selectable in Settings, shows a model dropdown, the chosen model persists to
keyring, and (if a key is available) a live interaction returns speech. `create_ai_client`
raises `ValueError` for genuinely unknown prefixes.

**What it improves:** restores one of four advertised providers from guaranteed failure to
working, and removes a silent mis-routing hazard every future provider would inherit.

---

### `[x] T0-2` — `LLM_PROVIDER` had three conflicting defaults

> **DONE.** Verification found **three** call sites, not two. Shipped:
> - `config.DEFAULT_LLM_PROVIDER = "openai"` as the single source of truth
> - All three sites now pass it: `config.py:312`, `app.py:506` (realtime check —
>   the one the audit missed), `app.py:1796` (`_resolve_llm_credentials`)
> - `app.py` still calls `resolve_setting` fresh, preserving the deliberate
>   apply-without-restart behaviour; only the *default* is shared
> - Confirmed `STT_PROVIDER` / `TTS_PROVIDER` / `ANNOTATION_MODE` do **not** have the same
>   divergence
>
> **Tests:** +4 in `TestProviderDefaultConsistency`, including a drift guard that
> regex-counts every call site so a newly added one cannot reintroduce a literal.

#### ⚠ VERIFY before implementing

1. `grep` for **every** `resolve_setting("LLM_PROVIDER"` call site. The audit found two;
   confirm there is no third.
2. Confirm the same divergence does not exist for `STT_PROVIDER` / `TTS_PROVIDER` /
   `ANNOTATION_MODE`. `config.py` defaults them to `assemblyai` / `cartesia` / `off`;
   `app.py::_resolve_stt_credentials` and `_resolve_tts_credentials` use `assemblyai` /
   `cartesia`. **These appear consistent — verify, and if any diverge, fix them in the same
   commit.**

#### Problem

| Location | Default |
|---|---|
| `config.py` | `resolve_setting("LLM_PROVIDER", default="openai")` |
| `app.py::_resolve_llm_credentials()` | `resolve_setting("LLM_PROVIDER", default="anthropic")` |

Same keyring slot, two fallbacks. Masked today because `.env` sets it and the first-run
dialog writes index 0 (`openai`) — but a fresh install where the user **cancels** the dialog
lands on the `anthropic` path, straight into `T0-1`.

#### Change list

1. `config.py` — add a module-level constant near the other provider defaults:
   ```python
   DEFAULT_LLM_PROVIDER = "openai"
   """Single source of truth. app.py imports this rather than repeating a literal —
   the two call sites previously disagreed (openai vs anthropic), so a cancelled
   first-run dialog silently selected a different provider than Settings showed."""
   ```
2. `config.py` — `LLM_PROVIDER = resolve_setting("LLM_PROVIDER", default=DEFAULT_LLM_PROVIDER)`
3. `app.py` — import it and use it. **Keep the fresh `resolve_setting` call** — `config.py`
   documents that `LLM_PROVIDER` is deliberately *not* imported as a frozen constant so
   Settings changes apply without restart. Only the default becomes shared.

`openai` is the correct value: `.env` uses it, and `_PROVIDER_CATEGORIES`' LLM category has
`default_index=0`, which is the OpenAI provider.

#### Tests to add — `tests/test_config_keyring.py`

```python
def test_llm_provider_default_is_shared(self, mocker):
    """T0-2: config and app must agree on the fallback with no env/keyring value."""
    # patch env empty + keyring returning None
    # assert config.resolve_setting("LLM_PROVIDER", config.DEFAULT_LLM_PROVIDER) == "openai"

def test_no_hardcoded_llm_provider_default_in_app(self):
    """T0-2 drift guard: app.py must not contain a literal provider default.
    Mirrors the _CLICKTHROUGH_FLAGS bit-pattern guard in test_overlay.py."""
    # read app.py source, assert 'default="anthropic"' not present
```

That second test is a **drift guard** in the style the suite already uses — it prevents the
divergence silently returning.

#### Done when

Both call sites resolve through `DEFAULT_LLM_PROVIDER`. A cancelled first-run dialog on a
clean keyring yields the OpenAI path.

---

### `[x] T0-3` — `[POINT]` parsing had three silent-failure modes

> **DONE.** Decision taken: **Option C — strip + record**, as recommended. Shipped:
> - `(-?\d+)` for both coordinates; `$` anchor removed; `finditer` takes the last match
> - Case-insensitive with whitespace tolerance, matching `annotations.py`'s leniency
> - Label group made whitespace-tolerant and normalised via `.strip() or None`
>   (a third failure mode found while testing: `[POINT:1,2: btn ]` did not parse)
> - Two new fail-closed patterns: `_MALFORMED_POINT_RE` (complete but unparseable) and
>   `_UNTERMINATED_POINT_RE` (truncated tag *and* everything after it)
> - `PointParseResult.malformed_tags` records the raw stripped text; `_pipeline_worker`
>   logs it to `DebugSession`, so stripping does not cost diagnosability
> - **Deliberately did not clamp** in `parse_point_tag` — Space C is unclamped by contract
>   and `unscale_model_coords` owns clamping. A test asserts both halves of that contract.
>
> **Tests:** +41. Centrepiece is `TestSpokenTextNeverContainsPointTag`, a 25-case
> parametrised assertion that no input in any casing or truncation state can put `[POINT`
> into `spoken_text`. **Proven meaningful:** replayed against the original regex, 6/6
> representative inputs leaked coordinates into speech; all pass now.
>
> **Updated:** `test_malformed_tag_returns_no_coordinate`, which asserted the leak.

**Severity:** High — both modes break pointing **and** leak coordinates into TTS, violating
the codebase's own stated hard invariant.
**Blast radius:** `parse_point_tag` is called by every provider and by
`refine_point_via_crop`. This is the highest-blast-radius Tier 0 item. Full smoke test
required.

#### ⚠ DECISION REQUIRED before implementing

`tests/test_ai.py` currently asserts the leak:

```python
def test_malformed_tag_returns_no_coordinate(self):
    result = parse_point_tag("broken tag [POINT:garbage]")
    assert result.coordinate is None
    assert "broken tag [POINT:garbage]" in result.spoken_text   # ← asserts the leak
```

Someone encoded this deliberately. Pick one:

| Option | Behaviour | Trade-off |
|---|---|---|
| **A — Strip (recommended)** | Malformed tags removed from `spoken_text` | Coordinates never spoken. Malformed tags fail silently. |
| B — Keep | Current behaviour | Debuggable, but the user hears `"open bracket POINT colon garbage"`. |
| **C — Strip + log (best)** | Strip from `spoken_text`, log the raw tag to `DebugSession` | Both properties. `dbg` is already in scope in `_pipeline_worker`. |

**Recommendation: C.** It satisfies the never-speak-coordinates invariant without losing
diagnosability, and `annotations.py` already set this precedent with
`_UNTERMINATED_TAG_RE`. Note that C requires threading an optional logger into
`parse_point_tag` — keep it optional (`debug_log: Callable | None = None`) so the function
stays pure-by-default and unit-testable.

#### ⚠ VERIFY before implementing

1. Re-read all of `tests/test_ai.py::TestParsePointTag` (~10 tests) to catalogue every
   currently-asserted behaviour. Confirmed present and **must keep passing**:
   - `test_point_none` — `[POINT:none]`
   - `test_no_tag_at_all`
   - `test_coordinates_with_spaces` — `[POINT:640 , 400:btn]`
   - `test_screen_number_without_label` — `[POINT:400,300:screen2]` → `screen_number=2`,
     **not** `label="screen2"`. The `(?!screen\d)` negative lookahead exists for this;
     **do not break it.**
2. Check `test_locator.py` for coupling to `parse_point_tag` via `refine_point_via_crop`.

#### Problem

```python
_POINT_TAG_RE = re.compile(
    r"\[POINT:(?:none|(\d+)\s*,\s*(\d+)(?::(?!screen\d)([^\]:\s][^\]:]*?))?(?::screen(\d+))?)\]\s*$"
)
```

**Mode 1 — unsigned digits only.** `(\d+)` cannot match a negative number. A model emitting
`[POINT:-3,50:save button]` (real, when an element sits at a screenshot edge) fails the whole
match; `parse_point_tag` returns the entire original text as `spoken_text`, tag included, and
TTS reads the coordinates aloud. Positive overflow is handled by clamping in
`unscale_model_coords`; negative is not handled at all.

**Mode 2 — anchored to end of string.** Trailing `\s*$` requires the tag be last. Any
trailing prose — `"...[POINT:400,300:save] hope that helps!"` — means no match, no strip,
tag spoken.

#### Change list

1. `(-?\d+)` for both coordinate groups.
2. Drop the `$` anchor. Use `finditer` and take the **last** match so a tag mid-response is
   still found, and trailing prose survives in `spoken_text`.
3. Add a fail-closed unterminated-tag strip mirroring `annotations._UNTERMINATED_TAG_RE`:
   ```python
   _UNTERMINATED_POINT_RE = re.compile(r"\[\s*POINT\s*:.*$", re.IGNORECASE | re.DOTALL)
   ```
4. Add a malformed-but-terminated strip so `[POINT:garbage]` is removed (option C above).
5. **Do not** clamp inside `parse_point_tag`. Space C is documented as unclamped;
   `unscale_model_coords` owns clamping. Preserving that boundary matters — a negative
   coordinate must survive to `unscale_model_coords` where `max(0, min(...))` handles it.

#### Tests to add — `tests/test_ai.py::TestParsePointTag`

```python
def test_negative_x_parses(self):                 # [POINT:-3,50:save] → (-3, 50)
def test_negative_y_parses(self):                 # [POINT:400,-12:tab] → (400, -12)
def test_both_negative_parse(self):
def test_trailing_prose_after_tag_is_stripped(self):
    # "here it is. [POINT:400,300:save] hope that helps!"
    # → coordinate == (400, 300); "[POINT" not in spoken_text
def test_unterminated_tag_fail_closed_stripped(self):
    # "look here [POINT:120,40" → coordinate is None; "[POINT" not in spoken_text
def test_malformed_terminated_tag_stripped(self):
    # "broken [POINT:garbage]" → coordinate is None; "[POINT" not in spoken_text
def test_multiple_tags_last_one_wins(self):
def test_lowercase_point_tag_stripped(self):      # parity with annotations.py leniency
def test_negative_coord_survives_to_unscale_unclamped(self):
    # parse_point_tag must NOT clamp — Space C is unclamped by contract
```

**Add the invariant test** — this is the one that matters most:

```python
@pytest.mark.parametrize("text", [
    "[POINT:none]", "[POINT:400,300:save]", "[POINT:-3,50:edge]",
    "[POINT:garbage]", "look [POINT:120,40", "[point:1,2:x]",
    "a [POINT:1,2:x] b [POINT:3,4:y] c", "no tag here at all",
    "[POINT:400,300:screen2]", "[POINT:640 , 400:btn]",
])
def test_spoken_text_never_contains_point_marker(text):
    """T0-3 HARD INVARIANT: no input may ever yield a spoken_text containing
    '[POINT' in any casing. This is the never-speak-coordinates guarantee."""
    from ai import parse_point_tag
    assert "[point" not in parse_point_tag(text).spoken_text.lower()
```

#### Tests to update

`test_malformed_tag_returns_no_coordinate` — flip the second assertion to
`assert "[POINT" not in result.spoken_text`. Comment:
`# T0-3: previously asserted the tag SURVIVED in spoken_text. That leaked coordinates to
TTS, violating the never-speak-coordinates invariant. Now fail-closed stripped.`

#### Done when

All new tests pass, the parametrised invariant test passes for every case, the four
pre-existing behaviours still pass, and the smoke test confirms pointing still works
end to end.

---

### `[x] T0-4` — Model defaults ~~fictional~~ (audit was wrong) + startup logging

> **DONE, but the premise was mostly incorrect — recorded honestly.**
>
> Verification against the account's live `models.list()` and OpenRouter's live model list:
>
> | Constant | Audit claim | Reality |
> |---|---|---|
> | `OPENAI_MODEL_VISION` = `gpt-5.4` | fictional | **real and live** |
> | `OPENAI_REALTIME_MODEL` = `gpt-realtime-2` | fictional | **real and live** |
> | `GEMINI_MODEL_VISION` = `google/gemini-3.1-pro-preview` | verify | **real and live** |
> | `ANTHROPIC_MODEL` = `model-sonnet-4-6` | broken | **genuinely broken** → `T0-1` |
>
> Three of four defaults were already correct. Coding straight from the audit would have
> replaced working values. The benchmark figures in `config.py` docstrings were left in
> place rather than deleted, since they refer to real models — they remain unverified, which
> is what `§11` exists to fix.
>
> What actually shipped: the startup log now names the resolved provider *and* model, plus
> the STT/TTS classes, and uses the configured `HOTKEY` instead of hardcoded text:
> ```
> [nimbus] LLM: provider=openai model=openai/gpt-4o
> [nimbus] STT: FasterWhisperSTT | TTS: KokoroTTS
> [nimbus] Listening for ctrl+alt+space... (Ctrl+C to quit)
> ```
> **Tests:** +4 in `TestModelDefaultIntegrity`, guarding the placeholder pattern that was
> real (`model-sonnet`) rather than asserting against models that turned out to exist.

#### ⚠ VERIFY before implementing — all four, against live sources

| Constant | Current default | Verify at |
|---|---|---|
| `OPENAI_MODEL_VISION` | `"gpt-5.4"` | <https://platform.openai.com/docs/models> |
| `OPENAI_REALTIME_MODEL` | `"gpt-realtime-2"` | OpenAI Realtime docs |
| `GEMINI_MODEL_VISION` | `"google/gemini-3.1-pro-preview"` | <https://openrouter.ai/models> (exact slug) |
| `ANTHROPIC_MODEL` (`app.py`) | `"model-sonnet-4-6"` | See `T0-1` |

Programmatic check (fastest, uses the key already in `.env`):

```powershell
.\.venv\Scripts\python.exe -c "import dotenv,os;dotenv.load_dotenv();from openai import OpenAI;print('\n'.join(sorted(m.id for m in OpenAI(api_key=os.environ['OPENAI_API_KEY']).models.list().data)))"
```

Also **delete the unverifiable benchmark claims.** `config.py` docstrings cite
"85.4% on ScreenSpot-Pro" and "84.4%" for models that do not exist under those names. Remove
them or replace with a pointer to `T1-8`'s measured results. Do not carry unverifiable
numbers in source comments.

#### Change list

1. Replace all four defaults with verified IDs.
2. Delete or re-source every benchmark figure in `config.py` docstrings.
3. Add a startup log line naming the resolved model, so a mismatch is visible immediately
   rather than as a failed request five seconds into the first interaction:
   ```python
   _log(f"LLM: provider={provider} model={model_id}")
   ```
   `app.py` already logs `Model: openai/gpt-4o` — extend it to include the provider and
   confirm it fires for **all** providers, not just the OpenAI path.

#### Tests to add — `tests/test_config_keyring.py`

```python
def test_no_placeholder_model_defaults(self):
    """T0-4 drift guard: no default may contain a known-fictional identifier."""
    import config
    forbidden = ("gpt-5.4", "gpt-realtime-2", "model-sonnet")
    for name in ("OPENAI_MODEL_VISION", "OPENAI_REALTIME_MODEL", "GEMINI_MODEL_VISION"):
        assert not any(f in getattr(config, name) for f in forbidden)
```

Note this guards against *these specific* placeholders returning. It cannot validate a model
exists — that requires a network call and does not belong in the unit suite.

#### Done when

A fresh clone with no `.env` starts and completes one interaction on the default provider,
given only a valid API key.

---

### `[x] T0-5` — Unreachable dead code in `_play_chime_async`

> **DONE.** Verification confirmed `_play_chime_async` had exactly one reference (its own
> definition) and `_CHIME_SAMPLES` only appeared inside the dead body — but
> `_CHIME_SAMPLE_RATE` is **live** inside `_play_feedback_tone_async`, exactly as the item
> predicted. Deleted the function and `_CHIME_SAMPLES`; kept `_CHIME_SAMPLE_RATE`; documented
> the deliberate error-swallowing in the surviving function's docstring.
>
> **Tests:** +7 in `TestFeedbackTones`, including the device-error swallow (the property that
> keeps a missing audio device from breaking push-to-talk), a distinctness check so the three
> cues cannot collapse into one, and drift guards asserting the dead alias stays gone and
> `_CHIME_SAMPLE_RATE` stays present.

#### ⚠ VERIFY before implementing

Confirmed during audit: **no test references `_play_chime_async`, `_CHIME_SAMPLES`, or
`_play_feedback_tone_async`.** Re-confirm before deleting:

```powershell
Select-String -Path tests\*.py -Pattern '_play_chime_async|_CHIME_SAMPLES|_CHIME_SAMPLE_RATE|_play_feedback_tone'
```

Expect zero results. If any appear, keep the alias and delete only the unreachable body.

#### Problem

`app.py::_play_chime_async()`:

```python
def _play_chime_async() -> None:
    _play_feedback_tone_async("listening")
    return
    global _CHIME_SAMPLES     # ← ~25 unreachable lines follow
```

Refactored into `_play_feedback_tone_async()`; the old body was never removed.
`_CHIME_SAMPLE_RATE` and `_CHIME_SAMPLES` module globals exist only to serve it.

#### Change list

1. Delete `_play_chime_async` entirely (verification shows nothing references it).
2. Delete the now-orphaned `_CHIME_SAMPLES` global.
3. **Keep `_CHIME_SAMPLE_RATE`** — `_play_feedback_tone_async` uses it. Verify by grep before
   removing anything.

#### Tests to add — `tests/test_app.py`

```python
class TestFeedbackTones:
    """T0-5: audio cues are UX-only and must never raise into the pipeline."""

    def test_feedback_tone_spec_returns_known_kinds(self):
        # listening / done / error each return (freqs tuple, duration float)

    def test_unknown_kind_falls_back_to_listening(self):

    def test_play_feedback_tone_swallows_device_errors(self, mocker):
        # patch sounddevice.play to raise; assert no exception propagates
```

That third test is the valuable one — it locks in the deliberate error-swallowing that keeps
a missing audio device from breaking push-to-talk.

#### Done when

`app.py` has one audio-cue implementation, the suite is green, `--selftest` passes.

---

### `[x] T0-6` — Documentation drift on memory recall size

> **DONE.** Confirmed `MEMORY_RECALL_MAX_CHARS = 1500` against a docstring claiming
> "3000, ~750 tokens". Corrected the docstring and dropped the token estimate, which was
> derived from the wrong number. **Deliberately did not change the constant** — `T2-4` and
> `T1-6a` both move the context budget, so that is one decision to take later with all three
> in view. A pointer to that effect is now in the docstring.
>
> **Tests:** +5 in `TestRecallBounds`. These pin two properties that were documented but
> never guarded: the cap is honoured, and the **tail** is returned (recent context wins), plus
> the fail-closed handling of a non-positive cap — where Python's `text[-0:]` would otherwise
> return the entire file.

#### ⚠ VERIFY

Confirm the live value: `config.MEMORY_RECALL_MAX_CHARS` — audit found **1500**, while
`memory.py::recall()`'s docstring says *"3000, ~750 tokens"*. Also check `KB_RECALL_MAX_CHARS`
(audit: `60_000`) for similar drift in `kb.py`.

#### Change list

**Minimal fix now (recommended):** correct the docstring to state 1500 and drop the token
estimate, which was derived from the wrong number.

**Do not raise the constant yet.** `T2-4` (images in history) changes the context budget, and
`T1-6a` (context caching) changes the cost model. Decide the number once, with both in view.
Add a note in the docstring: `# Revisit alongside T2-4 / T1-6a as one context-budget decision.`

#### Tests to add — `tests/test_memory.py`

```python
def test_recall_respects_configured_max_chars(self, tmp_path):
    """T0-6: guard the documented limit against silent drift."""
    # write a file longer than the limit, assert len(recall(...)) <= limit
    # assert the TAIL is returned, not the head
```

The tail assertion matters — tail-read is the documented contract (recent context wins) and
nothing currently guards it.

---

### `[x] T0-7` — Unsynchronised cross-thread state on `_press_captures`

> **DONE — Option A (`threading.Lock`)**, chosen by the item's own verification step:
> **8 existing tests set `_press_captures` / `_press_memory` / `_press_cursor_pos`
> directly**, so a `Queue` refactor would have rewritten working tests for a race fix.
>
> Shipped `_press_lock` plus two accessors, `_read_press_state()` and
> `_write_press_state()`. Every access in `app.py` now goes through them — seven call sites,
> including three the audit had not enumerated (the realtime press handler, the realtime
> release spinner, and the release-cursor fallback). Reads take **one atomic triple**, so a
> caller can never pair a fresh captures list with a stale memory string.
>
> **Tests:** +10, written *before* the refactor and confirmed green against the old code
> first — that ordering is what made this safe. Includes the previously-uncovered
> reuse-vs-recapture threshold behaviour (the no-second-flicker property) and a genuine
> concurrency test that hammers the accessors from four threads and asserts no torn triple is
> ever observed.
**Blast radius:** press/release capture handoff. Touches the latency-critical path, so
measure before and after.

#### ⚠ VERIFY before implementing

1. Re-read `_handle_press`, `_press_time_capture`, `_release_capture_worker`, and
   `_pipeline_worker` together to map every read and write of `_press_captures`,
   `_press_memory`, `_press_cursor_pos`, and `_capture_thread`.
2. Check `tests/test_app.py::TestNimbusApp` for tests that set these attributes directly —
   a `Queue` refactor would break those, a `Lock` would not.
3. Confirm the reuse-vs-recapture behaviour you must preserve: reuse when
   `cursor_moved_px <= _REUSE_THRESHOLD_PX` (150). This exists so a still cursor does not
   cause a second overlay hide/show flicker. **Do not regress it.**

#### Problem

Written by `nimbus-press-capture`, read by `nimbus-release-capture` and the pipeline worker,
with no lock. `_release_capture_worker` does `press_thread.join(timeout=0.5)` first, which
*usually* orders things — but on timeout it reads fields the other thread may still be writing.

#### Change list — pick one

**Option A — `threading.Lock` (lower risk, recommended if tests touch the attributes).**
Add `self._press_lock = threading.Lock()`; guard every read and write. Keep the `join`.
Small, surgical, no behaviour change.

**Option B — `queue.Queue` (cleaner, matches the release path).** Replace the shared
attributes with a `Queue(maxsize=1)` populated by `_press_time_capture`. Removes the shared
mutable state entirely. Larger diff; `_handle_press` must create the queue and pass it
through, and the reuse decision needs the cursor position carried in the queued tuple.

**Decide with verification step 2.** If existing tests poke `_press_captures` directly,
Option A avoids rewriting them; the goal is a race fix, not a refactor.

#### Tests to add — `tests/test_app.py::TestNimbusApp`

```python
def test_press_capture_result_visible_to_release_worker(self, mocker):
    """T0-7: handoff must be safe when the press thread finishes normally."""

def test_release_worker_handles_press_thread_timeout(self, mocker):
    """T0-7: on join timeout the release worker must not read partial state.
    Falls back to re-capture rather than using a half-written result."""

def test_cursor_still_reuses_press_captures(self, mocker):
    """T0-7 regression: cursor movement <= 150px must still reuse, so the
    no-flicker property is preserved."""

def test_cursor_moved_triggers_recapture(self, mocker):
    """T0-7 regression: > 150px must re-capture."""
```

The last two are **regression tests for behaviour that currently has no coverage** — write
them first, confirm they pass against the current code, then refactor. That is what makes
this safe.

#### Done when

No unsynchronised access remains, the four tests pass, and the smoke test shows no added
flicker or latency.

---

### 3.2 Tier 0 exit criteria — ✅ met

- [x] All seven items `[x]` (`T0-8` removed — operational, not code)
- [x] Suite green with more tests than baseline: **477 → 558** (+81), zero regressions
- [x] `python -m app --selftest` → `SELFTEST OK`
- [x] App launches, tray appears, clean shutdown, no orphaned processes
- [x] Fully-local path intact — ran with `STT_PROVIDER=faster-whisper` +
      `TTS_PROVIDER=kokoro`; `LLM_PROVIDER=ollama` resolves to `OllamaClient`
      with `llava:7b`
- [x] **Every** selectable provider resolves to a routable client with a real model id,
      verified by constructing each one against the real (unmocked) factory
- [x] No linter or type diagnostics in any changed file
- [x] Temporary verification scripts removed from the tree

**Not done (deliberate):** no git commits. Changes are left in the working tree for the
maintainer to review and commit.

**Residual risk, stated plainly:**
1. **Anthropic is unproven against the live API** — no key available. Correct by construction
   and unit test; the first real call should be treated as unverified.
2. **The hotkey path was not exercised by a human keypress.** Start-up, provider resolution,
   parsing, and shutdown were verified; the press → speak → release → point loop was not.
   Worth one manual pass before Tier 1.
3. `parse_point_tag` can now leave a double space where a mid-text tag was removed. Harmless
   for TTS; not normalised because collapsing all whitespace would be a behaviour change
   beyond the bug fix.

---

## 4. Tier 1 — Model layer

**STATUS: complete — 9 of 10 items built. 558 → 757 tests passing, zero regressions.**
Everything built is reachable from Settings (§4.3). Only `T1-6c` is unbuilt, and only
because nothing consumes it yet.

| Item | Status | Note |
|---|---|---|
| `T1-1` Native `google-genai` client | ✅ **done** | live-verified |
| `T1-2` Structured output | ✅ **done** | live-verified; three design flaws found and fixed by live testing |
| `T1-7` Thinking-budget tiering | ✅ **done** | live-verified; measured 2.8s TTFB win |
| `T1-9` Split grounding/conversation | ✅ **done** | **promoted from optional to required** by a measured API property |
| `T1-6a` KB context caching | ✅ **done** | live-verified: cache hit on turn 2 |
| `T1-6b` Code execution | ✅ **done** | live-verified; forced a speech-hygiene fix |
| `T1-5` Search grounding | ⚠ **implemented, benefit unproven** | plumbing correct and proven; **does not reliably improve accuracy under Nimbus's own prompt** — see §4.1 |
| `T1-3` Agentic Vision | ✅ **done, off by default** | live-verified: toggle honoured, speech leak-free, coordinate returned in both modes. Whether it beats `crop` is still unmeasured — hence off |
| `T1-4` Gemini Live API | ✅ **done, off by default** | built as `gemini_live.py`; unit-tested with injected fakes. Audio quality still needs your ears — see §4.2 |
| `T1-8` Measurement harness | ⊘ **skipped by decision** | tooling complete (`tools/label_fixtures.py`, `tools/bench_grounding.py`); measurement not run |
| `T1-6c` Files API | ⊘ **unnecessary, not outstanding** | Its only consumer was `T3-2`, which shipped with local text extraction instead — provider-agnostic, works on the fully-local path, keeps `kb_content` a string. See `T3-2` |

**Governing constraint from §0 — honoured:** every addition is behind the `AIClient`
interface. The fully-local path still works; `gemini-native` + `faster-whisper` + Kokoro was
run end to end. All four pre-existing providers still report
`supports_structured_geometry() is False` and are untouched, asserted by
`TestBackwardCompatibility`.

### 4.0 What verification changed — again

Pre-flight verification altered the plan in **five** material ways. As in Tier 0, one of them
would have shipped broken code.

| # | Doc assumed | Verified reality | Consequence |
|---|---|---|---|
| 1 | Direct Google keys look like `AIza…` | The supplied working key is **`AQ.`** format | Detecting one prefix would have silently mis-routed every `AQ.` key to OpenRouter, where it fails auth. `_GOOGLE_KEY_PREFIXES` is now a tuple. |
| 2 | `box_2d` axis order needs confirming | **`[ymin, xmin, ymax, xmax]` — y first**, and `point` is `[y, x]` | Confirmed against a real screenshot. Helpers take y and x as **named** arguments so the order cannot be silently transposed. |
| 3 | Prose and a tool call might stream together | **They never do.** Measured at thinking budgets 0, 64, 128, 256, 512: whenever the model called `point_at` it emitted **zero text** | A single tool-enabled call produced a pointer with **total silence**. This is what forced `T1-9`. |
| 4 | `thinking_budget=0` works | Works on flash; **`pro` models return 400 "Budget 0 is invalid"** | `_clamp_thinking_budget` raises zero to a floor per model. Without it, enabling the latency win would break every pro model. |
| 5 | `google-genai` is additive | It **downgraded `websockets` 17.0.1 → 16.1.1** | Suite re-run immediately: 558 still green, `--selftest` OK. No regression, but worth knowing before a release build. |

**Findings that only a live run could produce.** Three separate defects survived unit tests
and were caught by running against the real API:

1. **The prompt fought the tools.** Given the tag-based `_NIMBUS_SYSTEM_PROMPT` *and* a
   `point_at` tool, the model obeyed the prompt and emitted a `[POINT:…]` text tag — putting
   coordinates straight back into the speech channel, defeating the entire purpose of T1-2.
   Fixed with dedicated structured prompts.
2. **Annotation mode returned silence.** Handed a drawing tool, the model called it and said
   *nothing*. The user would have heard silence while a box appeared. Fixed by requiring
   speech first, in capitals, in the prompt.
3. **The model wrote tool syntax as prose.** Once tools were removed from the speech call, a
   prompt that still *mentioned* `draw_box` caused the model to write
   ` ```python draw_box(ymin=47…)``` ` as text — which TTS reads aloud as "backtick backtick
   backtick python draw box open paren". Fixed twice over: the speech prompt no longer names
   any function, and `ai.strip_non_speech` removes fences and tool calls as a guarantee
   rather than a hope.
4. **Code execution made the model switch into document mode.** Enabling `T1-6b` produced
   `$f'(x) = u'(x)v(x)$` and `### 1. Analytical Derivation` — LaTeX and markdown headings on
   the exact use case the feature exists for. Read aloud, "dollar f prime of x equals" is
   worse than no answer. `strip_non_speech` was extended with LaTeX and markdown patterns; the
   result is now genuinely good TTS: *"three x squared times sine x plus x cubed times cosine
   x, and at x equals two the value comes out to about seven point five eight."*

### 4.1 `T1-5` — implemented, but the benefit did not survive contact

**Recorded as a negative result rather than quietly marked done.**

The plumbing is correct and proven. Isolating variables against the live API:

| Configuration | Citations | Answer |
|---|---|---|
| non-streaming, search only | 2 | 3.14.6 ✓ |
| streaming, search only | 1 | 3.14.6 ✓ |
| streaming, search + thinking budget | 1 | 3.14.6 ✓ |
| streaming, search + `max_output_tokens` | 1 | 3.14.6 ✓ |
| **streaming, search + persona system prompt** | **0** | 3.14 ✓ (uncited) |
| **full Nimbus prompt + screenshot** | **0** | **3.12.5 ✗** |

So: the citation-harvesting code works, and a *plain* grounded call is accurate. But under
Nimbus's actual conditions — strong persona instruction plus an attached screenshot — citation
metadata disappears and accuracy degrades to the point of being wrong.

This vindicates §0.3's rating of `T1-5` as *"convenience, not exclusivity"*. It stays **off by
default**, and `web_search_queries` is now captured alongside citations so the debug log can
distinguish "grounding broke" from "grounding ran but returned no attribution". Turning it on
is not recommended until the prompt interaction is understood.

### 4.2 `T1-4` is built, but its Definition of Done is not met yet

It was previously deferred on the grounds that a bidirectional **audio** path cannot be
verified from here — no way to hear output, speak into a mic, or press the hotkey. That
reasoning was sound but reached the wrong conclusion: it argued against *shipping it on*,
not against *building it*.

So it is built and shipped **off**, behind the `GEMINI_LIVE` toggle. That resolves the
tension:

- The code exists and is unit-tested (connection, mic and speaker are all injectable, so the
  session tests with no audio hardware and no network).
- Nobody reaches it by accident, so a latent audio bug cannot regress the default path.
- The one thing tests genuinely cannot cover — *does it sound right* — is now a switch the
  maintainer can flip, which is the only way that question was ever going to be answered.

`realtime.py` remains the cautionary precedent: it is hidden from Settings *because* it
shipped with known audio issues. The difference is that `realtime.py` was on a code path
users could land on. This one is opt-in and labelled "least tested" in its own tooltip.

**Still outstanding for §1.2 Definition of Done:** a manual smoke test — turn it on, hold the
hotkey, confirm you hear a reply and that the pointer moves. Until then `T1-4` is *built and
unit-tested*, not *verified*. §0.3 still rates its value **neutral** (comparable to OpenAI
Realtime), so it is the weakest item in the tier on merit even once it works.

### 4.3 The experimental settings group

Four Tier 1 capabilities are genuinely useful but genuinely unproven. Leaving them as
env-var-only switches meant they would never be exercised; defaulting them on would violate
§1.3. So they are surfaced in the Settings dialog in a collapsible **experimental** group,
alongside the API-key fields, every one defaulting **off**.

| Setting | Item | Default | Why it is not on |
|---|---|---|---|
| `CODE_EXECUTION` | `T1-6b` | `off` | adds sandbox latency; makes the model switch to document mode and emit LaTeX/markdown |
| `SEARCH_GROUNDING` | `T1-5` | `off` | **measured worse** under Nimbus's own prompt (§4.1) |
| `AGENTIC_VISION` | `T1-3` | `off` | unmeasured against the existing crop pass |
| `GEMINI_LIVE` | `T1-4` | `off` | least-tested path; audio not yet verified |

Design rules the group follows, each asserted by a test:

1. **Every tooltip names a cost, not just a benefit.** A settings dialog that lists only
   upsides pushes users into choices they would not have made informed. `SEARCH_GROUNDING`'s
   tooltip carries the measured failure explicitly, including the wrong answer it produced
   (3.12.5 instead of 3.14.6) and the words `NOT RECOMMENDED`.
2. **Every tooltip states its provider requirement**, since all four are native-Gemini-only.
3. **Every toggle is listed in `_LOCAL_KEYRING_ENTRIES`**, so "Clear all Nimbus local data"
   really does return to a first-run state rather than leaving experimental flags set.
4. The group is **collapsed by default**, so it cannot be stumbled into.

#### A real defect this surfaced in the test suite

The toggles persist to the keyring, and `resolve_setting` resolves env → keyring → default
**once at import**. Three tests asserted `config.AGENTIC_VISION == "off"` directly — which
tests *the developer's machine*, not the code. The moment a toggle is flipped in Settings, the
value persists and those tests go red on a perfectly healthy build. This is not hypothetical:
it happened during this session's live verification and cost three failures.

Fixed with a `first_run_config` fixture in the suite's **first `conftest.py`**: it reloads
`config` with the environment cleared and the keyring stubbed empty, asserts the *declared*
default, then reloads again on teardown so no later test inherits a stubbed config. Verified by
setting all five keyring entries to `on` and re-running: **757 passed**, where the old tests
failed three.

> Note for §1.4, which previously stated there is **no `conftest.py`**. There is now exactly
> one, holding exactly one fixture, for the reason above.

---

### `[-] T1-8` — Grounding-accuracy harness — *tooling done, measurement skipped by decision*

> **Maintainer decision (2026-08-09): skip labelling fixtures. Nimbus's multi-screen /
> multi-DPI handling already works — verified directly against real hardware (§ below) —
> and the harness's only remaining purpose is optimisation data for `T1-3` and `T4-6`, which
> is not worth the time cost right now.**
>
> The tooling itself is complete and self-tested, so this is available at zero cost whenever
> revisited — nothing further to build. What exists:
> - `tools/label_fixtures.py` — click-and-drag box labeller, schema-validated at save time
> - `tools/bench_grounding.py` — scorer, reusing `tools/bench.py`'s Mann-Whitney/bootstrap
>   stats. **Caught a real bug via its own self-test**: a bare fixture query like "the save
>   icon" has no directional word, so `classify_query` marks it conceptual and the native
>   client's split-role design (T1-9) skips the geometry call entirely — scoring 0% on an
>   unmissable target. Fixed with `as_locate_query()`, which wraps bare phrases into an
>   explicit locate request before scoring.
> - +29 tests in `test_gemini_cache.py::TestGroundingHarness` /
>   `TestLocateQueryPhrasing` / `TestFixtureLoading` / `TestRunSummary`
>
> **Multi-screen support needed no measurement to confirm — it already works and was verified
> directly:**
> ```
> monitors detected: 1
>   screen 1: 3840x1080 at (0,0)
>   physical 3840x1080 -> model sees 1920x1080  scale=2.00
>   model(960,540) -> physical(1920,540)   correct
> ```
> Multi-monitor, mixed-DPI, and resolution handling are §2.2 architecture, not a T1-8
> deliverable — `capture_all_screens()`, the per-monitor overlay windows, and
> `unscale_model_coords` were already correct before Tier 1 started.
>
> Revisit if: `T1-3` (Agentic Vision) is reconsidered, 4K icon-miss reports come in, or a
> competitive benchmark number is needed.

> **The blocker is removed. `tools/label_fixtures.py` is built and tested.**
>
> The doc identified manual fixture labelling as the real cost, so the tool came first: capture
> a screen, drag a box around a target, type the query you'd actually ask, `Ctrl+S`. Undo,
> unsaved-changes guard, and schema validation at labelling time included — the harness must
> refuse a malformed fixture rather than score against a bad box.
>
> ```powershell
> py -3.13 tools/label_fixtures.py --capture              # label the current screen
> py -3.13 tools/label_fixtures.py --image shot.png       # label an existing image
> ```
>
> Boxes are stored in **capture-image pixels (Space C)** — the same space the model's
> normalised output converts into, so the harness compares like with like and needs no extra
> transform. Sidecar schema is `build_sidecar()` / `validate_sidecar()`, both pure and unit-tested.
>
> **Still needed from the maintainer:** fixture screenshots across the DPI and UI variety the
> item lists. Then `tools/bench_grounding.py` scores providers against them and populates §11.
>
> One datum already worth measuring: with a screenshot attached, TTFB is ~3–5 s regardless of
> thinking budget, which suggests **image handling, not reasoning, dominates latency**. That
> makes `T1-3`'s "one round trip instead of two" claim genuinely uncertain rather than
> obviously true.

**Why first:** `T1-2`, `T1-3`, `T1-9`, `T4-4`, `T4-6` and all provider defaults are currently
decided by assumption. This makes them decided by data. **Revised estimate: 1.5–2 days**, not
1 — the fixture labelling is manual work and is a prerequisite, not a parallel task.

#### ⚠ VERIFY before implementing

1. Read `tools/bench.py` fully (192 LOC) to reuse its Mann-Whitney U + bootstrap-CI
   reporting rather than reimplementing.
2. Read `tests/test_bench.py` (5 tests) for its existing conventions.
3. Confirm `scipy` is available in the venv and note that `nimbus.spec` **excludes** it —
   the harness is script-only and must never be imported by runtime modules.

#### Change list

1. `tools/grounding_fixtures/` — screenshots + a JSON sidecar per image:
   ```json
   {"image": "vscode_4k_200dpi.png", "monitor": {"width": 3840, "height": 2160},
    "targets": [{"query": "the save icon", "box": [1204, 88, 1232, 116]}]}
   ```
2. `tools/label_fixtures.py` — a small Qt or PIL tool to draw boxes and emit the sidecar.
   **Build this before labelling by hand**; it converts hours of tedium into minutes.
3. `tools/bench_grounding.py` — for each (provider × model × strategy), record: pixel error
   from box centre, **hit-rate** (did the point land inside the box), latency, token cost.
4. Reuse `tools/bench.py`'s reporting for significance testing between strategies.

Strategies to compare: direct tag parse · structured `point` · `box_2d` · two-stage grid
locator · refinement crop on/off · Agentic Vision on/off.

Fixtures must span: 1080p at 100% · 1440p at 125% · 4K at 200% · a dense IDE toolbar · a
ribbon UI · a dark theme · a small (<24 px) icon target.

#### Tests to add — `tests/test_bench_grounding.py`

```python
def test_hit_rate_computed_correctly(self):
    """Point inside box → hit; on the boundary → hit; outside → miss."""
def test_pixel_error_is_euclidean_from_box_centre(self):
def test_fixture_sidecar_schema_validates(self):
def test_malformed_fixture_is_skipped_not_fatal(self):
def test_summary_handles_zero_successful_calls(self):
    """A provider that errors on every fixture must report cleanly, not divide by zero."""
```

The harness itself needs tests because a measurement tool that is wrong is worse than no
measurement.

#### Done when

`python tools/bench_grounding.py --provider openai --strategy tag` produces a table with
hit-rate, median pixel error, and CI. Results recorded in a new `§11 Measured results`
section of this document — **that section becomes the authority for every later model decision.**

---

### `[x] T1-1` — Native `google-genai` client

> **DONE, live-verified.** New module `gemini_native.py` (~500 LOC) holding
> `GeminiNativeClient(AIClient)`. Placed in its own module because the native SDK brings a
> type vocabulary nothing else in Nimbus needs; `create_ai_client` imports it lazily, so
> other-provider users pay no import cost.
>
> - Routed by `ai.is_direct_google_key()` — accepts **both** `AIza` and `AQ.` (finding #1)
> - Public surface byte-identical to every other client, so `_pipeline_worker` cannot tell
>   which client is behind it
> - **OpenRouter path completely untouched** — asserted by
>   `test_openrouter_gemini_path_untouched`
> - Frozen build handled: `requirements.txt` pinned, `nimbus.spec` `hiddenimports` extended
>   with the dynamically-loaded `google.genai` / `google.auth` submodules, and
>   `_run_selftest`'s module tuple extended so a broken bundle fails at selftest rather than
>   at a user's first interaction
> - Settings dialog gained *Google Gemini (native SDK) — recommended* as a distinct provider
>   with a model picker, so the transport is a visible choice rather than hidden auto-detection
>
> **Tests:** +75 in new `tests/test_gemini_native.py`, all mock-based via an injected
> `client_factory` — no network, no key.

#### ⚠ VERIFY before implementing

1. Current `google-genai` package name, version, and import surface — <https://ai.google.dev/gemini-api/docs>
2. Current Gemini model IDs for vision (<https://ai.google.dev/gemini-api/docs/models>)
3. Whether streaming text deltas + a tool call can arrive in one response (required — see `T1-2` risk)
4. `nimbus.spec` implications: does `google-genai` ship native libs or data files? If yes it
   needs `collect_all()` like `av` / `espeakng_loader` / `phonemizer` already do, plus a
   frozen-EXE verification pass.

#### Current state

`ai.py::GeminiClient` is an `openai.OpenAI` instance pointed at OpenRouter's
OpenAI-compatible endpoint. `create_ai_client()` can alternatively point it at
`generativelanguage.googleapis.com/v1beta/openai/` — **also** a compatibility shim.
Inaccessible today: structured spatial output, thinking budgets, Live API, Search grounding,
URL context, explicit caching, code execution, Files API, computer use.

`GeminiClient.ask_stream()` already wraps its request in a `try/except` raising a
`RuntimeError` with a four-item diagnostic checklist about OpenRouter keys, funding, and
preview-model opt-in — evidence of how many failure modes the extra hop adds.

#### Change list

1. `requirements.txt` — add `google-genai` (pinned).
2. `ai.py` — `class GeminiNativeClient(AIClient)` implementing `ask_stream()` / `ask()` with
   the **exact** existing contract: a context manager exposing `.text_deltas()` and
   `.final_result() -> PointParseResult`. Mirror `_GeminiStreamingResponse`'s shape.
3. `ai.py::create_ai_client()` — route a direct Google key (`AIza…`) to the native client;
   keep `sk-or-` → OpenRouter → existing `GeminiClient`. **Both paths stay.**
4. `settings_dialog.py` — the existing Gemini `_Provider` already accepts either key type
   (`key_hint` mentions both). Update the hint; no structural change.
5. `nimbus.spec` — add to `hiddenimports`; add `collect_all` if verification step 4 requires it.
6. `app.py::_run_selftest()` — no change needed (`ai` is already in `runtime_modules`).

#### Tests to add — `tests/test_ai.py::TestGeminiNativeClient`

```python
def test_streams_text_deltas(self, mocker):              # connection_factory injected
def test_final_result_returns_point_parse_result(self, mocker):
def test_context_manager_closes_stream_on_exit(self, mocker):
def test_context_manager_closes_stream_on_exception(self, mocker):
def test_history_converted_to_native_format(self, mocker):
def test_empty_history_turns_skipped(self, mocker):
    """Parity with GeminiClient: empty content is rejected by the API."""
def test_kb_content_injected_when_present(self, mocker):
def test_multi_image_multi_screen_payload(self, mocker):
def test_raises_actionable_error_on_auth_failure(self, mocker):
```

`tests/test_ai.py::TestCreateAIClient`:

```python
def test_google_key_routes_to_native_client(self):       # AIza... → GeminiNativeClient
def test_openrouter_key_still_routes_to_gemini_client(self):
    """T1-1 regression: the OpenRouter path must NOT be broken by adding native."""
def test_explicit_base_url_still_wins(self):
```

**Regression gate:** every existing `TestGeminiClient` and `TestGeminiClientExtraCoverage`
test must still pass untouched. If any needs changing, the change is not additive.

#### Rollback

Delete the factory branch. The OpenRouter path is untouched and remains the fallback.

---

### `[x] T1-2` — Structured output instead of free-text tag parsing

> **DONE, live-verified. The prediction held: geometry now never touches the speech channel.**
>
> Geometry arrives as a `function_call` part from a *separate request* (see `T1-9`), so the
> class of bug fixed in T0-3 is now **structurally impossible** for this provider rather than
> merely guarded against. `PointParseResult.malformed_tags` comes back empty because there is
> no tag to malform.
>
> Shipped:
> - `AIClient.supports_structured_geometry()` / `supports_thinking_budget()` — optional, with
>   concrete `False` defaults, so all four existing providers are untouched (§1.3 rule)
> - `ai.normalised_point_to_space_c()` / `normalised_box_to_space_c()` — pure, **named y/x
>   arguments** to make axis transposition impossible. **No fourth coordinate space:** output
>   is Space C and feeds `unscale_model_coords` unchanged
> - `annotations.Rect` with a `.center` property — a real box frames a control correctly where
>   a `Circle` with a model-guessed radius clips or over-covers
> - `point_at` / `draw_box` tools declaring **`y` and `x` as separate named integers**, not an
>   array, so the wire format is self-documenting
> - `ai.strip_non_speech()` — defence in depth against fences and tool syntax in prose
> - `_pipeline_worker` skips the `"[" in sentence_buffer` guard on the structured path. That
>   guard halts TTS flushing on **any** bracket, so removing it also fixes legitimate prose
>   like "the array index `[0]`" being truncated
>
> **Tests:** the axis-order tests deliberately use a **wide, short** box (a search bar) so a
> transposed order fails loudly instead of looking plausible — the exact trap the doc warned
> about. Plus a hard-invariant test asserting no marker (`[point`, `point_at`, raw digits,
> fences) survives into spoken text.

**Highest-value item in this document.** Provider-agnostic in its first half.

#### ⚠ VERIFY before implementing

1. Gemini's current structured-output / pointing response schema and its **coordinate
   normalisation** (audit found 0–1000 normalised; confirm, and confirm the axis order).
   `box_2d` ordering in particular must be verified — getting `[ymin, xmin, ymax, xmax]`
   versus `[xmin, ymin, xmax, ymax]` backwards produces plausible-looking wrong coordinates,
   which is the worst failure mode.
2. Whether prose **and** a tool call stream together in one response. Sentence-level TTS
   streaming depends on text deltas arriving promptly. If they serialise, latency regresses
   and this needs a different shape (possibly `T1-9`'s parallel split instead).
3. OpenAI strict function calling and Anthropic tool-use schemas, if implementing those too.

#### Why the current design is fragile — provider-agnostic

Coordinates share a channel with speech. Five layers of defence exist to compensate:
`_POINT_TAG_RE` (two known holes, `T0-3`) · `annotations._ANY_TAG_RE` ·
`annotations._UNTERMINATED_TAG_RE` · the `if "[" in sentence_buffer` streaming guard ·
case-insensitive regexes with `\s*` tolerance. **Structured output removes the problem
instead of defending against it.**

#### Change list

1. `ai.py` — add to `AIClient` an **optional** method with a concrete default:
   ```python
   def supports_structured_geometry(self) -> bool:
       return False        # OllamaClient and any future provider keep working unchanged
   ```
2. `ai.py` — a provider-neutral `Geometry` dataclass set (reuse `annotations.Circle` /
   `Arrow` / `Underline` / `Label`, add `Rect`). **Geometry stays in Space C** so
   `unscale_model_coords` and `annotations_to_local` are unchanged. This is the critical
   design constraint: do not introduce a fourth coordinate space.
3. `ai.py::GeminiNativeClient` — native `point` / `box_2d`, normalised → Space C.
4. `app.py::_pipeline_worker` — when `supports_structured_geometry()`, take geometry from the
   structured channel; otherwise the existing tag path, unchanged.
5. Only when the structured path is active, skip the `if "[" in sentence_buffer` guard. That
   guard currently halts TTS flushing on **any** bracket, including legitimate prose like
   "the array index `[0]`" — so removing it is a small latency and correctness win.
6. `annotations.py` — add `Rect`; `overlay.py` — render it (see `T3-5`).

#### Tests to add

`tests/test_ai.py::TestStructuredGeometry`:

```python
def test_normalised_coords_map_to_space_c(self):
    """0-1000 normalised → declared-resolution pixels. Pure function, no network."""
def test_box_2d_axis_order_matches_verified_schema(self):
    """T1-2: guards the axis-order trap. Uses a deliberately non-square box so a
    transposed order fails loudly instead of looking plausible."""
def test_rect_centre_used_as_point_when_pointing(self):
def test_out_of_range_normalised_value_is_clamped_at_space_c_boundary(self):
def test_default_supports_structured_geometry_is_false(self):
    """Backward-compat: OllamaClient and AnthropicClient must be unaffected."""
```

`tests/test_app.py::TestNimbusApp`:

```python
def test_structured_path_skips_tag_parse(self, mocker):
def test_tag_path_still_used_when_provider_lacks_structured(self, mocker):
    """T1-2 regression: Ollama must behave exactly as before."""
def test_bracket_in_prose_no_longer_halts_tts_on_structured_path(self, mocker):
    """e.g. 'the array index [0] is first' must still stream to TTS."""
```

**Regression gate:** all 18 `test_annotations.py` tests and all `TestParsePointTag` tests
pass untouched.

#### Rollback

`supports_structured_geometry()` returns `False`. Everything reverts to the tag path with no
other change.

---

### `[x] T1-3` — Agentic Vision instead of the refinement crop — *built, off by default*

> **BUILT AND LIVE-VERIFIED, shipped OFF.** `GROUNDING_REFINEMENT` ∈ `{crop, agentic, off}`
> defaults to `crop`, so current behaviour is unchanged and `refine_point_via_crop` is fully
> intact. `AGENTIC_VISION` defaults to `off` and is togglable from Settings (§4.3).
>
> **Why this is now built when the item previously said "do not implement".** The original
> gate was sound in intent — don't *adopt* on the strength of a vendor blog post — but it
> conflated *building* with *defaulting on*. Building it behind a default-off toggle is what
> makes measurement possible at all; refusing to build it guaranteed the evidence would never
> exist. The gate is preserved where it actually belongs: **`crop` remains the default**, and
> `agentic` stays off until measured.
>
> **Live verification (2026-08-09, `gemini-3-flash-preview`, 1920×1080):**
>
> | | `AGENTIC_VISION=off` | `AGENTIC_VISION=on` |
> |---|---|---|
> | `supports_agentic_refinement()` | `False` | `True` |
> | spoken text | non-empty | non-empty |
> | coordinate returned | yes | yes |
> | `point_at` / `[POINT` leaked into speech | none | none |
> | TTFB | 4.16 s | 2.55 s |
>
> The TTFB figure is **not** evidence the feature is faster — it is one sample of a
> two-request race against a shared network, and the agentic run raises the geometry
> budget to 2048. Treat it as "not catastrophically slower", nothing more.
>
> **Coordinate-ordering check.** The odd result on a real cluttered desktop (an address bar
> reported at 98% of screen height) prompted a ground-truth test rather than an assumption: a
> synthetic 1920×1080 UI with four **asymmetrically placed** corner buttons, each asked for
> individually. All four returned coordinates landed dead-centre inside their known
> rectangles, and a transposition would have failed every one. So the y-first wire format is
> handled correctly and the desktop miss was the model mislocating, not a code defect. This is
> the check that would have caught a silent systematic y/x swap.
>
> One relevant datum from earlier live testing: with a screenshot attached, TTFB is ~3–5 s
> regardless of thinking budget, suggesting image handling — not reasoning — dominates. That
> still makes the "one round trip instead of two" claim worth measuring rather than assuming.

**Still gated for the default.** Do not switch the default to `agentic` until measurement
shows it beats the two-pass crop.

#### ⚠ VERIFY

1. Which Gemini models support Agentic Vision and how it is enabled.
2. Its latency cost — it may internally do multiple passes, so "one round trip" from the
   caller's view is not automatically faster. **Measure with `T1-8`.**
3. Whether it works alongside structured output from `T1-2`.

#### Current state

`locator.py::refine_point_via_crop()` + `app.py::_refine_model_coordinate()`: crop a 900 px
native-resolution window (`REFINEMENT_CROP_SIZE`) around the first coordinate and re-ask with
`_REFINEMENT_SYSTEM_PROMPT`. Gated by `_looks_directional()` (13 phrases) and
`_references_cursor_area()` (7 phrases). Deliberately non-destructive: uncertain verification
returns `None` and the original point is kept. Exists because `CANDIDATE_RESOLUTIONS` caps at
1920×1200, so 4K downscales ~2×.

#### Change list — as built

1. `GROUNDING_REFINEMENT` ∈ `{crop, agentic, off}`, default `crop`
   (current behaviour — a new setting must never change existing behaviour, §1.3).
2. `AGENTIC_VISION` on/off, default `off`, surfaced in Settings.
3. `GeminiNativeClient.supports_agentic_refinement()` reports the toggle. `app.py` dispatches
   three ways on `GROUNDING_REFINEMENT` and **silently falls back to `crop`** when the active
   client does not support agentic refinement — so every non-Gemini provider keeps today's
   behaviour with no branching at the call site.
4. `_agentic_instruction()` is appended to the **geometry call's** system prompt only. The user
   never hears grounding guidance, and putting it on the speech call would waste tokens and
   risk it being spoken aloud.
5. The geometry call's thinking budget is raised from the minimum to `_AGENTIC_THINKING_BUDGET`
   (2048) when the toggle is on. Self-inspection *is* reasoning; at budget 0 there is no room
   to do it, so the feature would silently no-op. This is the feature's latency cost and the
   reason it is opt-in.
6. **`refine_point_via_crop()` kept fully intact.** It is provider-agnostic and works today.

#### Tests added — `tests/test_experimental.py`

Covering: off by default; capability reported when enabled; the instruction reaches the
geometry call and *not* the speech call; the budget is raised above the minimal-budget
baseline; a disabled client's config is byte-identical to today's; other providers do not
claim agentic support (so the silent fallback actually triggers); and `GROUNDING_REFINEMENT`
defaults to `crop`.

#### Rollback

Setting back to `crop`, or `AGENTIC_VISION=off`. Two independent switches, either sufficient.

---

### `[x] T1-4` — Gemini Live API alongside `realtime.py` — *built, off by default*

> **BUILT AND UNIT-TESTED, shipped OFF. Not yet audio-verified — see §4.2.**
>
> Implemented as a **new module `gemini_live.py`**, not as an addition to `realtime.py`. Two
> reasons: `realtime.py` is the known-broken path and coupling a new implementation to it
> inherits its problems, and the Live API is async-only while every other audio path in Nimbus
> is synchronous and thread-based, so the asyncio containment needs its own home.
>
> **Selected by the `GEMINI_LIVE` toggle** in Settings (§4.3), default `off`. Any setup failure
> leaves the normal pipeline running — the fail-safe `realtime.py` already established.
>
> **Design notes worth carrying forward:**
>
> - `_AsyncLiveBridge` confines asyncio to one private event loop on its own thread and exposes
>   a blocking, iterable façade. Pushing asyncio up into `app.py` would collide with the Qt
>   event loop. Isolating it also keeps `GeminiLiveSession` testable with a plain fake and no
>   asyncio at all.
> - **Input is 16 kHz, output is 24 kHz.** This asymmetry is a live trap: `realtime.py` uses
>   24 kHz *input*, and copying that value produces audio the model hears at the wrong pitch.
>   Both rates are named constants with the hazard documented at the definition.
> - Coordinates stay **normalised 0–1000** at the callback boundary. `app.py` owns conversion to
>   Space C because only it knows the capture dimensions for the turn — so no fourth coordinate
>   space is introduced, consistent with §2.2.
> - The **two-mic hazard** applies here exactly as it does to `realtime.py`, and the same
>   `_should_connect_stt` guard covers it.
> - `connect()` waits at most 15 s for the session to open, so a hung connect cannot prevent
>   Nimbus from starting.
>
> **Outstanding:** the manual audio smoke test. Until then this is *built*, not *verified*.

#### ⚠ VERIFY

1. Live API WebSocket protocol, session config shape, and current model IDs.
2. Audio format both directions (current OpenAI path uses 24 kHz PCM16 mono — confirm parity).
3. Function-calling event shape for `point_at`.
4. Session-resumption mechanics.
5. **The two-mic hazard.** `app.py::_should_connect_stt()` exists solely to stop the 24 kHz
   realtime mic and the 16 kHz AssemblyAI mic both grabbing the device. Confirm it still
   guards correctly for the Gemini path.

#### Current state

`realtime.py` (345 LOC) is **deliberately hidden from Settings** —
`settings_dialog.py` says: *"experimental speech-to-speech path with known audio issues (no
transcription / no playback on some setups)"*. Reachable only via
`LLM_PROVIDER=openai-realtime` in `.env`.

**Honest assessment:** OpenAI Realtime and Gemini Live are comparable. The real argument is
that the existing implementation is broken, so a rewrite costs the same either way. Affective
dialog and proactive audio are the genuine tiebreakers for a "buddy" persona.

#### Change list — as built

1. **New module `gemini_live.py`** — `GeminiLiveSession` with the same public surface as
   `RealtimeSession` (`connect` / `start_turn` / `respond` / `stop` / `close`) and the same
   injectable `connection_factory` / `mic_stream_factory` / `speaker_factory`, so `app.py`
   selects between them without new branching. Plus `_AsyncLiveBridge` for asyncio containment.
2. `app.py::_setup_gemini_live()` + `_gemini_live_on_coordinate()` — gated on the `GEMINI_LIVE`
   toggle. Fail-safe preserved: any setup error leaves the session `None` and the normal
   pipeline runs.
3. `settings_dialog.py` — `GEMINI_LIVE` in the experimental group (§4.3), off by default, with a
   tooltip that says plainly it is the least-tested path.
4. `nimbus.spec` + `app.py::_run_selftest()` — `gemini_live` registered in both. It is imported
   lazily behind a toggle, so PyInstaller's static graph cannot see it and the selftest would
   otherwise never touch it. **This gap was real and is now closed** (`gemini_cache` too).

#### Tests added — `tests/test_experimental.py`

Injected fakes for connection, mic and speaker throughout — no audio hardware, no network.
Covering: off by default; `point_at` tool calls fire the coordinate callback; malformed or
partial tool args are dropped rather than raised; PCM16→float32 conversion; mic chunks are not
forwarded outside a turn; a mic-callback exception cannot kill the audio thread; `close()` tears
down mic, speaker and session even when each raises; and coordinates stay normalised at the
callback boundary.

#### Rollback

Provider selection back to `openai-realtime` or any non-realtime provider.

---

### `[⚠] T1-5` — Search grounding — *implemented; benefit unproven. See §4.1*

> **Plumbing DONE and proven; the feature's promise did not hold under Nimbus's conditions.**
>
> Shipped: `SEARCH_GROUNDING` (default `off`), attached to the **speech** call only so search
> latency can never delay the pointer; citation extraction from
> `grounding_metadata.grounding_chunks[].web.{title,uri}`; citations and `web_search_queries`
> published to `app.py` and written to the debug log — **never** to spoken text, per the
> write-for-the-ear contract. A test asserts no URL can reach `spoken_text`.
>
> **The negative result is in §4.1 and is the important part.** A plain grounded call returns
> citations and the correct answer. Add Nimbus's persona prompt and citations vanish; add a
> screenshot too and the answer became *wrong* (3.12.5 instead of 3.14.6). Recommended to
> leave off until that interaction is understood.
>
> URL context: not implemented.

#### ⚠ VERIFY
Tool names/config for Search grounding and URL context; citation response shape; whether
grounding is compatible with structured output (`T1-2`) in one request.

#### Change list
1. Setting `SEARCH_GROUNDING` ∈ `{off, on}`, default **`off`** (§1.3 — new settings default
   to current behaviour, and this one has cost and privacy implications).
2. Only offered when the active client supports it.
3. Citations appended to the **debug log and memory record**, never spoken — reading URLs
   aloud violates the write-for-the-ear prompt contract.

#### Tests to add
```python
def test_grounding_disabled_by_default(self): ...
def test_citations_recorded_in_memory_not_spoken(self, mocker):
    """Spoken text must contain no URLs."""
def test_unsupported_provider_ignores_grounding_setting(self, mocker): ...
```

**Honest note:** not uniquely Gemini. Anthropic has web search; any search API works.
Gemini's advantage is grounding + citations in one request.

---

### `[x] T1-6a` · `[x] T1-6b` · `[ ] T1-6c` — Caching, code execution, Files API

> **6a caching — DONE, live-verified.** The doc's own gating question answered first:
> a max-size 60,000-char KB measures **10,002 tokens**, and caching served **10,008 of 10,013**
> prompt tokens from cache. So the feature is viable — the suspected minimum-size problem was
> unfounded for this model.
>
> New `gemini_cache.py` with `KBCacheManager`. Live result: turn 1 miss, **turn 2 hit**,
> `close()` released the cache.
>
> **A design constraint shaped this and is worth remembering.** Gemini bundles
> `system_instruction` *and* tool declarations into the cache, and a request using
> `cached_content` cannot override them. The split-role architecture (T1-9) issues two calls
> with different prompts and different tools, so one shared cache is impossible. The resolution
> is also the better design: cache the KB and use it on the **speech call only**. The geometry
> call is pure visual grounding — documentation cannot help locate a pixel — so it now receives
> no KB at all, shrinking that payload as a side benefit.
>
> Details: keyed by **content hash**, so editing the KB file invalidates immediately rather
> than serving stale docs for the TTL. 15-minute TTL. Caches deleted on shutdown, because they
> are billed for storage duration. **Defaults ON** — the one deliberate exception to §1.3's
> "new settings reproduce current behaviour", justified because it changes nothing observable
> and every failure path falls back to inline injection.
>
> **6b code execution — DONE, live-verified.** Off by default. Verified on the annotation
> prompt's own worked example (a chain-rule correction, previously unverified arithmetic):
> the model now computes and numerically checks its answer. Forced the `strip_non_speech`
> extension above — without it the feature actively harmed the speech channel.
>
> **6c Files API — not started.** Its only consumer is `T3-2` (PDF/multi-file KB), which is not
> scheduled, so building it now would be speculative.
>
> **Tests:** +38 in new `tests/test_gemini_cache.py` — threshold decisions, content-hash
> invalidation, graceful failure, concurrent `get_or_create` creating exactly one cache, and
> LaTeX/markdown speech hygiene.

#### ⚠ VERIFY
Caching API shape and minimum cacheable size (a 60 K-char KB may be below or above the
threshold — this determines whether 6a is worth doing at all); code-execution tool config;
Files API upload/reference lifecycle and retention.

**6a — Explicit context caching.** `ai.py` already implements Anthropic `cache_control`
breakpoints carefully: system prompt as one ephemeral block, KB body as a second, memory
prefix split out of the user message as a third — with a comment explaining *never cache the
current transcript; only stable prefixes help.* `GeminiClient` does none of this.
Gemini explicit caching suits `KB_RECALL_MAX_CHARS = 60_000` (~15 K tokens): cache once per
app session, reference cheaply thereafter.
*Tests:* cache created once per app; invalidated on app switch; **KB content still injected
when caching is unavailable** (graceful degradation).

**6b — Code execution.** The annotation prompt's own worked example is a calculus chain-rule
correction where the arithmetic is currently model-generated and unverified.
*Tests:* execution results reach spoken text; execution failure degrades to a normal answer
rather than erroring the pipeline.

**6c — Files API.** Prerequisite for `T3-2` (PDF/multi-file KB). Upload once, reference across
turns.
*Tests:* upload once and reuse; expiry handled by re-upload, not a crash.

---

### `[x] T1-7` — Thinking-budget tiering by query class

> **DONE, live-verified. The measured win is larger than the doc predicted.**
>
> Time-to-first-token, prose-only, same prompt:
>
> | Thinking budget | TTFB |
> |---|---|
> | default | **3.97 s** |
> | 0 | **1.18 s** |
>
> A **2.8 second** improvement — the single largest latency lever measured anywhere in Nimbus,
> larger than any of the four existing optimisations in §2.5.
>
> Shipped:
> - `ai.classify_query()` — pure, returns `locate` / `conceptual` / `diagnostic`.
>   **Diagnostic is tested first**, deliberately: "why is the save button greyed out" contains
>   a directional word but needs real reasoning, and classifying it as a cheap lookup would
>   degrade the answer
> - `THINKING_BUDGET_BY_CLASS` = locate 0 · conceptual 512 · diagnostic 4096
> - `_clamp_thinking_budget()` — raises zero to a per-model floor, because **pro models reject
>   `budget=0` with a 400** (finding #4). Without this, enabling the optimisation would have
>   broken every pro model
> - The geometry call always uses the minimal budget: locating an element is pure perception,
>   so reasoning tokens there are latency with no accuracy return
>
> **Honest note:** the 2.8s figure is prose-only. With a 1920×1080 screenshot attached, image
> handling dominates and observed TTFB is ~3–5 s regardless of budget. The budget win is real
> but is not the whole latency story — `T1-8` should measure the image-attached case properly.
>
> **Tests:** table-driven classification, budget ordering, per-model clamping, and a drift
> guard asserting `ai` and `app` directional-word lists stay in sync.

#### ⚠ VERIFY
Thinking/reasoning parameter names per provider and their valid ranges. Confirm a minimal or
zero budget is permitted on the target model — if the floor is high, the latency win shrinks.

#### Current state
Every query gets `_NIMBUS_MAX_TOKENS = 1024`, one temperature, no reasoning control.
"Where's the save button?" and "Why is my derivative wrong?" cost identical latency.

#### Change list
1. `ai.py` — a pure `classify_query(transcript) -> QueryClass` function. **Reuse
   `_looks_directional()`**, which already makes this distinction for the grid locator.
2. Map class → budget per provider; unsupported providers ignore it.

| Class | Budget | Rationale |
|---|---|---|
| Locate | Minimal / none | Pure grounding; reasoning adds latency, not accuracy |
| Conceptual | Low | Explanation, no spatial work |
| Diagnostic | High | Genuine multi-step reasoning |

#### Tests to add
```python
class TestClassifyQuery:
    """Pure function — table-driven, no network."""
    @pytest.mark.parametrize("q,expected", [
        ("where is the save button", "locate"),
        ("show me the color panel", "locate"),
        ("what is a pivot table", "conceptual"),
        ("why is my answer wrong", "diagnostic"),
        ("", "conceptual"),                      # empty must not crash
    ])
    def test_classification(self, q, expected): ...

def test_unsupported_provider_ignores_budget(self, mocker): ...
def test_budget_never_exceeds_provider_max(self): ...
```

**What it improves:** directly attacks the loudest complaint in this product category —
that the interaction *feels slow*. Locate queries are the most frequent and most
latency-sensitive case; spending reasoning tokens on them is pure loss.

---

### `[x] T1-9` — Split the grounding role from the conversation role

> **DONE — and promoted from "optional, gated on T1-8" to REQUIRED by measurement.**
>
> The doc framed this as an elegant optimisation worth doing only if grounding accuracy
> demanded it. Verification made it mandatory instead: **Gemini returns prose *or* a function
> call in a turn, never both.** Measured at thinking budgets 0, 64, 128, 256 and 512 — every
> single time the model chose to call `point_at`, it emitted zero text. A single tool-enabled
> call therefore produced a pointer with **complete silence**, which is a correctness failure,
> not a cosmetic one: the user held a hotkey and asked a question.
>
> So the two roles run as two **concurrent** requests:
>
> | Call | Tools | Budget | Role |
> |---|---|---|---|
> | speech | **none** — declaring any silences prose | per query class | streams to sentence-level TTS |
> | geometry | `point_at` / `draw_box` | minimal | daemon thread, harvested in `final_result()` |
>
> Wall-clock is `max(speech, geometry)`, not their sum — the same overlapping trick
> `_release_capture_worker` already uses. Geometry resolves while the first sentence is still
> playing, which is exactly the headroom available since the cursor flies *after* speech begins.
>
> Two prompts, as the doc specified: `_NIMBUS_STRUCTURED_SYSTEM_PROMPT` and
> `_NIMBUS_STRUCTURED_ANNOTATION_PROMPT`. Both preserve the persona verbatim and **name no
> functions at all** — because a prompt that mentions tools while none are declared makes the
> model write the call out as markdown (finding #3).
>
> Cost control: the geometry call is **skipped entirely for conceptual questions**, so "what is
> HTTP" costs exactly one request. Annotation mode always attempts geometry, since the user
> explicitly enabled drawing — a bug caught by unit test, where "circle the search bar" was
> classifying as conceptual and silently producing no annotation.
>
> **Safety property, asserted by `test_geometry_failure_still_speaks`:** speech never depends
> on geometry succeeding. A geometry exception, timeout, or malformed argument drops the
> pointer and keeps the answer.

**Original plan (kept for context).** Gated on `T1-8`; Tier 1 in *value*, Tier 3 in *effort*.

#### ⚠ VERIFY
`T1-8` results first. Then: does a cheap conversational model plus a specialised grounding
model actually beat one good model on hit-rate **and** first-audible-word latency? Measure
both, not just accuracy.

#### The tension

| Role | Wants | Latency profile |
|---|---|---|
| Conversation | Fast, cheap, warm, personable | **Critical** — sentence 1 must reach TTS fast |
| Grounding | Pixel precision | **Tolerant** — the pointer flies *after* speech starts |

`_NIMBUS_SYSTEM_PROMPT` is ~1,500 characters balancing "all lowercase, casual, warm, write
for the ear" against "return integer pixel coordinates". Two focused prompts beat one prompt
negotiating with itself.

#### Change list (sketch — design properly after `T1-8`)
1. `config.py` — optional `GROUNDING_PROVIDER` / `GROUNDING_MODEL`; **empty means use the
   single client**, preserving today's behaviour exactly.
2. `app.py::_pipeline_worker` — when set, fire both calls in parallel: speech streams to TTS
   immediately, geometry arrives during playback and drives the pointer.
3. Two prompts: `_NIMBUS_SPEECH_PROMPT` (no pointing rules) and `_NIMBUS_GROUNDING_PROMPT`
   (geometry only).

#### Tests to add
```python
def test_single_client_mode_is_default(self): ...
def test_split_mode_fires_both_clients(self, mocker): ...
def test_grounding_failure_still_speaks(self, mocker):
    """A grounding error must never silence the answer."""
def test_speech_failure_aborts_cleanly(self, mocker): ...
def test_cancel_aborts_both_clients(self, mocker):
    """Both must honour the cancel event — 11-checkpoint discipline extends here."""
```

That third test encodes the key safety property: **speech must never depend on grounding
succeeding.**

**Accuracy note:** specialised grounding models genuinely outperform generalists here.
Gemini Robotics-ER is tuned for pointing; in open models, Molmo was trained to point, and
UI-TARS / OmniParser target GUI grounding directly.

---

## 5. Tier 2 — Product wins

Each item maps to a documented, recurring complaint about tools in this category, or a
capability gap competing implementations shipped and this one hasn't. **All are
provider-independent** — none require a Gemini key.

**STATUS: complete. 757 → 988 tests passing (+231), zero regressions, `--selftest` OK.**
Every non-deferred item is done.

| Item | Status | Note |
|---|---|---|
| `T2-8` Aspect-correct capture | ✅ **done** | *Not in the original plan.* Found on real hardware; measured 4/6 → 6/6 grounding. See §5.0 |
| `T2-5` Code Mode | ✅ **done** | per-app prompt addenda; exe names verified on the machine, not guessed |
| `T2-2` Esc to cancel | ✅ **done** | machinery already existed; users simply could not reach it |
| `T2-1` Privacy Guard | ✅ **done, ON by default** | the one sanctioned exception to §1.3 |
| `T2-4` History screenshots | ✅ **done, off by default** | `HISTORY_IMAGE_COUNT=0`; all four providers convert properly |
| `T2-7` Hotkey capture widget | ✅ **done** | press-the-chord instead of typing it; no new validation logic |
| `T2-6` Overlay flicker | ⊘ **deferred by decision** | cosmetic payoff against the two highest-risk areas in §1.6. See §5.9 |
| `T2-3` Multi-step lessons | ⊘ **deferred by decision** | Tier 3 effort wearing a Tier 2 label. See §5.9 |

### 5.0 `T2-8` — the item the plan did not contain

Not in any tier when Tier 2 began. It surfaced from a throwaway diagnostic while
investigating an unrelated complaint, and turned out to be the largest remaining accuracy
defect on the maintainer's actual hardware.

`capture.pick_resolution` picks the closest-aspect entry from `CANDIDATE_RESOLUTIONS`, and
its docstring correctly states the goal is to avoid distortion. But that list spans aspect
**1.333 to 1.778**, and the monitor in question is 3840×1080 — **32:9, aspect 3.556**. The
closest entry was 1920×1080, so every screenshot was squashed **2× horizontally** before the
model saw it. Circular icons arrived as ellipses.

The algorithm was never at fault. The candidate *data* could not express the shape.

The diagnostic is in the capture result itself and is worth remembering:
**`scale_x != scale_y` means the image is geometrically distorted.** Real reading was
`scale=(2.00, 1.00)`.

Measured on a synthetic 32:9 desktop with six ground-truth targets, error in physical pixels:

| Strategy | scale | hits | median | max |
|---|---|---|---|---|
| 1920×1080 squashed (old) | (2.00, 1.00) | **4/6** | 4 px | **50 px** |
| 2560×720 aspect-correct | (1.50, 1.50) | 6/6 | 9 px | 15 px |
| 1920×540 aspect-correct | (2.00, 2.00) | 6/6 | 8 px | 11 px |
| 3840×1080 native | (1.00, 1.00) | 6/6 | 7 px | 10 px |

Both small icons were missed under the squash. The decisive detail: **1920×540 has fewer
pixels than the old squashed capture and still beat it outright**, so aspect fidelity matters
far more than resolution here and the fix costs nothing in tokens.

Fix: when the best candidate's aspect drifts more than `ASPECT_TOLERANCE` (5%), fall back to
a single uniform scale factor bounded by `MAX_MODEL_LONG_EDGE` / `MAX_MODEL_SHORT_EDGE`.
Checked *after* the never-upscale branch, so a 5:4 panel still returns native rather than
being routed through the fallback for a 6% drift.

Scoping is proven, not asserted: `TestAspectFidelity` parametrises 15 real monitor shapes and
asserts `scale_x == scale_y` for every one, plus an `UNCHANGED` table pinning 11 non-ultrawide
resolutions to byte-identical output. Only the four ultrawide rows changed.

Verified on the real monitor after the change: `target=2560x720 scale=(1.500, 1.500)`.

**Generalised lesson.** This is the second Tier-2-adjacent bug in a row where the *code* was
right and a *table of constants* was wrong, and both were invisible to review because the
logic reads correctly. Worth auditing the other fixed lists (`CANDIDATE_RESOLUTIONS` is done;
`_DIRECTIONAL_QUERY_WORDS` and `REFINEMENT_CROP_SIZE` are untested against unusual hardware).

---

### `[x] T2-1` — Privacy Guard (on by default) — *done, ON by default*

> **DONE.** New `privacy.py` holding `should_skip_capture()` as a **pure function** — no I/O,
> no clock, no global state — so the entire policy is exhaustively testable without mocks.
> That matters more here than elsewhere: a silently broken blocklist is worse than none,
> because the user believes they are protected.
>
> **Verification corrected the plan twice.**
>
> | Doc said | Reality |
> |---|---|
> | `get_foreground_app()` is in `capture.py` | it is in `app.py:397` |
> | **two** `capture_all_screens()` call sites to gate | there are **three** — press-time, release-time re-capture, and the realtime path |
>
> The third site is why the gate went into one shared helper, `_capture_screens_guarded()`,
> rather than being applied by hand at each site: all three repeated the same
> hide/wait/grab/show dance, and a fourth added later now inherits the guard for free.
> Invariant #3 is preserved, and the overlay is restored in a `finally` so a `grab()` failure
> cannot leave the pointer permanently invisible.
>
> **Suppressing capture does not abort the turn.** The helper returns `[]`, the pipeline
> continues voice-only, and the model is *told* it cannot see the screen — otherwise it
> answers as though it can and describes a screen it was never shown. Any coordinate it
> invents anyway is discarded, and annotation tags are still stripped from the spoken text
> even though the shapes are dropped.
>
> **Two policy bugs were caught by the new tests before shipping**, both real:
>
> 1. `\bpassword\b` blocked *any* page mentioning passwords — including documentation, which
>    is exactly the kind of page Nimbus is most useful on. Narrowed to credential context
>    (`enter/new/confirm/master/your password`, `password manager`, or a title starting with
>    the word).
> 2. The `.env` pattern required a preceding slash or space, so `config.env` was not matched.
>    Tightened to the literal extension instead, which still avoids matching "environment".
>
> Fails **open** on detection failure: `("unknown", "")` captures normally. Blocking is based
> on positive identification only, because failing closed would make a privacy feature
> indistinguishable from random breakage.
>
> Surfaced in Settings as its own visible **Privacy** group — not inside the collapsed
> experimental group — since it is on by default and is the setting that makes the dialog's
> "Nothing leaves your machine" line honest about screen contents rather than only about
> credentials.

**Strongest single differentiator available, and among the cheapest to build.**
Research across this category found **one** implementation shipping anything like it, and
none of the commercial products.

#### ⚠ VERIFY
1. Exact `.exe` basenames for target apps — launch each and read `get_foreground_app()`
   output. `KeePass.exe`, `Bitwarden.exe`, `1Password.exe` are guesses until confirmed.
2. `get_foreground_app()` returns `("unknown", "")` on failure — decide the fail-safe. **Fail
   *closed* (skip capture) is wrong here**: it would break Nimbus whenever foreground
   detection hiccups. Fail *open* but log.
3. Where the gate belongs. **Both** `_press_time_capture` and `_release_capture_worker` call
   `capture_all_screens()` — gate both, or gate inside a shared helper.

#### Current state
`capture_all_screens()` grabs every monitor unconditionally on every push-to-talk, with no
content awareness. The Settings label reads *"Nothing leaves your machine"* — true of
**credentials**, but users will read it as being about **screen contents**, which is not
accurate for cloud providers. This fix makes the existing claim honest.

#### Change list
1. `config.py` — `PRIVACY_GUARD` default `"on"`; `PRIVACY_GUARD_APPS` and
   `PRIVACY_GUARD_TITLES` with sensible defaults, user-extensible via Settings.
   **This is the one permitted exception to "new settings default to current behaviour"** —
   justified because the current behaviour is the defect. Note it in `README.md`.
2. New `privacy.py` (keeps `capture.py` free of policy):
   ```python
   def should_skip_capture(app_name: str, window_title: str) -> tuple[bool, str]:
       """Return (skip, human_readable_reason). Pure function — no I/O, fully testable."""
   ```
3. `app.py` — gate before capture; on skip, still run the interaction voice-only and emit
   `sig_show_toast` (`overlay.show_toast()` already exists).
4. `settings_dialog.py` — checkbox + editable list.

#### Tests to add — new `tests/test_privacy.py`

```python
class TestShouldSkipCapture:
    """T2-1: pure policy function. No I/O, no mocks needed."""

    @pytest.mark.parametrize("app", ["KeePass.exe", "keepass.exe", "Bitwarden.exe",
                                     "1Password.exe", "LastPass.exe"])
    def test_blocklisted_apps_skip(self, app): ...

    def test_app_match_is_case_insensitive(self): ...

    @pytest.mark.parametrize("title", ["Sign in - Google", "Online Banking",
                                        "config.env - VS Code", "Enter your password",
                                        "2FA verification code"])
    def test_blocklisted_titles_skip(self, title): ...

    def test_ordinary_app_does_not_skip(self): ...
    def test_unknown_app_does_not_skip(self):
        """Fail OPEN on detection failure — a hiccup must not break Nimbus."""
    def test_reason_string_is_user_presentable(self):
        """No regex source or exe paths in the toast text."""
    def test_disabled_guard_never_skips(self): ...
    def test_user_added_pattern_is_honoured(self): ...
    def test_malformed_user_regex_is_ignored_not_fatal(self):
        """A bad user pattern must not crash the pipeline."""
```

`tests/test_app.py`:

```python
def test_capture_skipped_when_privacy_guard_trips(self, mocker): ...
def test_interaction_still_answers_without_screenshot(self, mocker):
    """Voice-only answer must still work — skipping capture is not aborting."""
def test_toast_shown_when_capture_skipped(self, mocker): ...
```

#### Done when
All tests pass; manually confirmed by opening a password manager, holding the hotkey, and
seeing the toast with no screenshot in `~/.nimbus/debug/` (diagnostics temporarily on).

---

### `[x] T2-2` — <kbd>Esc</kbd> to cancel — *done*

> **DONE.** All the machinery already existed — `_cancel_event`, the 11 pipeline checkpoints,
> `tts.stop()` with its epoch counter. Only the way to reach it was missing, which is why
> this was among the cheapest items in the tier.
>
> Implemented by **extending the existing `pynput` listener**, as the item's `⚠ VERIFY` step
> recommended, rather than adding a second one: each listener installs its own
> `WH_KEYBOARD_LL` hook that runs on *every keystroke system-wide*, so a second hook would
> double that cost for every key typed anywhere in Windows in order to watch one key.
>
> Esc deliberately does **not** go through `parse_hotkey()`. That function rejects
> modifier-free chords so a Settings typo cannot turn ordinary typing into push-to-talk; that
> guard is correct and stays. Cancel needs the opposite — a bare key — so it takes a
> separate, narrower, non-configurable path. Asserted by
> `test_esc_is_not_routed_through_parse_hotkey`.
>
> **Gating is the whole design.** Esc is among the most-pressed keys on a keyboard, so firing
> unconditionally would mean Nimbus reacting to every dialog dismissal and vim escape in the
> session. `_is_response_in_flight()` reports two phases, because the user perceives both as
> "Nimbus is busy": the worker still running, **and** TTS still speaking, which outlives the
> worker. `hotkey.py` stays stateless about the pipeline — it asks, the app answers.
>
> Both the predicate and the callback are exception-swallowed. They run on the pynput listener
> thread, where an escaping exception kills the listener and the hotkey silently stops working
> for the rest of the session — far worse than a cancel that did not happen.
>
> `_on_cancel` reuses `_handle_press`'s abandon sequence exactly, including the 200 ms TTS
> grace window: `tts.stop()` cuts playback mid-word but the speaker keeps decaying, and
> without the window that decay is picked up by the mic and contaminates the *next*
> transcript. Nothing is written to memory, so an abandoned turn cannot pollute per-app
> history with an answer the user rejected.

#### ⚠ VERIFY
1. `PushToTalkHotkey` currently owns the only `pynput` listener. Decide: extend it with an
   optional cancel key, or add a second listener. **Extending is preferred** — two
   `WH_KEYBOARD_LL` hooks doubles the hook cost on every keystroke system-wide.
2. Confirm `parse_hotkey()` is **not** used for the cancel key. It deliberately rejects
   modifier-free chords so a Settings typo cannot turn typing into push-to-talk; that guard
   is correct and must stay. Cancel needs a separate, narrower path.
3. Confirm which state means "in flight" — `self._worker_thread.is_alive()` and/or TTS
   playing. Esc must be ignored otherwise, or Nimbus interferes with every Esc press.

#### Current state
The only way to stop a response is pressing the hotkey again, which starts a *new*
interaction. All the machinery exists — `_cancel_event`, 11 checkpoints, `tts.stop()` with an
epoch counter, `realtime.stop()`. What's missing is a way for the user to reach it.

#### Change list
1. `hotkey.py` — optional `on_cancel` callback, fired on bare Esc, **only when the app
   reports a response in flight** (pass a predicate, keep `hotkey.py` stateless about the
   pipeline).
2. `app.py` — `sig_cancel` signal → main-thread slot performing the same sequence
   `_handle_press` already does: set `_cancel_event`, `tts.stop()`,
   `set_tts_grace_until(now + 0.2)`, `sig_hide_spinner`, `sig_clear_annotations`.
3. Mention it in `onboarding.py`'s welcome dialog.

#### Tests to add — `tests/test_hotkey.py`

```python
def test_esc_fires_cancel_when_in_flight(self): ...
def test_esc_ignored_when_idle(self):
    """Must not interfere with every other Esc press in the session."""
def test_esc_does_not_affect_ptt_state_machine(self):
    """Esc must not corrupt the IDLE/RECORDING transitions."""
def test_cancel_callback_optional(self):
    """Backward-compat: existing construction without on_cancel still works."""
def test_esc_during_recording_does_not_fire_release(self): ...
```

`tests/test_app.py`:

```python
def test_cancel_stops_tts_and_clears_overlay(self, mocker): ...
def test_cancel_sets_tts_grace_window(self, mocker):
    """Otherwise the aborted TTS tail contaminates the next transcript."""
def test_cancel_does_not_record_memory(self, mocker):
    """An aborted turn must not be written to per-app memory."""
```

That last test guards a real hazard: partial interactions polluting memory.

---

### `[x] T2-5` — Code Mode — *done*

> **DONE.** New `prompts.py`: `APP_PROMPT_ADDENDA` keyed by sanitised exe basename, with
> `addendum_for_app()` / `apply_app_addendum()`. Covers code editors, browsers and terminals.
>
> **Exe names were verified, not guessed.** The item's `⚠ VERIFY` step said to confirm
> basenames rather than assume them, so installed software and running processes were
> enumerated on the machine: `Kiro.exe`, `notepad++.exe` and `devenv.exe` are confirmed
> present. `Kiro.exe` was not in the doc's list at all and is the maintainer's primary
> editor — it would have been the one app Code Mode missed. Well-known names for the rest of
> the field are included as best-effort; a non-matching key is inert.
>
> #### §5.5 The interaction that would have silently broken pointing
>
> `GeminiNativeClient` identified Nimbus's own prompts by **equality** in two places: to swap
> in the structured tool-based prompt, and to decide whether to fire the geometry call at
> all. Appending `"this is a code editor…"` makes a Nimbus prompt unequal to the base, so on
> the native path Code Mode would have:
>
> 1. skipped the structured-prompt swap, and
> 2. set `wants_geometry = False` — **no geometry call, no pointer, ever**, in every code
>    editor.
>
> It would still have answered perfectly, which is what makes it nasty: nothing errors, and
> the only symptom is "it stopped pointing in my editor". Caught by reasoning about the
> interaction before writing the wiring, not by a test run.
>
> Fixed by making both checks **prefix-based** and routing them through one helper,
> `_is_structured_nimbus_prompt`, so the prompt swap and the geometry decision can no longer
> disagree about what counts as "ours" — which is exactly how they diverged. `locator.py`'s
> genuinely custom refinement prompt still passes through untouched, asserted by
> `test_genuinely_custom_prompt_is_still_passed_through`.
>
> Two rules are enforced by test rather than convention: the addendum is **appended, never
> substituted** (the base prompt carries the persona, the write-for-the-ear contract and the
> pointing rules), and keys go through `memory._sanitize_app_name` so they match the folder
> names users already see and cannot drift.
>
> One addendum was rewritten because a test rejected it: the browser text gave no
> spoken-output guidance, which would have let the model read URLs aloud character by
> character.

#### ⚠ VERIFY
Exact `.exe` basenames by launching each editor and reading `get_foreground_app()`:
`Code.exe` (VS Code), `Cursor.exe`, `devenv.exe` (Visual Studio), `idea64.exe`,
`pycharm64.exe`, `sublime_text.exe`, `zed.exe`, `nvim.exe`. Confirm — do not guess.

#### Change list
1. New `prompts.py` (or a constant in `ai.py`): `APP_PROMPT_ADDENDA: dict[str, str]`,
   keyed by sanitised exe name. **Reuse `memory._sanitize_app_name`** so keys match the
   existing convention users already see in their memory folder.
2. `app.py::_pipeline_worker` — append the addendum to the system prompt when matched.
3. Extensible for spreadsheets, design tools, video editors later.

#### Tests to add — new `tests/test_prompts.py`

```python
class TestAppPromptAddenda:
    @pytest.mark.parametrize("exe", ["Code.exe", "code.exe", "Cursor.exe", "idea64.exe"])
    def test_known_editors_get_code_addendum(self, exe): ...
    def test_unknown_app_gets_no_addendum(self): ...
    def test_lookup_is_case_insensitive(self): ...
    def test_addendum_appended_not_replacing_base_prompt(self):
        """The persona must survive — this is an addendum, not a substitution."""
    def test_sanitisation_matches_memory_module(self):
        """Drift guard: keys must match memory._sanitize_app_name output."""
```

`tests/test_app.py`: `test_code_mode_addendum_reaches_ask_stream`.

That fourth test matters — accidentally replacing rather than appending the prompt would
silently destroy the "write for the ear" and pointing contracts.

---

### `[x] T2-4` — Keep screenshots in conversation history — *done, off by default*

> **DONE.** `HISTORY_IMAGE_COUNT` defaults to **0**, so the default path is byte-identical
> text-only behaviour (§1.3). Capped at 3: screenshots dominate token cost, and a stale screen
> actively misleads because the user has usually moved on.
>
> **Verification confirmed the plan's warning and raised the stakes.** All four history
> converters dropped non-text blocks *by omission* — the OpenAI-compat, Ollama and native
> Gemini paths each walked `content` looking only for `type == "text"`. Only Anthropic would
> have worked, because `_history` is already in its block format. So enabling the setting
> would have appeared to work while being **inert on the provider the maintainer actually
> uses**.
>
> Fixed by adding one canonical block shape plus a shared walker in `ai.py`
> (`history_image_block`, `iter_history_blocks`, `history_text`), then translating per provider
> — four genuinely different wire formats:
>
> | Provider | History image format |
> |---|---|
> | Anthropic | content blocks, native — passes through |
> | OpenAI / Gemini shim | `image_url` part with a `data:` URI |
> | Ollama | sibling `images` array of bare base64 |
> | Gemini native | `types.Part.from_bytes` |
>
> Two provider-specific hazards handled: Gemini native never attaches an image to a `model`
> turn (the model did not send one, and it is rejected), and a corrupt base64 payload is
> skipped rather than failing a live request.
>
> Images are downscaled by `HISTORY_IMAGE_SCALE` (0.5, a quarter of the pixels). A history
> screenshot only needs to support *recognition* — "the blue button you mentioned" — never
> fresh grounding, which always uses the current turn's full-resolution capture.
>
> Eviction runs **after** the 10-exchange trim and strips images only, leaving old text intact:
> text is cheap and stays useful, screenshots are expensive and go stale.
>
> The export privacy property survived and is now pinned by test: `_history_message_text`
> still excludes image payloads, so nothing base64 reaches the user's Documents folder. An
> image-only turn exports as empty rather than as base64.
>
> `T1-6a` cache interaction checked, as the item required: none. The cache carries the system
> instruction only, while history rides in `contents`, so history images cannot invalidate a
> cached prefix.

#### ⚠ VERIFY
1. Per-provider history image formats — Anthropic content blocks, OpenAI `image_url`, Ollama
   `images` array, Gemini native. Each differs.
2. Token cost of one downscaled screenshot, to size the budget.
3. Interaction with `T1-6a` caching — `ai.py`'s comment is right: cache stable prefixes,
   never the current turn. **Sequence after caching is settled.**

#### Current state
`_MAX_HISTORY_EXCHANGES = 10`, text only. `GeminiClient.ask_stream()` confirms it: history
converts by concatenating text blocks and dropping everything else. Follow-ups like *"what
about that button you pointed at?"* reach a model with no record of the previous screen.

#### Change list
1. `config.py` — `HISTORY_IMAGE_COUNT` default **`0`** (current behaviour, §1.3), and a
   downscale factor.
2. `app.py` — retain the last N cursor-screen captures in `_history`, downscaled.
3. Each provider's history converter handles images or drops them gracefully.

#### Tests to add
```python
def test_history_image_count_defaults_to_zero(self):
    """Backward-compat: default must reproduce today's text-only behaviour."""
def test_images_retained_up_to_configured_count(self): ...
def test_oldest_image_evicted_first(self): ...
def test_history_trim_still_respects_max_exchanges(self):
    """T2-4 regression: the 10-exchange cap must still hold."""
def test_provider_without_history_images_drops_them_cleanly(self, mocker): ...
def test_export_session_history_still_excludes_images(self):
    """_history_message_text deliberately excludes image payloads from Documents
    exports. That privacy property must survive."""
```

That last test guards an existing deliberate decision that this change could silently break.

---

### `[-] T2-3` — Local multi-step lesson state `DEFERRED`

**Deferred by decision — see §5.9.** Tier 3 effort in practice. Kept fully specified below so
it can be picked up unchanged.

**Depends on `T1-2`** (structured multi-step output).

#### ⚠ VERIFY
Can the model reliably return discrete steps each with its own geometry? Test the schema
before building the state machine.

#### Change list
1. Structured multi-step response: each step carries text + optional geometry.
2. `lessons.py` — pure state machine: `advance()` / `back()` / `repeat()` / `clear()`.
3. Intent matching on the transcript ("next" / "continue" / "repeat" / "back" / "stop") →
   handled **locally, with no API call**. Keyword list initially; `_looks_directional()` is
   the precedent.
4. Persist in the existing SQLite database so state survives restarts.

#### Tests to add — new `tests/test_lessons.py`
```python
class TestLessonState:
    """Pure state machine — exhaustively testable with no mocks."""
    def test_advance_moves_to_next_step(self): ...
    def test_advance_past_last_step_completes(self): ...
    def test_back_from_first_step_stays(self): ...
    def test_repeat_does_not_change_index(self): ...
    def test_clear_resets(self): ...
    def test_state_survives_roundtrip_through_sqlite(self, tmp_path): ...

class TestLessonIntent:
    @pytest.mark.parametrize("t,expected", [
        ("next", "advance"), ("continue", "advance"), ("repeat that", "repeat"),
        ("go back", "back"), ("stop", "clear"),
        ("what is a pivot table", None),   # must NOT be treated as navigation
    ])
    def test_intent_matching(self, t, expected): ...
```

That final negative case is essential — a false positive would hijack a normal question.

`tests/test_app.py`: `test_navigation_makes_no_api_call` — the whole point of the feature.

---

### 5.9 Why `T2-6` and `T2-3` are deferred

Both were deferred on recommendation and confirmed by the maintainer. Recorded here because
"we ran out of time" and "we decided not to" are different things, and only the second is
worth trusting later.

**`T2-6` overlay flicker — deferred on risk/reward.** The payoff is cosmetic: removing up to
two 50 ms blanks per interaction. The cost is touching two of the highest-risk areas in §1.6
— the invariant that overlays hide before `mss.grab()`, and the Win32 click-through
restyling. Breaking the first means the model sees Nimbus's own pointer and points at it;
breaking the second means an overlay that swallows clicks. Both are severe, and neither is
worth a flicker. Its own `⚠ VERIFY` step already flagged that option 1 may be invalid,
because the model receives *all* screens and a cursor on a secondary display would still
mislead it.

**`T2-3` multi-step lessons — deferred on scope.** It needs a structured multi-step schema, a
persisted state machine, and local intent matching. That is genuinely Tier 3 effort, and the
roadmap already parked it in a later phase; listing it under Tier 2 understated it.

---

### `[-] T2-6` — Reduce overlay hide/show flicker `DEFERRED`

#### ⚠ VERIFY
1. Whether hiding only the target monitor's overlay is sufficient — the model receives **all**
   screens, so a cursor visible on a secondary screen could still mislead it. **This may
   invalidate option 1**; check before building.
2. For option 3, the Win32 window-affinity API and whether it affects `mss` (which reads the
   desktop, and may not honour affinity flags that only affect certain capture paths).

#### Current state
Up to two hide/show cycles per interaction (press, plus release when the cursor moved
>150 px), each with a 50 ms blank, each re-applying Win32 click-through styles.

#### Change list — increasing effort
1. Hide only the overlay for the monitor being grabbed — **pending verification step 1.**
2. Keep windows mapped: set `_pointer_visible = False` + synchronous repaint instead of
   `hide()`. Avoids the show/restyle cycle entirely. **Likely the best risk/reward.**
3. OS-level capture exclusion so no hiding is needed at all.

#### Tests to add
```python
def test_pointer_hidden_without_window_hide(self, mocker):
    """Option 2: window stays mapped, pointer not painted."""
def test_clickthrough_styles_not_reapplied_when_window_stays_mapped(self, mocker): ...
def test_capture_still_excludes_cursor(self, mocker):
    """The invariant this whole mechanism exists to protect."""
def test_all_overlays_restored_after_capture(self, mocker): ...
```

**Blast radius warning:** touches invariant 3 (overlays hide before `mss.grab()`) and the
Win32 click-through path — two of the highest-risk areas in §1.6. Full smoke test on a
multi-monitor mixed-DPI setup is mandatory.

---

### `[x] T2-7` — Hotkey capture widget in Settings — *done*

> **DONE.** `HotkeyCaptureButton` in `settings_dialog.py`: click, press the chord, it records
> it. The text field is kept as an advanced fallback, and the capture button mirrors into it
> so the save path still reads exactly one value and the two controls cannot disagree.
>
> **No new validation, as specified.** `parse_hotkey()` keeps sole ownership of the grammar,
> the normalised display form, and the tailored conflict messages. The widget captures keys,
> hands the string over, and shows whatever comes back. Verified live: pressing Alt+Space
> displays *"Alt+Space opens the Windows window menu; add Ctrl or choose another key."* — the
> existing message, not a new one.
>
> The risk was never validation. It was that Qt key codes and pynput key objects are
> unrelated vocabularies, and a mistranslation records cleanly in Settings then never fires
> at runtime. So the mapping is a pure function over primitives,
> `qt_key_event_to_hotkey_string`, unit-testable with no widgets and no event loop.
>
> #### ⚠ VERIFY — what checking Qt's real values changed
>
> The verify step named `Key.alt_gr` and left/right modifier variants. Both mattered, and a
> third issue was found that the plan did not anticipate:
>
> | # | Finding | Consequence |
> |---|---|---|
> | 1 | **Windows reports Shift+Tab as `Key_Backtab` (0x01000002), not `Key_Tab`** | A `shift+tab` chord was silently unrecordable — the widget would appear to ignore the key. Not in the plan. |
> | 2 | **Windows reports AltGr as Ctrl+Alt** | Needs *no* correction: `hotkey._is_alt` already lumps `alt_gr` with `alt`, so the recorded `ctrl+alt+<key>` is exactly what fires at runtime. Worth knowing rather than fixing. |
> | 3 | `Key_A == ord('A')`, `Key_0 == ord('0')`, F1–F12 contiguous | Letters and digits need no lookup table; F-keys are simple offset arithmetic. |
> | 4 | **Qt consumes Tab for focus navigation before `keyPressEvent`** | Without overriding `focusNextPrevChild`, a Tab chord could never be captured at all. |
>
> #### Mutation testing — one comment was overstating itself
>
> After the tests passed, three deliberate mutations were introduced to check the tests
> actually catch what they claim:
>
> | Mutation | Result |
> |---|---|
> | Remove the `Key_Backtab` mapping | **caught** (1 failure) |
> | Break the F-key offset by one | **caught** (3 failures) |
> | Remove the bare-modifier guard | **not caught — 45/45 still green** |
>
> The third is a genuine finding. Every modifier key code already falls outside all accepted
> trigger ranges, so it reaches the same `return None` at the bottom of the function; the
> explicit guard is belt-and-braces, not load-bearing. The tests are still correct — they
> assert the *behaviour*, which holds either way — but the code comment implied the guard was
> what made it work. Comment corrected to say so plainly, and the guard kept for intent and
> for safety if the accepted ranges are ever widened.
>
> This is worth repeating on other items: passing tests prove the behaviour, not that each
> line you wrote is the reason for it.

#### Change list
`settings_dialog.py` — a capture widget: click, press a chord, it records and displays it,
validates via the existing `parse_hotkey()`, shows the error inline. Keep the text field as
an advanced fallback.

#### Tests to add
```python
def test_captured_chord_normalises_to_parse_hotkey_format(self): ...
def test_rejected_chord_shows_inline_error(self): ...
def test_alt_gr_normalises_to_alt(self):
    """Parity with hotkey._is_alt, which lumps alt/alt_l/alt_r/alt_gr."""
def test_text_fallback_still_accepted(self): ...
def test_known_bad_chords_rejected_with_specific_message(self):
    """alt+space, ctrl+shift+space, ctrl+space each have a tailored message."""
```

**No new logic** — `parse_hotkey()` already does validation, normalisation, and messaging.
This only surfaces it.

---

## 6. Tier 3 — Depth & differentiation

Larger items. Each still requires the §1.2 Definition of Done and a test plan; the plans here
are outlines to be expanded when the item is scheduled.

**Scope decided 2026-08-15.** Three items in, five out. The five exclusions each have a
different reason, and the reasons matter more than the count:

| Item | Status | Reason |
|---|---|---|
| `T3-3` Knowledge Journal | ✅ **done, ON by default** | strongest education feature; two-thirds already existed in `memory.py`'s database |
| `T3-5` Richer annotations | ✅ **done** | consumes `T1-2` structured geometry directly; also fixed a latent `Rect` bug |
| `T3-2` Knowledge base expansion | ✅ **done** | folders + PDF/DOCX via local extraction, plus relevance ranking. Found and fixed a real sanitiser drift. `T1-6c` proved **unnecessary**, not outstanding |
| `T3-6` Lesson recording | ⊘ deferred | **a different approach is planned** — not cost or risk |
| `T3-1` Gated Computer Use | 🚩 **skipped outright** | reliability and blast radius; now a §8 non-goal, not a backlog item |
| `T3-4` Continuous awareness | ⊘ deferred | most privacy-threatening feature in the document |
| `T3-7` Win32 hotkey listener | ⊘ deferred | 8–12h of fragile ctypes for a caveat nobody has hit |
| `T3-8` Workflow capture | ⊘ deferred | keylogging, for a speculative payoff |

Also outstanding from earlier tiers, both deferred with reasons at §5.9: `T2-3` multi-step
lessons and `T2-6` overlay flicker.

---

### `[x] T3-3` — Knowledge Journal with spaced repetition — *done, ON by default*

> **DONE.** New `review.py`: SM-2-style scheduling, a `review_queue` table in the existing
> database, and local intent matching. `KNOWLEDGE_JOURNAL` defaults **on** — purely additive,
> so §1.3 does not apply: a new table alongside `apps`, written only after an interaction has
> already succeeded.
>
> **All three `⚠ VERIFY` points confirmed, none needed changing.** `CREATE TABLE IF NOT
> EXISTS` was sufficient with no `ALTER`; `config.INSIGHTS_PATH` was defined and written by
> nothing (only a docstring mention at `memory.py:26`), so it was free to use; WAL was already
> enabled. A rare item where the plan survived contact intact.
>
> #### What makes it more than a flashcard app
>
> Review items carry an optional `target_label`, populated from the element Nimbus pointed at.
> That makes an item **positional**: it can later be asked as *"show me where the export
> button is"* and graded against a real grounding call. No flashcard tool can ask that,
> because none of them can see the screen. It costs nothing to capture — the label is already
> in the result.
>
> #### Design decisions
>
> - **A fixed interval ladder** (1 → 3 → 7 → 14 → 30 → 60 → 120 days) scaled by an ease
>   factor, rather than SM-2's computed interval. The classic formula misbehaves on small
>   datasets, which is exactly what a personal journal is. A ladder is predictable and
>   explainable to the user.
> - **A wrong answer resets to the start**, not back one rung. Stepping back would keep a
>   genuinely unknown item circulating at week-long gaps.
> - **Ease moves asymmetrically** — failure penalises more than success rewards, because
>   getting something right once is weak evidence and getting it wrong is strong evidence.
> - **Capped at 120 days**, because software changes: an item from a year ago may describe a
>   UI that no longer exists.
> - **Never returns a zero-day interval**, or an item would be re-asked in the same session.
>
> #### The false-positive guard is the interesting part
>
> Intent matching runs locally with no API call — navigating your own journal should be free.
> But a false positive silently replaces a genuine answer with a quiz, which is worse than
> not having the feature. So `classify_review_intent` requires the transcript to be
> *predominantly* a command, capped at six words. Pinned by tests over the dangerous
> near-misses:
>
> | Transcript | Result |
> |---|---|
> | "quiz me" | `quiz` |
> | "how would you quiz me on this spreadsheet formula" | `None` — real question |
> | "what did we cover in the meeting about the quarterly budget review" | `None` |
>
> Journal commands are checked **before capture**, so a question about the user's own journal
> never takes a screenshot — less work and one less privacy exposure.
>
> Every write is swallowed and logged. The journal is written *after* the user already has
> their answer, so losing an entry is invisible, whereas raising would surface as a failed
> interaction. A broken journal degrades to a normal answer, asserted by
> `test_journal_failure_falls_back_to_the_pipeline`.

**Original plan (kept for context).** Strongest available Education-track feature, and
two-thirds of it already exists.

#### ⚠ VERIFY
1. `memory.py`'s SQLite schema and whether `CREATE TABLE IF NOT EXISTS` migration is
   sufficient for adding a table (it is — but confirm no `ALTER` is needed on `apps`).
2. `config.INSIGHTS_PATH` is defined and **nothing writes it** — confirm it is free to use.
3. WAL mode is already enabled; confirm concurrent read/write from the Qt main thread only
   (the documented single-writer model).

#### Change list
1. New `review_queue` table in the existing database: question, answer, app, first_learned,
   next_review, interval_index, ease.
2. SM-2 style scheduling, intervals 1 → 3 → 7 → 14 → 30 → 60 → 120 days.
3. Voice intents: *"what did we cover today?"*, *"what should I review?"*, *"quiz me."*
4. Quiz mode grades the spoken answer and adjusts the interval. **Screen awareness is the
   differentiator over any flashcard app** — it can ask *"show me where the colour grading
   panel is"* and verify against a grounding call.
5. Progress written to `INSIGHTS_PATH`, honouring the human-readable transparency contract.

#### Tests to add — new `tests/test_review.py`
```python
class TestSM2Scheduler:
    """Pure scheduling math — no I/O."""
    def test_first_correct_answer_sets_one_day_interval(self): ...
    def test_consecutive_correct_advances_interval(self): ...
    def test_incorrect_answer_resets_interval(self): ...
    def test_interval_never_exceeds_maximum(self): ...
    def test_ease_factor_bounded(self): ...

class TestReviewQueue:
    def test_due_items_returned_in_order(self, tmp_path): ...
    def test_future_items_not_due(self, tmp_path): ...
    def test_schema_created_idempotently(self, tmp_path):
        """Matches memory.py's CREATE TABLE IF NOT EXISTS contract."""
    def test_existing_apps_table_untouched(self, tmp_path):
        """Backward-compat: adding a table must not disturb existing memory data."""
```

That last test is the backward-compatibility gate — users have existing databases.

---

### `[x] T3-2` — Knowledge base: multi-file, folders, PDFs — *done*

> **DONE.** `KB_DIR/<app>.md` still works untouched; `KB_DIR/<app>/` now accepts any number of
> `.md`, `.txt`, `.pdf` and `.docx` files, read recursively in stable alphabetical order. Both
> layouts can coexist, flat file first.
>
> #### The `⚠ VERIFY` step found a real defect
>
> Item 1 required `kb._sanitize_app_name` to stay **byte-identical** to
> `memory._sanitize_app_name`. Its docstring claimed it "mirrors memory exactly". **It did
> not** — measured across 15 inputs, **7 disagreed**:
>
> | Input | `memory` | `kb` (before) |
> |---|---|---|
> | `"  spaced.exe  "` | `spaced.exe` | `  spaced.exe  ` |
> | `"app?.exe"` | `app_.exe` | `app?.exe` |
> | `"pipe|app.exe"` | `pipe_app.exe` | `pipe|app.exe` |
>
> `memory` strips surrounding whitespace and replaces all nine Windows-reserved characters;
> `kb` stripped nothing and replaced three. The reserved-character cases are theoretical
> (illegal in filenames anyway), but **the whitespace case broke the documented mental model**:
> users are told to read the canonical name out of `~/.nimbus/memory/` and name their KB file
> to match, and for such an app memory showed `spaced.exe` while `kb` looked for
> `  spaced.exe  ` and silently found nothing.
>
> Fixed by **delegating** rather than re-syncing two copies, so they can never drift again.
> Guarded by a 15-case parametrised drift test.
>
> #### Deviation: no Files API, and `T1-6c` is now unnecessary
>
> The plan specified PDFs via the Gemini **Files API** (`T1-6c`). Built with **local text
> extraction** instead, deliberately:
>
> - `kb_content` is a **string** injected into the system prompt and flows
>   provider-agnostically through the entire pipeline. A file-reference path would fracture
>   that contract.
> - It is **Gemini-only**, so PDFs would not work on the fully-local Ollama path — a §1.6
>   regression gate.
> - Extracted text stays **inspectable**, honouring the transparency contract.
>
> So `T1-6c` is **not outstanding — it is unnecessary**, and its row should be read that way.
>
> Extractors are lazily imported and individually optional: a missing `pypdf` skips PDFs with
> a log rather than breaking the knowledge base, mirroring how `faster_whisper` and
> `kokoro_onnx` degrade. `pypdf` is pure Python with no native dependencies, which matters for
> the PyInstaller build.
>
> #### Over-budget behaviour changed, and this is the substantive win
>
> Previously an over-budget file was **tail-truncated** — Nimbus read the last 60,000
> characters and silently discarded everything before it, so a question about the discarded
> part was answered from nothing. Now, when a query is supplied, sections are split on
> Markdown headings, scored by **distinct** query-term overlap, and the best kept until the
> budget fills, re-emitted in original document order.
>
> Distinct terms rather than occurrence counts on purpose: a section repeating one word fifty
> times is not more relevant than one covering five of the query's words, and counting
> occurrences would let a glossary entry outrank the page that answers the question.
>
> Callers passing no query keep the exact previous behaviour, so this is additive.
> **Keyword-based, not embeddings** — §8's judgement against premature vector-DB complexity
> stands, and a keyword score is inspectable in a way a cosine distance is not.
>
> #### Live verification: the in-house software case
>
> A knowledge base for a fictional internal tool (`orionflow.exe`) built from a Markdown
> overview, a **DOCX shortcut table**, and a PDF, then the same question asked twice:
>
> | | Answer |
> |---|---|
> | **Without** KB | *"shift plus command plus r"* — hallucinated, and `command` is not even a Windows key |
> | **With** KB | *"control shift q"* — correct, extracted from the DOCX **table**, and it volunteered `F9` from the same table |
>
> That is the entire justification for the item: public tools are in the training data,
> internal tools are not, and no amount of screen-reading recovers a convention that exists
> only in a wiki. PDF extraction verified separately against a hand-built PDF containing real
> text — a blank page would only have proven "does not raise".
>
> #### Discoverability — the follow-up, now closed
>
> The first pass wrote a guide to `KB_DIR/README.md` by hand. But `Nimbus Wiki/` is
> **gitignored**, so that file was local-only and would never have reached a user.
>
> The feature itself always shipped — `kb.py` is code and `config._resolve_kb_dir` creates the
> folder at startup. What did not ship was **any explanation**, and the naming convention
> (`orionflow.exe.md`, matching the executable) is not guessable. A new user would have seen an
> empty folder and never used the feature at all. A capability nobody can discover is
> functionally absent.
>
> Two fixes:
>
> 1. **`kb.ensure_guide()`** seeds the guide on startup, writing only when absent so a user's
>    own edits survive. The text is **embedded as a string** in `kb.py` rather than shipped as a
>    PyInstaller `datas` entry — a data file would need `sys._MEIPASS` resolution that differs
>    between the frozen build and a source checkout, which is a recurring "works in dev, missing
>    in the installer" failure. 3 KB of text in the module cannot go missing. Failures are
>    swallowed: help text must not block startup.
> 2. **An "Open knowledge base folder…" button in Settings**, with a tooltip carrying the naming
>    pattern and formats. It re-seeds before opening, covering folders created by an earlier
>    version. Unlike the startup call this reports failures, because the user clicked something.
>
> A latent fragility was fixed along the way: the handler originally read `KB_DIR` **twice**,
> once via `kb.py`'s own `from config import KB_DIR` and once directly, so it could seed one
> folder and open another. It now derives the folder from the path `ensure_guide` returns.
>
> Verified by deleting the guide, running `--selftest` (correctly does **not** seed — it only
> imports), then launching the app and confirming the file reappeared.

#### `T3-2` also exposed a UI regression from earlier tiers

The Settings dialog had grown past a **1366×768 laptop**: 744 px of content, 783 with the
window frame, against 728 usable. The Save button would have been off-screen on a dialog that
is **modal at first launch**, so setup could not have been completed at all.

Nothing in `T3-2` caused it — the growth came from Tiers 1–3 (the Privacy group, the
experimental group, the restart note, the hotkey capture row) and `T3-2`'s own button was the
last straw. It was invisible here because this machine has 1040 usable pixels.

Fixed by wrapping the content in a `QScrollArea` with the **button box outside it**. That
placement is the load-bearing detail: a fully scrolled dialog can still hide Save below the
fold, whereas a pinned button box cannot, however many settings are added later.

Scrolling alone was not sufficient. A scrollable dialog opens at its *minimum* size, which was
about 111 px — a letterbox. `_size_to_screen` asks the **page** for its natural height and
clamps to 88% of the screen. Asking the dialog's own layout gave 426 px, because a
`QScrollArea` reports its own small hint rather than its child's.

Now: 790 px on this display, scrolls on a 768-tall laptop, every control reachable on both.
Guarded by `TestSettingsFitsSmallScreens`, including a parametrised fit check across
768/900/1080/1440 and an inventory test so the refactor cannot silently drop a widget.

**Original plan (kept for context).** Depended on `T1-6c` (Files API) for PDFs — superseded,
see above.

#### ⚠ VERIFY
1. `kb._sanitize_app_name` must stay **byte-identical** to `memory._sanitize_app_name`.
   Users navigate by matching filenames across both folders. Add a drift-guard test.
2. Existing flat `<app>.exe.md` files must keep working — this is additive.

#### Change list
1. Folder per app, all Markdown concatenated (flat file still supported).
2. PDF/DOCX via Files API.
3. URL ingestion via URL context (`T1-5`).
4. Relevance selection when over budget: **keyword overlap first.** Only consider embeddings
   if measurement proves keyword ranking insufficient — `memory.py` argues explicitly against
   premature vector-DB complexity and that judgement stands (§8).

#### Tests to add
```python
def test_flat_file_still_works(self, tmp_path):
    """Backward-compat gate — existing user files must not break."""
def test_folder_contents_concatenated_in_stable_order(self, tmp_path): ...
def test_over_budget_content_ranked_then_truncated(self, tmp_path): ...
def test_sanitize_matches_memory_module_exactly(self):
    """Drift guard across kb.py and memory.py."""
def test_unreadable_file_skipped_not_fatal(self, tmp_path):
    """KB files are user-controlled; a bad encoding must not break the pipeline.
    app.py already wraps kb.recall in try/except — keep that true."""
```

---

### `[x] T3-5` — Richer annotation vocabulary — *done*

> **DONE.** Three new shapes — `Rect`, `Highlight`, `StepBadge` — on **both** paths: tag
> parsing for text-based providers and function-calling tools (`draw_box`,
> `highlight_region`, `mark_step`) for the native Gemini path. Building only the tag path
> would have left the new shapes unavailable on the default provider.
>
> #### A latent bug this uncovered
>
> `Rect` was added in **T1-2** to carry structured `box_2d` output. Neither
> `app._annotations_to_physical` nor `overlay.annotations_to_local` ever learned about it, so
> every rectangle fell through both `isinstance` dispatches and was **silently discarded**
> before reaching the screen. The `draw_box` tool could fire, return valid geometry, and
> nothing would ever appear.
>
> Nothing errored, which is why it survived: an unhandled shape is simply not drawn. Now
> guarded at both boundaries by `test_rect_is_no_longer_dropped`.
>
> #### Structural fix to a repeat hazard
>
> The tag keyword list was previously written out **twice** — once in the complete-tag strip
> regex and once in the fail-closed unterminated-tag regex. Adding a shape meant remembering
> both, and forgetting the second would let a *truncated* tag's coordinates be read aloud.
> That is the worst failure mode available here, and it would pass every happy-path test,
> because only a cancelled or token-limited stream produces an unterminated tag.
>
> Now one `_SHAPE_KEYWORDS` constant is interpolated into both, with
> `test_every_shape_keyword_is_in_both_strip_regexes` proving they cannot diverge.
>
> #### Rendering decisions
>
> - **Highlight paints in a separate first pass**, before every other shape. A dim layer drawn
>   afterwards would darken the very annotations it exists to draw attention to. Done as a
>   distinct pass rather than relying on list order, because the *model* controls that order
>   and must not be able to break the visual.
> - Implemented as **four rectangles around the target**, not a clip path — cheap, exact, no
>   seams — with the clear region outlined so the target is identifiable on an already-dark
>   screen rather than merely "the bit that did not change".
> - `Rect` is **outline only**, never filled, following the same rule as `Circle`: frame the
>   control, leave it readable.
> - `StepBadge` **is** filled, because unlike the framing shapes it is a marker in its own
>   right and must read against arbitrary backgrounds.
>
> The prompt gained *when to use* guidance, not just syntax — prefer `RECT` over `CIRCLE` for
> box-shaped controls, at most one `HIGHLIGHT` per reply (two competing dim layers cancel out),
> number `STEP` badges from 1 in spoken order.

**Original plan (kept for context).** `T1-2` should land first so geometry arrives structured
rather than via more regex.

#### Change list
- **`Rect`** — direct consumer of `box_2d`. Frames a control correctly where a circle with a
  guessed radius clips or over-covers.
- **Numbered step badges** (①②③) — visual counterpart to `T2-3`.
- **Highlight-dim** — dim everything except the target. Most legible way to direct attention
  in a dense UI; the per-monitor overlay makes it straightforward.
- **Polygon / freehand** — irregular regions.

#### Tests to add
```python
# tests/test_annotations.py — mirror the 18 existing shape tests
def test_rect_parses(self): ...
def test_rect_tag_stripped_from_spoken_text(self):
    """Non-negotiable: every new shape must satisfy the never-speak-coords invariant."""
def test_unterminated_rect_fail_closed_stripped(self): ...

# tests/test_overlay.py
def test_rect_maps_to_local_logical(self):
    """Positions transform, lengths scale — invariant 4."""
def test_highlight_dim_covers_screen_minus_target(self): ...
def test_step_badge_renders_at_position(self): ...
```

**Every new shape needs a strip test and a coordinate-mapping test.** Those two properties are
what the existing 18 annotation tests protect.

---

### `[-] T3-1` — Gated Computer Use ("do it for me") `SKIPPED — DO NOT BUILD`

> ## 🚩 MAINTAINER DECISION (2026-08-15): SKIPPED, NOT MERELY POSTPONED
>
> **Do not implement this without an explicit, deliberate reversal of this decision.** If you
> are reading this because it looked like an obvious next feature, that is exactly the
> impulse this flag exists to interrupt.
>
> ### The three reasons, in order of weight
>
> **1. The reliability numbers do not support it.** Autonomous GUI agents land around
> **20–40% success on realistic desktop tasks**, and newer long-horizon benchmarks suggest
> existing evaluations *overstate* real-world performance. A feature that modifies the user's
> system and is wrong most of the time is not a feature.
>
> **2. The failure modes are not comparable.** Every other item in this document fails by
> being unhelpful. This one fails by *doing the wrong thing to the user's files*:
>
> | | Pointing (today) | Acting (`T3-1`) |
> |---|---|---|
> | Model is wrong | "it pointed slightly off" | "it clicked the wrong button and I lost work" |
> | Recovery | user ignores it | may be unrecoverable |
> | Blast radius | a cursor on screen | anything the user's account can do |
>
> **3. It is the only item that can damage the machine.** Nimbus currently has *no* code path
> that writes to, clicks in, or modifies any other application. That is a genuinely valuable
> property — it makes every other guarantee in this document cheap to hold — and `T3-1` is
> the single change that would destroy it. Once the capability exists, "off by default" is the
> only thing standing between a bug and the user's system.
>
> ### Why this is positioning, not a limitation
>
> Guidance beating automation at current agent reliability is not a consolation prize. It
> converts model error from *"it broke my project"* into *"it pointed slightly off"*, which is
> a dramatically better product. The teaching identity — Nimbus shows you, so **you** learn
> the software — is a real differentiator, and it is incompatible with clicking on the user's
> behalf. Building this would trade a defensible position for an undifferentiated one.
>
> Recorded in §8 as a non-goal.
>
> ### If this is ever revisited
>
> Preconditions, all of them, before a single line is written:
>
> 1. Published success rates on realistic desktop tasks are **materially above 90%**, measured
>    on something resembling Nimbus's actual workload — not a curated benchmark.
> 2. A dry-run mode exists **first**, so the feature is testable without side effects.
> 3. The `T2-1` Privacy Guard hard-block is implemented and tested **before** any execution
>    path, not alongside it.
> 4. Per-step confirmation, never chaining, always previewing, always interruptible via
>    `T2-2`.
>
> The original design outline is kept below so a future reversal starts from analysis rather
> than a blank page. **Its presence is not an endorsement.**

**Read fully before implementing. The default must not change. Highest-risk item in this
document — the only one that can modify the user's system.**

#### Why the current thesis is a strategic advantage, not a limitation
Autonomous GUI agents remain unreliable on realistic desktop tasks — the strongest published
results sit around 20–40% success, and newer long-horizon benchmarks suggest existing
evaluations *overstate* real-world performance. At that reliability, guidance beats
automation: it converts model error from *"it broke my project"* into *"it pointed slightly
off."* That is a dramatically better failure mode.

#### Design — preview-and-confirm, never autonomous
1. User explicitly asks (*"just do it for me"*).
2. Nimbus **proposes and shows** the action — annotation on the target, spoken description.
3. User confirms **per step**.
4. Nimbus executes **one** action, re-captures, re-evaluates.
5. Any ambiguity aborts back to pointing mode.

**Non-negotiables:** off by default · per-step confirmation · never chains unattended ·
always previews · always interruptible via `T2-2` · **hard-blocked whenever `T2-1` trips.**

#### Tests to add
```python
def test_disabled_by_default(self): ...
def test_no_action_without_explicit_confirmation(self, mocker): ...
def test_single_step_only_never_chains(self, mocker): ...
def test_privacy_guard_blocks_computer_use(self, mocker):
    """Hard gate: must never act on a screen we refused to capture."""
def test_cancel_aborts_before_execution(self, mocker): ...
def test_ambiguous_proposal_falls_back_to_pointing(self, mocker): ...
def test_dry_run_mode_executes_nothing(self, mocker):
    """Required for testing without touching the real system."""
```

A dry-run mode is mandatory so the feature is testable at all without side effects.

---

### `[-] T3-6` — Lesson recording and export `DEFERRED`

> **Deferred by maintainer decision (2026-08-15): a different approach to this is planned.**
>
> Not deferred on cost or risk — the goal is sound and the gap it closes versus the
> tutorial-documentation tools is real. It is held because the maintainer intends to solve
> "turn a session into a shareable artifact" differently, and building the MP4-compositing
> version first would create work to throw away.
>
> The outline below stays for reference. **Confirm the intended approach before starting**,
> rather than assuming this plan is still the one.

Optional session recording producing an MP4 with the annotation overlay composited in, plus a
timestamped Markdown transcript with screenshots.

**What it improves:** turns an ephemeral interaction into a shareable artifact — what the
established tutorial-documentation tools sell, except those require a human to *author* the
guide in advance and several require instrumenting the target application. Nimbus authors
nothing and instruments nothing. This closes the one real gap versus them while keeping the
zero-authoring advantage. Strong for teachers: demonstrate once, export, hand to a class.

*Tests:* recording start/stop lifecycle · frames written · transcript timestamps align ·
disk-space guard · recording failure never breaks the interaction.

---

### `[ ] T3-4` — Continuous screen awareness (proactive assist) `DEFERRED`

Opt-in mode using Live API video streaming (`T1-4`) with proactive audio.

**Guardrails, non-negotiable:** off by default · always-visible indicator when active ·
hard-blocked by `T2-1` · auto-expiring session (~30 min) · one-keystroke kill.

**Risk:** the feature that most threatens the privacy posture. Comparable products have found
proactive watching sits uneasily beside hotkey-only marketing. Ship late, opt-in, loudly
indicated. **Do not make it the demo centrepiece.**

---

### `[ ] T3-7` — Win32 `RegisterHotKey` listener `DEFERRED`

`hotkey.py` and `config.py` both name this as the clean fix for the observe-only
double-delivery caveat, deferred as *"8–12h of fragile ctypes code"* and *"a future drop-in
subclass."* That analysis is correct. Release detection needs `GetAsyncKeyState` polling,
which is the fragile part. **Do only if hotkey conflicts become a real support burden.**

---

### `[ ] T3-8` — Workflow capture `DEFERRED`

Record clicks/keystrokes so the user can ask *"what did I just do?"* and get it narrated back,
or turned into a reusable procedure feeding `T2-3` and `T3-6`. Same privacy gating as `T3-4`.

---

## 7. Tier 4 — Polish

**Two done, one found already complete.** `T4-5` and `T4-7` (labelling half) shipped;
`T4-1` turned out to have been satisfied before the audit was written.

### 7.1 `T4-5` — the audit was wrong in a useful direction

The item claimed `stt.on_partial_transcript()` was "wired and **consumed by nothing**". Half
right. It *was* consumed, at `app.py`:

```python
nimbus._stt.on_partial_transcript(
    lambda text: print(f"[stt partial] {text}", flush=True)
)
```

Nimbus ships as a **windowed** executable, so that console does not exist and the output went
nowhere. Better news than the audit implied: the callback path was already proven working, so
the job was routing a live signal to a widget, not building a feature.

**Why it matters more than "nice to have".** Being misheard is the most common failure in a
voice app and was previously invisible — ask a question, wait, receive a confident answer to a
question you never asked, with no way to tell which step went wrong. It also **composes with
`T2-2`**: seeing the wrong transcript while the spinner is still turning means Esc can abort
*before* the wrong answer is spoken.

#### Provider liveness is not uniform, and that is documented rather than hidden

| Provider | Behaviour |
|---|---|
| `AssemblyAIStreamingSTT` | genuine streaming partials — text appears word-by-word while speaking |
| `FasterWhisperSTT` (the default local path) | **batch** — fires once from `stop_recording()`, so the caption appears at release, beside the thinking spinner |

Both are useful; only the first is "live". Found by reading the call sites rather than
assuming, and pinned by `TestProviderLivenessIsHonest` so nobody later "fixes" a non-bug when
faster-whisper shows text at release.

#### Implementation notes

- **Front-elision**, the opposite of normal truncation: speech arrives incrementally, so the
  newest and least-verified words are at the end. Cutting the tail would hide exactly what the
  user is watching for.
- Anchored **bottom-centre**, not following the cursor: text that chases the mouse is
  unreadable, and the cursor region already hosts the waveform and spinner.
- Routed to **one** monitor, with others cleared first, so a mid-session monitor switch cannot
  leave two captions on screen. Deliberately unlike `set_audio_level`, which broadcasts.
- `CAPTIONS_ENABLED` is cached at import. Partials arrive many times per second, and
  `resolve_setting` writes to the keyring when the value came from the environment — that must
  not touch Credential Manager on the hottest path in the app.

### 7.2 `T4-7` — labelling only, deliberately

Settings that need a restart now carry a `↻` marker, with one explanatory note near Save.
24 settings are marked; API keys are deliberately **not**, because they are read per request
and a new key works immediately.

Full live reload was **not** built, and the reason is worth keeping: the import-time caching
is not an oversight. `resolve_setting` writes to the keyring whenever a value came from the
environment, so re-resolving per interaction would put a Credential Manager write on the hot
path. Removing the cache is the wrong fix; a proper reload path is `T4-7b`.

The confusion this fixes got materially worse over Tiers 1–3, which added eleven settings and
made every one restart-gated. The test that matters is
`test_settings_cached_at_app_import_are_all_marked` — it guards the failure mode that rots
quietly, a *new* cached setting added with no label.

| ID | Item | Improves | Effort | Verify first |
|---|---|---|---|---|
| `[x] T4-5` | **Live caption overlay** ✅ **DONE** | See §7.1. The audit said the callback was "consumed by nothing" — it *was* wired, to a `print()` reaching a console a windowed build does not have. Now drives an on-screen caption. | Low | Done: marshalled via `sig_caption` |
| `[x] T4-1` | ~~**Free TTS tier**~~ ✅ **ALREADY DONE — audit was stale** | `KokoroTTS` is *"Local offline TTS via Kokoro-82M (ONNX runtime, no torch, no API key)"*. The fully-keyless path (Ollama + faster-whisper + Kokoro) already shipped. Nothing to build. | — | — |
| `[x] T4-7` | **Restart labels** ✅ **DONE** | See §7.2. Minimum viable version only: label the 24 restart-gated settings rather than build live reload. | Low | Done |
| `[ ] T4-2` | **Multilingual auto-detect** + matching voice | Accessibility and reach. | Low-med | Detection library licence; per-provider voice IDs |
| `[ ] T4-3` | **Skills system** (`~/.nimbus/skills/*.py`) | User extensibility without forking; mirrors `kb.py`'s drop-in-folder pattern. | Medium | **Executing user Python is a security surface.** Document the trust model explicitly. |
| `[ ] T4-6` | **4K-aware capture resolutions** | `CANDIDATE_RESOLUTIONS` caps at 1920×1200 → 4K downscales ~2×, losing small-icon detail (the reason the refinement crop exists). | Low | **Decide with `T1-8` data** — costs tokens |
| `[ ] T4-4` | **OCR fallback** for tiny text | Handles "read the fine print". | Low | Possibly obviated by `T1-3` — **measure first** |
| `[~] T4-7b` | **Full runtime setting reload** (remainder) | The labelling half is done (§7.2). Actually reloading mid-session is still open, and still **not** a matter of removing the cache — `resolve_setting` writes to the keyring when a value came from the environment, so re-resolving per interaction would put a Credential Manager write on the hot path. | Medium | Which settings are genuinely safe to swap mid-session |
| `[ ] T4-8` | **Code-sign the installer** | Unsigned → SmartScreen warns on every download; a real adoption barrier. | Low effort | Certificate cost |

---

## 8. Explicit non-goals

Recorded so these decisions are not relitigated.

| Non-goal | Why |
|---|---|
| **Clicking on the user's behalf, at all** 🚩 | **Upgraded from "not by default" to "not at all" (2026-08-15).** `T3-1` is skipped, not postponed — see the flag on that item. Nimbus has no code path that writes to, clicks in, or modifies any other application, and that property is now a deliberate product commitment rather than an accident of scope. Guidance beats automation at 20–40% agent reliability, and the teaching identity depends on the user learning the software rather than watching it be operated. |
| **Vector DB / embeddings for memory** | `memory.py` argues against it and is right at this scale. Plain Markdown is human-readable, hand-editable, deletable — that transparency *is* the UX contract. Revisit only when keyword ranking (`T3-2`) provably fails. |
| **Always-on capture by default** | See `T3-4`. Opt-in, indicated, expiring. |
| **`suppress=True` on the keyboard hook** | Blocks every keystroke system-wide. `T3-7` is the correct fix if ever needed. |
| **Cross-platform (macOS / Linux)** | The value is deep Windows integration: Win32 click-through ex-styles, per-monitor DPI v2, `GetForegroundWindow`, Credential Manager, named-mutex single-instance. Windows is also the genuinely underserved platform here. Porting means rewriting the differentiators. |
| **Server-side key proxy** | BYOK + DPAPI keyring storage is a deliberate differentiator, and it is what makes the fully-local path possible. |
| **Replacing the multi-provider architecture** | §0. The `AIClient` seam is the most valuable asset in the codebase. |
| **A fourth coordinate space** | Three (A/B/C) is already the hard part. New geometry lands in Space C and reuses `unscale_model_coords`. |

---

## 9. Roadmap

| Phase | Items | Est. | Gate to exit |
|---|---|---|---|
| **1 — Stabilise ✅ DONE** | `T0-1` … `T0-7` | 1 day (actual) | §3.2 — all met |
| **2 — Measure** | `T1-8` | 1.5–2 days | ⊘ skipped by decision — tooling built, measurement not run |
| **3 — Model layer ✅ DONE** | `T1-1`, `T1-2`, `T1-3`, `T1-4`, `T1-5`, `T1-6a/b`, `T1-7`, `T1-9` | — | Local path verified; all existing provider tests untouched; 9/10 items built, all reachable from Settings |
| **3b — Model layer remainder** | `T1-6c` Files API only | — | Blocked on nothing but a consumer; do it with `T3-2` |
| **4 — Product wins ✅ DONE** | `T2-8`, `T2-5`, `T2-2`, `T2-1`, `T2-4`, `T2-7` | — | Verified on real hardware; `T2-6`/`T2-3` deferred by decision (§5.9) |
| **5 — Depth ✅ MOSTLY DONE** | `T3-3`, `T3-5` done · `T4-5`, `T4-7` done | — | Education story demonstrable end to end |
| **6 — Ambitious ✅ DONE** | `T3-2` | — | Verified live: in-house DOCX table answered a question the model otherwise hallucinated |
| **Deferred / skipped** | `T3-6`, `T3-4`, `T3-7`, `T3-8`, `T2-3`, `T2-6`, most of Tier 4 · **`T3-1` skipped outright** 🚩 | — | Each carries its reason at the item |

> Corrected: this row previously listed `T1-9` as ambitious/outstanding. `T1-9` was
> **completed in Tier 1** — it was promoted from optional to required by a measured API
> property (Gemini returns prose *or* a tool call, never both). Stale entry, now fixed.

**Parallelisation:** Tier 2 items are provider-independent and can run alongside Phase 3.
`T2-5`, `T2-2`, and `T2-7` need no API key at all.

**If time is short**, the highest-value subset is:
`Tier 0` + `T1-8` + `T1-1` + `T1-2` + `T2-1` + `T2-2` + `T2-5` + `T3-3`.
That yields a Nimbus with no known defects, native structured grounding proven by
measurement, the category's loudest complaints fixed, and a real Education story.

---

## 10. Appendix — Test infrastructure

### 10.1 Baseline

**1252 tests** (477 baseline → 558 after Tier 0 → 757 after Tier 1 → 988 after Tier 2
→ 1252 after Tier 3/4).
Tier 1 added three files (`test_gemini_native.py`, `test_gemini_cache.py`,
`test_experimental.py`); Tier 2 added five more — `test_privacy.py` (+58),
`test_hotkey_capture.py` (+45), `test_history_images.py` (+31), `test_prompts.py` (+26),
`test_cancel.py` (+25) — plus 45 new `test_capture.py` tests for `T2-8`. The Tier 3/4 partial
added four: `test_review.py` (+73), `test_shapes.py` (+54), `test_captions.py` (+35),
`test_restart_labels.py` (+23), `test_kb_expansion.py` (+78). Roughly one file per module. **No
`pyproject.toml`**, and the `pytest.ini` added later carries collection scope only (§1.4) — otherwise
pytest runs on defaults; `tests/__init__.py` makes `tests` a package so the
repo root lands on `sys.path`, which is why `from ai import ...` works inside test methods.
`tests/conftest.py` holds one fixture only (§4.3).

Tier 0 additions by file: `test_ai.py` +48 · `test_app.py` +17 · `test_config_keyring.py` +8 ·
`test_memory.py` +5 · `test_settings_dialog.py` +1 (one replaced by two).

Verified counts as of Tier 3/4 partial (collected, not estimated):

| File | Tests | File | Tests |
|---|---|---|---|
| `test_ai.py` | 133 | `test_history_images.py` | 31 |
| `test_app.py` | 102 | `test_cancel.py` · `test_prompts.py` | 26 · 26 |
| `test_gemini_native.py` | 81 | `test_locator.py` | 25 |
| `test_review.py` | 73 | `test_config_keyring.py` | 24 |
| `test_capture.py` | 72 | `test_restart_labels.py` | 23 |
| `test_gemini_cache.py` | 67 | `test_hotkey.py` · `test_stt.py` | 21 · 21 |
| `test_privacy.py` | 58 | `test_memory.py` | 20 |
| `test_shapes.py` | 54 | `test_annotations.py` | 18 |
| `test_experimental.py` | 50 | `test_ollama_health.py` | 16 |
| `test_hotkey_capture.py` | 45 | `test_tray.py` · `test_kb.py` · `test_realtime.py` | 11 · 10 · 10 |
| `test_settings_dialog.py` | 41 | `test_bench.py` · `test_debug_log.py` · `test_updates.py` · `test_onboarding.py` | 5 · 3 · 3 · 1 |
| `test_overlay.py` | 37 | | |
| `test_captions.py` | 35 | | |
| `test_tts.py` | 32 | | |

### 10.2 Test conflicts encountered in Tier 0 — ✅ all resolved

The audit predicted two. There were **five** tests asserting pre-fix behaviour. Every one was
*updated* with a comment explaining the reversal, never deleted.

| Test | File | Asserted | Item |
|---|---|---|---|
| `test_malformed_tag_returns_no_coordinate` | `test_ai.py` | malformed tag **stays in `spoken_text`** | `T0-3` |
| `test_defaults_to_anthropic_path` | `test_app.py` | `"anthropic/model-sonnet-4-6"` | `T0-1` |
| `test_unknown_provider_falls_back_to_anthropic` | `test_app.py` | same placeholder id | `T0-1` |
| `test_routes_anthropic_prefix_to_anthropic_client` | `test_ai.py` | `model_id` stored **verbatim** | `T0-1` |
| `test_anthropic_provider_has_no_model_picker` | `test_settings_dialog.py` | Anthropic must have **no** model picker | `T0-1` |
| `test_routes_google_prefix_to_native_gemini_with_direct_key` | `test_ai.py` | direct Google key → **OpenAI-compat shim** | `T1-1` |

Tier 1 added a sixth. Its name already said "native", but it meant *Google's
OpenAI-compatibility endpoint*, not the native SDK — a useful reminder that a test name can
encode a stale mental model as easily as a stale value. Replaced by two tests covering both
key formats.

**Lesson for later tiers:** a test asserting current behaviour is not automatically a
specification. Two of these encoded the bug itself, and one
(`test_anthropic_provider_has_no_model_picker`) encoded a design decision — "minimal UX" —
that was reasonable when written and became the direct cause of an unreachable setting.
Distinguish the three before changing any of them.

**Encoding note:** several test docstrings contain em-dashes stored as mojibake, which
defeats byte-exact string replacement. Anchor edits on ASCII-only lines, or edit
programmatically with explicit UTF-8.

### 10.3 Behaviours to preserve (currently asserted, easy to break)

- `test_screen_number_without_label` — `[POINT:400,300:screen2]` yields `screen_number=2`,
  **not** `label="screen2"`. The `(?!screen\d)` negative lookahead exists for this.
- `test_coordinates_with_spaces` — `[POINT:640 , 400:btn]` must parse.
- `_CLICKTHROUGH_FLAGS == 0x080800A8` — exact bit pattern asserted in `test_overlay.py`.
- `TestOllamaClientReviewerFixes` — httpx client leak on connect failure; `deltas_exhausted`
  set in a `finally` so `final_result()` cannot re-raise a mid-stream error.
- `TestResolveLLMCredentials::test_routes_to_ollama_when_provider_is_ollama` — the Settings
  dropdown must not be cosmetic. Regression test for a real past bug.
- `_history_message_text` excludes image payloads from Documents exports — a deliberate
  privacy property (`T2-4` could silently break it).

### 10.4 Frozen-build checklist for any new dependency

1. Add to `requirements.txt` with a **pinned** version.
2. Add to `nimbus.spec` `hiddenimports`.
3. If it ships native libs or data files, add `collect_all()` — see the existing handling of
   `av` (its `.pyd` is misclassified as data; `av.libs` DLLs need explicit inclusion),
   `espeakng_loader`, `phonemizer`, `segments`, `csvw`, `jsonschema`.
4. Add the module to `_run_selftest()`'s `runtime_modules` tuple if it is imported at runtime.
5. Build and verify — **this step is not optional**; several deps above only revealed problems
   in a frozen EXE:
   ```powershell
   .\.venv\Scripts\python.exe -m PyInstaller nimbus.spec --noconfirm
   .\dist\Nimbus\Nimbus.exe --selftest      # must print SELFTEST OK
   ```
6. Check bundle size did not balloon; extend `excludes` if a heavy transitive dep appeared.
   The first build was 1.1 GB before exclusions.

### 10.5 Test-writing checklist per item

- [ ] Test fails before the fix, passes after (prove it tests something)
- [ ] Happy path
- [ ] Every documented edge case
- [ ] Failure mode: does a dependency error crash the pipeline, or degrade?
- [ ] Backward compatibility: does the old behaviour still work when the new setting is off?
- [ ] Drift guard for any new constant, magic number, or duplicated default
- [ ] Thread safety, if the change crosses a thread boundary
- [ ] Docstring names the bug or behaviour being guarded

---

## 11. Measured results

The systematic table below was to be populated by `T1-8`, which was **skipped by decision**. It
stays empty, and the standing caveat therefore stands: **no comparative hit-rate or px-error
claim in this document is verified**, including the ones citing external benchmarks.

| Provider | Model | Strategy | Hit-rate | Median px error | p95 latency | Cost/call |
|---|---|---|---|---|---|---|
| _not measured — `T1-8` skipped_ | | | | | | |

### 11.1 One-off measurements actually taken

These are real numbers from live runs, recorded so they are not mistaken for estimates. They
are **single samples, not benchmarks** — no repetition, no confidence interval, shared network.

| What | Measured | Item | Caveat |
|---|---|---|---|
| TTFB, thinking budget 512 → 0 | 3.97 s → 1.18 s | `T1-7` | No screenshot attached. The largest latency lever found anywhere in Nimbus. |
| TTFB with a screenshot attached | ~3–5 s regardless of budget | `T1-7` | Suggests image handling, not reasoning, dominates once an image is in play — which blunts the budget win in the real pipeline. |
| KB cache, 60,000-char KB | 10,002 tokens; 10,008 of 10,013 prompt tokens served from cache on turn 2 | `T1-6a` | Confirms the cache is real, not that it saves money at Nimbus's actual KB sizes. |
| Agentic Vision TTFB, off → on | 4.16 s → 2.55 s | `T1-3` | **Do not read as "faster".** One sample each, two concurrent requests racing a shared network, and the agentic run *raises* its budget to 2048. Reads as "not catastrophically slower". |
| Prose vs. function call in one turn | Never both, at budgets 0 / 64 / 128 / 256 / 512 | `T1-9` | The measurement that forced the split-call architecture. A single tool-enabled call produced a pointer with total silence. |
| Search grounding accuracy | Wrong answer (3.12.5 vs 3.14.6), citations absent | `T1-5` | Under Nimbus's own persona prompt with a screenshot. Why it ships off and the tooltip says NOT RECOMMENDED. |
| Flash vs 3.1 Pro pointing | Both 6/6, median 1 px | — | Pro is **not** more accurate, and is slower and dearer. Flash stays the default. See §11.3. |
| Crop refinement, before fix | pixel-perfect seed pushed 51 px off target, 3/3 | §11.3 | The real cause of the reported accuracy regression. |
| Crop refinement, after fix | 66 px seed → 1 px; perfect seed preserved | §11.3 | Refinement now behaves as designed. |

### 11.3 The accuracy regression: Gemini emits normalised coords in `[POINT]` tags too

**Reported symptom:** pointing became less accurate after Tier 1. **Suspected cause:** the
model. **Actual cause:** neither — a unit mismatch in the refinement pass, on the native
Gemini path only.

First, the model was ruled out with ground truth. Six targets on a synthetic 1920×1080 UI,
including two 32×32 icons:

| Model | Hit-rate | Median error | Max error |
|---|---|---|---|
| `gemini-3-flash-preview` | 6/6 | 1 px | 4 px |
| `gemini-3.1-pro-preview` | 6/6 | 1 px | 14 px |

So raw grounding is near-exact on both, and **Pro is not more accurate than Flash** — it is
slower and dearer for no measured gain. The regression was downstream.

`locator.refine_point_via_crop` was then tested directly, and reproduced 3/3:

| Seed given to refinement | True centre | Refined to | Error before → after |
|---|---|---|---|
| (1016, 70) — pixel-perfect | (1016, 70) | (1066, 78) | 0 px → **51 px, outside the icon** |
| (1075, 100) — 66 px off | (1016, 70) | (1060, 78) | 66 px → 45 px, still outside |

The pass that exists to *improve* accuracy was destroying it. `gemini_native.py` carried a
comment asserting that text-path coordinates were "already in Space C… so they need no
normalised conversion". That assumption was false. Raw stream capture on the 900×900 crop,
where the true centre is pixel (450, 70) and normalised (500, 78):

```
[POINT:500,78:the gear icon]      [POINT:500,77:gear]      [POINT:500,78:gear]
```

Exactly the normalised values. Confirmed as a general convention, not a coincidence — a
dead-centre target returned `[POINT:500,500]` at **every** size tested:

| Image | If pixels | If normalised | Actual |
|---|---|---|---|
| 900×900 | (450, 450) | (500, 500) | `500,500` |
| 1920×1080 | (960, 540) | (500, 500) | `500,500` |
| 600×400 | (300, 200) | (500, 500) | `500,500` |
| 400×1200 | (200, 600) | (500, 500) | `500,500` |

Gemini returns normalised 0–1000 **even when the prompt states the pixel dimensions and asks
for pixels** — `_REFINEMENT_SYSTEM_PROMPT` does exactly that and is ignored. The convention is
trained in. Consuming those values as pixels inflated every refined point by
`dimension / 1000`, biasing it down and right, worse the further from the crop's top-left.

Only this provider was affected; the other clients' models do return pixels there, which is
why the crop pass worked before the native path existed.

**Fix:** the native client's text-tag fallback converts through
`normalised_point_to_space_c`, the same helper the structured tool path already used. After:

| Seed | Refined to | Error before → after |
|---|---|---|
| (1016, 70) — pixel-perfect | (1016, 70) | 0 px → **0 px** (preserved) |
| (1075, 100) — 66 px off | (1017, 70) | 66 px → **1 px**, inside the icon |

Guarded by `TestTextTagCoordinatesAreNormalised`. Verified meaningful: reverting the fix fails
4 of its 6 tests.

**Lesson worth generalising.** A provider's coordinate convention is a property of the *model*,
not of the prompt, and cannot be assumed from either the prompt or the other providers'
behaviour. §2.2's coordinate-space contract should be read as also covering *units* at every
provider boundary, not just spaces. The bug was invisible in review — the wrong assumption was
written down as a confident comment — and only measurement caught it.

### 11.2 Grounding correctness spot-check (`T1-3`)

Not a hit-rate measurement — a **correctness** check with ground truth, run because a live
desktop result looked wrong and eyeballing could not distinguish "model missed" from "code
transposes y and x".

Synthetic 1920×1080 UI, four buttons at **asymmetric** positions so a transposition cannot pass
by coincidence. Each queried in a separate turn, `gemini-3-flash-preview`:

| Target | Ground-truth rect | Returned | Verdict |
|---|---|---|---|
| Save (top-left) | x 60–260, y 40–100 | (159, 70) | inside |
| Trash (top-right) | x 1660–1860, y 40–100 | (1759, 70) | inside |
| Publish (bottom-left) | x 60–260, y 980–1040 | (159, 1010) | inside |
| Settings (bottom-right) | x 1660–1860, y 980–1040 | (1759, 1009) | inside |

All four landed dead-centre. **Conclusion:** the y-first normalised wire format is handled
correctly end to end, and the earlier suspicious desktop result was the model mislocating on a
cluttered screen — not a code defect. This test is cheap and worth re-running as a regression
gate whenever the coordinate path changes; it is the check that would catch a silent systematic
y/x swap, which is the single highest-consequence failure mode in §2.2.

---

*Cross-references point to file and symbol names rather than line numbers, since line numbers
drift. External sources are linked inline. Content from external sources was rephrased for
compliance with licensing restrictions.*
