# Where everything lives, and the seams between the parts

## Repository layout

```
Nimbus/
  app.py                   orchestrator: 26 pyqtSignals, press/release/pipeline, licence gate, selftest
  ai.py                    AIClient ABC + 4 providers, prompts, [POINT] parsing, create_ai_client
  gemini_native.py         the default path: structured geometry tools, thinking budgets, split-role calls
  gemini_cache.py          explicit KB context caching for the native path
  gemini_live.py           speech-to-speech (off by default), parallel pipeline not an AIClient
  realtime.py              OpenAI Realtime speech-to-speech (off by default), same shape
  locator.py               two-stage grid fallback + native-resolution refinement crop
  prompts.py               per-app system-prompt addenda, keyed on sanitised exe basename
  capture.py               mss grab, DPI awareness, resolution pick, Space C -> Space A unscaling
  overlay.py               per-monitor click-through DPI-aware pointer + teaching annotations
  annotations.py           shape-tag grammar, seven frozen dataclasses, fail-closed stripping
  theme.py                 the design system: palette, motion, contrast maths, generated QSS
  brand.py                 one loader for the mark; alpha-trimmed and cached per asset
  privacy.py               Privacy Guard: a pure function, no I/O, no clock
  kb.py                    per-app knowledge base: folders, PDF/DOCX, relevance ranking
  memory.py                per-app Markdown log + SQLite `apps` index
  review.py                Knowledge Journal: spaced repetition, pure scheduling functions
  sessions.py              chat sessions, messages, screenshots, privacy_skips, history rebuild
  stt.py / tts.py          speech in and out, cloud and local, factory-injectable
  hotkey.py                global chord listener, observe-only (suppress=False)
  licensing.py             Ed25519 verification, trial, offline grace, seats — no Qt
  activation_dialog.py     the licence gate's UI — the first thing a new user sees
  settings_dialog.py       SettingsForm (hostable) + SettingsDialog (modal host)
  shell/                   the windowed app: window, nav, titlebar, widgets, pages/
  chat_hud.py              the floating transcript panel, excluded from screen capture
  tray.py                  the only always-available surface; the only clean exit path
  config.py                settings resolution env -> keyring -> default; every tunable
  tools/                   build, verify, benchmark, icons, fixtures, licence keys
  tests/                   45 files, 2,030 collected tests
  web/                     THE BACKEND: Next.js 15, Postgres, Stripe, EasyPaisa, licence API
  service/                 the earlier FastAPI licence service, superseded by web/
  installer/nimbus.iss     Inno Setup, per-user, no UAC
  nimbus.spec              PyInstaller one-dir spec
```

## The seams that matter

**`app.py` is the only module that knows about threads.** Everything else is either a pure function,
a class with injected dependencies, or a Qt widget that is only ever touched on the main thread.

**Providers sit behind ABCs.** `AIClient`, `STT`, `TTS`, each with a `create_*_client()` factory that
routes on a string. No `if provider == "gemini"` may ever appear in `_pipeline_worker`.

**`shell/` never imports `app`.** Every data source is an injected callable, every action is a
signal. That is what makes the window constructible with no `NimbusApp` and testable with no
pipeline. The same rule holds for `chat_hud.py`.

**`licensing.py` has no Qt.** The module that decides whether Nimbus may run is testable without a
widget; `activation_dialog.py` is its UI and takes the module by injection.

**Two speech-to-speech paths are deliberately not `AIClient`s.** `gemini_live.py` and `realtime.py`
collapse STT + model + TTS into one socket, so there is no intermediate text to sentence-chunk. They
expose `connect / start_turn / respond / stop / close` so `app.py` needs no new branching.

## The three coordinate spaces

The intellectual core of the codebase. Any change touching coordinates must preserve these
boundaries.

| Space | Units | Origin | Owner |
|---|---|---|---|
| **A** | physical pixels, virtual desktop | multi-monitor union top-left | `capture.py` |
| **B** | Qt logical / DIP pixels, **per-screen** | that screen's own top-left | `overlay.py` |
| **C** | the model's declared resolution | screenshot top-left | `ai.py` |

- `capture.unscale_model_coords` owns **C -> A**: clamp, then `x * scale`, then `+ monitor origin`.
- `overlay.physical_to_local_logical` owns **A -> B**: `- screen origin`, then `/ devicePixelRatio`.
- `overlay.annotations_to_local` applies the same rule to shapes: **positions transform, lengths
  only scale.**
- `ai.py` returns Space C **unclamped** by contract. Clamping happens in exactly one place.

Never cache a global device-pixel ratio. Always `screen.devicePixelRatio()`, per screen.

## Data on disk

| Path | Contents | Constant |
|---|---|---|
| Credential Manager, service `nimbus` / `Nimbus` | every API key, every setting, licence tokens | `KEYRING_SERVICE` |
| `~/.nimbus/memory/<app>.exe.md` | per-app interaction log, human-readable | `MEMORY_DIR` |
| `~/.nimbus/index.db` | SQLite WAL: `apps`, `review_queue`, `chat_sessions`, `chat_messages`, `privacy_skips` | `INDEX_DB_PATH` |
| `~/.nimbus/chats/<session>/<message>.jpg` | chat screenshots, **off by default** | `sessions.CHATS_DIR` |
| `~/.nimbus/insights.md` | Journal progress summary, written on request only | `INSIGHTS_PATH` |
| `~/.nimbus/debug/<ts>_<app>/` | opt-in diagnostics, 7-day retention | derived from `MEMORY_DIR` |
| `~/.nimbus/kokoro/` | local TTS models, ~336 MB on first use | `KOKORO_CACHE_DIR` |
| `~/.nimbus/chat_hud.json` | per-monitor panel position | derived from `INDEX_DB_PATH` |
| `~/.nimbus/licence-gate-error.log` | why the gate was skipped, for a windowed build with no stdout | — |
| `~/Documents/Nimbus Wiki/` | the user's knowledge base, plus a seeded `README.md` | `KB_DIR` |

Four writers share `index.db`. That is safe under WAL **only because every write happens on the Qt
main thread** — which is why `ChatHud.append` persists a message rather than `_pipeline_worker`.

## Worker threads, by name

`nimbus-press-capture`, `nimbus-release-capture`, `nimbus-pipeline`, `nimbus-retry`,
`nimbus-licence-check`, `nimbus-show-window`, `nimbus-gemini-geometry`, `tts-warmup`,
`stt-teardown`, `gemini-live-loop`, and a prefetch/playback pair per TTS provider.
