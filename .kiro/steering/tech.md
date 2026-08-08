# Stack, commands, and the two ways to get the environment wrong

## Stack

| Layer | Choice |
|---|---|
| Desktop | Python 3.13, PyQt6 (>=6.7), Windows 10 build 19041+ |
| Screen capture | `mss` >= 9.0.1, Per-Monitor-V2 DPI awareness (owned by Qt when a QApplication exists) |
| Hotkey | `pynput` >= 1.7.7, **observe-only** |
| Default model | Gemini via `google-genai` >= 2.16.0 (`config.DEFAULT_LLM_PROVIDER = "gemini-native"`) |
| Other models | `openai` >= 1.60, `anthropic` >= 0.40, raw `httpx` to a local Ollama |
| Speech in | AssemblyAI `u3-rt-pro` streaming, or local `faster-whisper` |
| Speech out | Cartesia `sonic-3`, ElevenLabs `eleven_flash_v2_5`, or local Kokoro-82M via ONNX |
| Secrets | `keyring` >= 25 (Windows Credential Manager, DPAPI per-user) |
| Licence crypto | `cryptography` Ed25519 — already present, adds nothing to the bundle |
| Build | PyInstaller `>=6.11,<7` one-dir, then Inno Setup 6 |
| Backend | Next.js 15 + TypeScript + Prisma + Postgres (Neon), Vercel `iad1` |
| Backend crypto | Node's own `crypto`: scrypt for passwords, Ed25519 for licences, no library |

## Commands

**Always `.\.venv\Scripts\python.exe`.** The system Python does not have these dependencies, and a
tool that half-works is worse than one that fails.

```powershell
# The full desktop suite. The dotenv neutralisation is not optional -- see below.
.\.venv\Scripts\python.exe -c "import dotenv,pytest,sys; dotenv.load_dotenv=lambda *a,**k:False; sys.exit(pytest.main(['-q']))"

# One file while iterating
.\.venv\Scripts\python.exe -m pytest tests/test_ai.py -v -k "point_tag"

# Frozen-import check. Catches a missing module long before a release build.
.\.venv\Scripts\python.exe -m app --selftest        # must print: SELFTEST OK

# Confirm the count went UP, never down
.\.venv\Scripts\python.exe -m pytest -q --collect-only | Select-String "collected"

# Build. Kill any running Nimbus first, or PyInstaller cannot replace the exe. ~9 minutes.
.\.venv\Scripts\python.exe -X utf8 -u -m tools.build_release --clean --installer

# Is the code I just wrote actually inside the binary?
.\.venv\Scripts\python.exe -m tools.verify_bundle --marker "some new string"

# The two other suites
cd web;     npm install; npm test        # 14 tests: licence signing and key generation
cd service; ..\.venv\Scripts\python.exe -m pytest -q   # 27 tests, superseded backend
cd web;     npx tsc --noEmit             # THE typecheck gate -- Vercel does not do this
```

## The two ways to get the environment wrong

**1. Forgetting to neutralise dotenv.** `config.py` calls `load_dotenv()` at import, so a local
`.env` leaks provider settings into the test run and turns a green suite into a green suite that
proves nothing about a clean machine. CI sets `NIMBUS_DISABLE_DOTENV=1`; locally, use the
one-liner above. This is why the canonical command looks strange.

**2. Assuming `next build` typechecks.** `web/next.config.ts` sets `typescript.ignoreBuildErrors`
and `eslint.ignoreDuringBuilds`. That is a release valve so a type error cannot block a deploy at
2 a.m., **not** permission to skip the check. `npx tsc --noEmit` is the gate and it is enforced by a
person, so run it before pushing anything in `web/`.

## Windows and shell notes

- Line endings are **CRLF** repo-wide. Verify after any programmatic edit.
- **Do not use PowerShell for file edits.** `Get-Content -Raw` plus `WriteAllText` has already
  destroyed a document here: 452 mojibake sequences and every em dash. Use Python with explicit
  `encoding="utf-8"`, or an editing tool.
- Use `python -X utf8` for scripts that print anything but ASCII; Windows consoles default to cp1252.
- Throwaway probe scripts are named `_something.py`, are excluded from the staleness check in
  `tools/build_release.py`, and **are deleted after use**.
- Long-running commands (dev servers, watchers) must not be run inline; they block.

## Settings resolution

`config.resolve_setting(name, default)` and `config.resolve_api_key(name)` both go
**env -> keyring -> default**, and both **write through to the keyring** when the value came from the
environment. That write-through is a one-shot `.env` migration and it is also why a handful of
settings are resolved once at import rather than per call: re-resolving on the hot path would put a
Credential Manager write inside every interaction.

Consequence: a setting cached at import needs a restart to take effect, and must therefore appear in
`settings_dialog.RESTART_REQUIRED_SETTINGS` so its label carries the `⟳` marker. API keys are
deliberately excluded — they are read per request, so a new key works immediately.
