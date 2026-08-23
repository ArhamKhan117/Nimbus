"""Nimbus configuration.

Loads environment variables from .env (BYOK pattern) AND from
the OS keyring (Windows Credential Manager via the keyring
package, DPAPI per-user encryption). On launch with .env present, the
keys are auto-migrated to the keyring as a backup; user can then delete
.env without losing the keys.
"""

from __future__ import annotations

import os
from pathlib import Path

import keyring
from dotenv import load_dotenv

# CI must exercise a clean environment even if a runner or future test setup
# happens to place a .env file beside the checkout. Production keeps the
# convenient local .env workflow; GitHub Actions sets this guard explicitly.
if os.getenv("NIMBUS_DISABLE_DOTENV") != "1":
    load_dotenv()


# ── Secrets resolution (env → keyring with one-shot migration) ──────────────

KEYRING_SERVICE: str = "nimbus"
"""Service name for keyring entries. Windows Credential Manager treats this
as the namespace key. All Nimbus API keys live under this single service
name; the ``name`` parameter is the env-var name (ANTHROPIC_API_KEY, etc.)."""


# ── Persisting a setting when the credential vault lies ─────────────────────
#
# `keyring.set_password` can return normally and store nothing. That is not a hypothetical: it was
# measured on a machine where the vault had accumulated 75 entries, and it is the reason a user
# reported that Settings did not survive a restart. The sequence was
#
#     keyring.set_password("nimbus", "ANNOTATION_MODE", "on")   -> returns, no exception
#     keyring.get_password("nimbus", "ANNOTATION_MODE")         -> "off"
#
# and every toggle in Settings behaved that way. Windows itself was fine: `cmdkey` wrote and read a
# generic credential, and no policy blocked storage. `keyring`'s Windows backend writes the newest
# value to the bare service target with `CRED_PERSIST_ENTERPRISE`, and an enterprise-persisted
# credential is roamed, so it is subject to a total size budget. Past that budget `CredWrite`
# reports success and drops the write. Writing the same target with `CRED_PERSIST_LOCAL_MACHINE`
# succeeded in the same process, which is what pinned the cause.
#
# The lesson generalises past this one machine: a write nobody reads back is not a write. So
# `store_setting` verifies, and falls back to a file when the vault cannot be trusted.

SETTINGS_FALLBACK_NAME = "settings.dat"
"""Where a setting goes when the vault silently refuses it. Beside the licence blob, in DATA_DIR."""


def _fallback_path():
    """Where the fallback lives, overridable by ``NIMBUS_SETTINGS_FALLBACK``.

    The override is read from the environment rather than patched onto this module, because the test
    suite reloads ``config`` and a reload restores every module attribute -- so a patched function
    silently reverts mid-test and writes land on the real file. That happened: one run left twenty-five
    fake entries in the developer's own settings, including provider keys.
    """
    from pathlib import Path

    override = os.getenv("NIMBUS_SETTINGS_FALLBACK")
    if override:
        return Path(override)
    # ``~/.nimbus`` spelled out rather than derived from one of the directory constants below. Those
    # are defined much later in this module and are individually redirectable by their own environment
    # variables, so borrowing one would tie where a setting is stored to where memory or a model cache
    # happens to point. It is the same root ``licensing`` uses for the licence blob.
    return Path(os.path.expanduser("~")) / ".nimbus" / SETTINGS_FALLBACK_NAME


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    """DPAPI round trip, or ``None`` when it is unavailable.

    The fallback file holds API keys, and the vault it replaces encrypts at rest, so writing them in
    clear would quietly downgrade the user's security to fix a persistence bug. DPAPI binds the
    ciphertext to this Windows account, which is the same protection the vault offers and needs no
    key of our own to look after.

    Reached through ``ctypes`` because the only dependency that would otherwise provide it is
    ``pywin32``, and ``keyring`` pulls ``pywin32-ctypes`` instead. Adding a dependency to encrypt a
    fallback for a dependency that failed is not a trade worth making.
    """
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return None

    source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                         ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # 0x01 is CRYPTPROTECT_UI_FORBIDDEN: never prompt. A dialog from inside a settings save would be
    # inexplicable to the user and would block the Qt main thread.
    arguments = ([ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(result)]
                 if protect else
                 [ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(result)])
    try:
        if not function(*arguments):
            return None
        out = ctypes.string_at(result.pbData, result.cbData)
    except Exception:
        return None
    finally:
        try:
            if result.pbData:
                kernel32.LocalFree(result.pbData)
        except Exception:
            pass
    return out


def _read_fallback() -> dict:
    """Every setting the vault could not hold. ``{}`` when there is nothing or it is unreadable."""
    import json

    path = _fallback_path()
    try:
        raw = path.read_bytes()
    except Exception:
        return {}
    if raw.startswith(b"{"):
        # Written on a machine where DPAPI was unavailable. Still readable, deliberately.
        text = raw.decode("utf-8", "replace")
    else:
        plain = _dpapi(raw, protect=False)
        if plain is None:
            return {}
        text = plain.decode("utf-8", "replace")
    try:
        loaded = json.loads(text)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_fallback(values: dict) -> bool:
    import json

    path = _fallback_path()
    body = json.dumps(values, indent=2, sort_keys=True).encode("utf-8")
    sealed = _dpapi(body, protect=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sealed if sealed is not None else body)
        return True
    except Exception:
        return False


def store_setting(name: str, value: str) -> bool:
    """Persist a setting and **prove** it persisted. ``False`` only when nothing worked.

    Vault first, because it encrypts at rest and is where every existing value already lives. Then
    read it back, because that is the only way to catch a backend that reports success and stores
    nothing. On a mismatch the value goes to the DPAPI-sealed fallback file and the vault entry is
    left alone rather than deleted, so a vault that starts working again is not stale.
    """
    try:
        keyring.set_password(KEYRING_SERVICE, name, value)
        if keyring.get_password(KEYRING_SERVICE, name) == value:
            # It took. Drop any fallback copy so the two cannot disagree later.
            stored = _read_fallback()
            if name in stored:
                del stored[name]
                _write_fallback(stored)
            return True
    except Exception:
        pass

    stored = _read_fallback()
    stored[name] = value
    return _write_fallback(stored)


def resolve_api_key(name: str) -> str | None:
    """Resolve an API key by name, preferring env var then keyring.

    On env-var-present, ALSO write the value to keyring as a backup —
    this is the one-shot migration path from the ``.env`` workflow
    to keyring storage. Subsequent launches with no .env will
    pick up the value from keyring transparently.

    Failures in keyring (locked vault, no backend, transient errors)
    are swallowed — the env-var path always works as a fallback. We
    never want a credential-store glitch to block app startup when the
    user has perfectly valid keys in their .env.

    Returns None if neither source has a value (caller shows the
    first-launch settings dialog).
    """
    env_value = os.getenv(name)
    if env_value:
        # Verified, and with the same fallback as every other setting. A key that appears to save and
        # then is gone next launch sends the user back to the provider's dashboard for a value they
        # already typed correctly.
        store_setting(name, env_value)
        return env_value
    # Same order as `resolve_setting`, and for the same reason: a key is only in the file because the
    # vault would not take the newer value, so the vault's copy is the outdated one.
    from_file = _read_fallback().get(name)
    if from_file:
        return from_file
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def resolve_setting(name: str, default: str) -> str:
    """Resolve a non-secret setting by name with env→keyring→default fallback.

    Sibling to ``resolve_api_key`` for config knobs (TTS_PROVIDER,
    LLM_PROVIDER, STT_PROVIDER, etc.) that need keyring persistence so
    bundled-EXE startup doesn't silently fall back to defaults when the
    user's `.env` doesn't load (cwd is install dir, not repo root).

    Differs from resolve_api_key in that it always returns a string —
    callers pass the right default for the setting (e.g. "cartesia" for
    TTS_PROVIDER) rather than handling None.

    Failures in keyring (locked vault, no backend) are swallowed in both
    directions: env path always returns successfully even if keyring write
    fails; keyring read errors fall through to the default.
    """
    env_value = os.getenv(name)
    if env_value:
        store_setting(name, env_value)
        return env_value
    # The fallback file comes **before** the vault, and the order is the whole fix.
    #
    # A name is only ever in that file because the vault refused to update it, which means the vault
    # still holds the previous value. Reading the vault first therefore returns the stale one and the
    # save appears to have done nothing -- which is exactly the bug being fixed, just moved. And it
    # cannot go stale in the other direction: `store_setting` removes the name from the file the
    # moment a vault write verifies, so a machine whose vault starts working returns to it by itself.
    from_file = _read_fallback().get(name)
    if from_file:
        return from_file
    try:
        stored = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        stored = None
    return stored if stored else default


def persist_setting(name: str, value: str) -> bool:
    """Write a non-secret setting to the keyring. ``False`` when the vault is unreachable.

    The write half of ``resolve_setting``, for the handful of settings that are changed from
    somewhere other than the Settings form -- the Home page's chat-panel switch, today. Without
    this those callers would each reach for ``keyring.set_password`` and the service name, and
    the one that got it wrong would silently write a setting nothing ever reads.

    **This does not clear an environment override.** ``resolve_setting`` checks ``os.getenv``
    first, so a value in ``.env`` still wins on the next read. That is the documented precedence
    and changing it here would make one setting behave unlike all the others; callers that need
    the new value to take effect immediately should apply it themselves rather than re-reading,
    which is what ``NimbusApp.set_chat_visible`` does.
    """
    try:
        keyring.set_password(KEYRING_SERVICE, name, str(value))
        return True
    except Exception:
        return False


def resolve_bounded_int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    """Resolve an integer setting without letting a corrupt keyring value crash startup."""
    try:
        value = int(resolve_setting(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# First-launch UX state is intentionally stored alongside settings rather than
# in a file: Credential Manager survives a portable/frozen install move and is
# already the app's source of truth for user preferences.
ONBOARDING_SEEN_KEY = "SEEN_ONBOARDING"
WELCOME_SEEN_KEY = "SEEN_WELCOME"


def onboarding_seen() -> bool:
    """Whether the one-time tray onboarding balloon was already displayed."""
    # Unlike ordinary settings, an environment value must not be able to make
    # a shown onboarding balloon reappear on every launch. This is strictly a
    # local, one-way keyring flag.
    try:
        stored = keyring.get_password(KEYRING_SERVICE, ONBOARDING_SEEN_KEY)
    except Exception:
        stored = None
    return (stored or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def mark_onboarding_seen() -> bool:
    """Persist successful onboarding display; return False if keyring is unavailable."""
    try:
        keyring.set_password(KEYRING_SERVICE, ONBOARDING_SEEN_KEY, "1")
    except Exception:
        return False
    return True


SHELL_STARTUP_REVISION_KEY = "SHELL_ON_STARTUP_DEFAULT_REVISION"
SHELL_STARTUP_REVISION = "2"
"""Bump this if the ``SHELL_ON_STARTUP`` default ever moves again."""


def migrate_shell_startup_default() -> bool:
    """Retire a stored ``SHELL_ON_STARTUP=off`` once, so the new default can be seen.

    ## Why a default change was not enough

    ``resolve_setting`` is env -> keyring -> default, so a stored value beats the default. The
    Settings dialog writes *every* checkbox on Save, and the old checkbox defaulted to unchecked --
    so anybody who ever opened Settings and pressed Save has ``"off"`` in Credential Manager,
    recording a choice they never made. Flipping the default reached new installs only, which is
    the population that had the least trouble.

    ## The cost, stated plainly

    A deliberate ``off`` and an inherited ``off`` are the same four characters; there is no record
    that distinguishes them, so a user who genuinely wanted the tray-only start loses it once and
    re-ticks one checkbox. That is the price of reversing a default, and it is cheaper than leaving
    every existing install unable to see its own window.

    Runs once, guarded by a revision marker, so it cannot keep overriding a re-chosen ``off``.
    Returns ``True`` only when it actually cleared something.
    """
    try:
        already = keyring.get_password(KEYRING_SERVICE, SHELL_STARTUP_REVISION_KEY)
    except Exception:
        # An unreadable keyring means the stored value is unreadable too, so there is nothing to
        # migrate and nowhere to record that we tried. `should_open_on_startup` falls back to on.
        return False
    if (already or "").strip() == SHELL_STARTUP_REVISION:
        return False

    cleared = False
    try:
        stored = keyring.get_password(KEYRING_SERVICE, "SHELL_ON_STARTUP")
        if (stored or "").strip().lower() == "off":
            keyring.delete_password(KEYRING_SERVICE, "SHELL_ON_STARTUP")
            cleared = True
    except Exception:
        pass

    # Marked even when nothing was cleared: the migration has had its one chance either way, and a
    # retry would only risk overriding a choice made after this ran.
    try:
        keyring.set_password(KEYRING_SERVICE, SHELL_STARTUP_REVISION_KEY, SHELL_STARTUP_REVISION)
    except Exception:
        pass
    return cleared


def welcome_seen() -> bool:
    """Whether the full first-run welcome/permissions dialog was completed."""
    try:
        return keyring.get_password(KEYRING_SERVICE, WELCOME_SEEN_KEY) == "1"
    except Exception:
        return False


def mark_welcome_seen() -> bool:
    try:
        keyring.set_password(KEYRING_SERVICE, WELCOME_SEEN_KEY, "1")
    except Exception:
        return False
    return True


# ── API keys ─────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str | None = resolve_api_key("ANTHROPIC_API_KEY")
"""Optional. Only needed when the Anthropic provider is selected. Plain
vision streaming via messages.stream()."""

ASSEMBLYAI_API_KEY: str | None = resolve_api_key("ASSEMBLYAI_API_KEY")
"""Needed only when STT_PROVIDER=assemblyai (the cloud STT option). Streaming
STT via AssemblyAI u3-rt-pro WebSocket + ForceEndpoint for ~150ms P50 PTT
finalization. Free credit at https://www.assemblyai.com/dashboard/signup.
The local faster-whisper option needs no key."""

CARTESIA_API_KEY: str | None = resolve_api_key("CARTESIA_API_KEY")
"""Needed only when TTS_PROVIDER=cartesia (a cloud TTS option). Streaming TTS
via Cartesia Sonic-3 WebSocket with ~150-250ms TTFB + expressive voice. Free
credits/month at https://play.cartesia.ai/sign-in. The local Kokoro option
needs no key."""

OPENAI_API_KEY: str | None = resolve_api_key("OPENAI_API_KEY")
"""OpenAI native API key (sk-...) for the default OpenAI LLM provider — GPT
vision in the normal pipeline (model set via OPENAI_MODEL_VISION), and
GPT-Realtime speech-to-speech as a separate path. Selected via
LLM_PROVIDER='openai' (default) or 'openai-realtime'. You can also paste an
OpenRouter sk-or- key. Get a native key at
https://platform.openai.com/api-keys."""


# ── OpenRouter dual-SDK routing (BYOK, model-agnostic) ──────────────────────

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
"""OpenRouter's OpenAI-compatible endpoint for Gemini / Grok / Llama / etc.

The existing ANTHROPIC_BASE_URL env var (read natively by the Anthropic SDK)
points at 'https://openrouter.ai/api' for Nimbus models. This constant is the
sibling endpoint for the OpenAI SDK used by GeminiClient (ai.py). Same API
key (ANTHROPIC_API_KEY from .env — which is actually the OpenRouter
sk-or-v1-... key when ANTHROPIC_BASE_URL is set to OpenRouter)."""


# ── LLM model ID (routed by prefix via ai.create_ai_client) ─────────────────

MODEL_ID: str = os.getenv("MODEL_ID", "openai/gpt-4o")
"""OpenRouter-style model ID. Prefix routes to the right SDK via
ai.create_ai_client():
    'anthropic/...'  → AnthropicClient (via anthropic SDK, OpenRouter
                        Anthropic-compat endpoint)
    'google/...'     → GeminiClient (via openai SDK, OpenRouter OpenAI-compat
                        endpoint)
    'openai/...'     → OpenAIVisionClient (via openai SDK, api.openai.com)

Defaults to 'openai/gpt-4o'. Set MODEL_ID in .env to override."""


# ── Screen capture ───────────────────────────────────────────────────────────

CANDIDATE_RESOLUTIONS: list[tuple[int, int]] = [
    (1600, 1200),  # 4:3   = 1.333
    (1920, 1200),  # 16:10 = 1.600
    (1920, 1080),  # 16:9  = 1.778
]

ASPECT_TOLERANCE: float = 0.05
"""How far a candidate resolution's aspect may drift from the monitor's before
``capture.pick_resolution`` abandons the candidate list (T2-8).

The list above spans 1.333 to 1.778, so an ultrawide has no acceptable entry: a 32:9
monitor (3840x1080, aspect 3.556) picks 16:9 and is squashed **2x horizontally**. The
selection algorithm is not at fault -- its docstring correctly states the goal is to avoid
distortion -- the candidate *data* simply cannot express those shapes.

5% relative error is loose enough that every 4:3 / 16:10 / 16:9 monitor keeps exactly
today's behaviour, and tight enough to catch every ultrawide. Verified against the common
resolution table in ``tests/test_capture.py``."""

MAX_MODEL_LONG_EDGE: int = 2560
MAX_MODEL_SHORT_EDGE: int = 1440
"""Bounds for the aspect-preserving fallback used when no candidate fits (T2-8).

A single uniform scale factor is applied so ``scale_x == scale_y`` and the image is
geometrically faithful. On a 3840x1080 monitor this yields 2560x720.

**Measured, not guessed.** Six ground-truth targets on a synthetic 32:9 desktop, error
computed in physical pixels:

| Strategy                    | scale        | hits | median | max   |
|-----------------------------|--------------|------|--------|-------|
| 1920x1080 (squashed, old)   | (2.00, 1.00) | 4/6  | 4 px   | 50 px |
| 2560x720  (aspect, current) | (1.50, 1.50) | 6/6  | 9 px   | 15 px |
| 1920x540   (aspect)         | (2.00, 2.00) | 6/6  | 8 px   | 11 px |
| 3840x1080  (native)         | (1.00, 1.00) | 6/6  | 7 px   | 10 px |

Both small icons were missed under the squash. Note that even 1920x540 -- *fewer* pixels
than the old squashed capture -- beat it outright, so aspect fidelity matters far more here
than resolution and the fix costs no extra tokens."""
"""High-detail screenshot resolutions used for the initial vision pass.

``capture.py`` also retains the original monitor image for a small, targeted
verification crop.  The full image keeps enough detail for normal grounding;
the crop avoids sending an entire 4K desktop again when Nimbus needs to check
a small control.
"""


# ── Hotkey ───────────────────────────────────────────────────────────────────

HOTKEY: str = resolve_setting("HOTKEY", default="ctrl+alt+space")
"""Default push-to-talk hotkey. Ctrl+Alt+Space because:

  1. Alt+Space alone conflicts with the Windows window menu + Copilot
     (Microsoft reassigned it in Windows 11). Making it work
     cleanly needs Win32 RegisterHotKey + GetAsyncKeyState polling for
     release detection -- 8-12h of fragile ctypes code, deferred as a
     future drop-in subclass.
  2. Ctrl+Shift+Space was an earlier pivot target but conflicts with
     Microsoft Excel + Google Sheets "Select entire worksheet" binding.
     Because our pynput listener uses suppress=False (observe-only),
     the spreadsheet underneath ALSO receives the keypress and wipes
     the user's selection every time they invoke Nimbus -- unacceptable
     when working in a spreadsheet.
  3. Fn+Space is firmware-level (handled by the keyboard EC below the
     OS) and invisible to WH_KEYBOARD_LL + pynput. Non-portable even
     where it happens to work. AutoHotkey docs: "the Fn key does not
     (as a general rule) generate any scan code that can be used."
  4. Ctrl+Alt+Space has no known code-level conflicts (Excel, Sheets,
     Windows menu, Copilot, VS Code all clear). Three-finger but all on
     the left side of the keyboard for one-handed ergonomics. suppress=
     False observe-only model carries over unchanged.

  KNOWN SETUP REQUIREMENT: if another app already binds Ctrl+Alt+Space
  (for example a launcher or assistant with a global quick-access
  shortcut), disable that binding — Nimbus's listener is observe-only,
  so both apps receive the keypress otherwise and the other app's popup
  will appear every time you invoke Nimbus. A future Win32 RegisterHotKey
  approach could claim the combo at the OS level to eliminate the conflict.

NEVER ctrl+space (VS Code IntelliSense conflict -- still rejected)."""


# ── STT (AssemblyAI u3-rt-pro streaming) ─────────────────────────────────────

ASSEMBLYAI_SPEECH_MODEL: str = "u3-rt-pro"
"""AssemblyAI Universal-3 realtime-pro streaming model.
~150ms P50 finalization after ForceEndpoint message on hotkey release."""

ASSEMBLYAI_STREAMING_URL: str = "wss://streaming.assemblyai.com/v3/ws"
"""AssemblyAI streaming WebSocket endpoint. Query params are set via SDK."""

AUDIO_SAMPLE_RATE: int = 16_000
"""PCM16 mono at 16kHz. Matches AssemblyAI u3-rt-pro's required sample rate +
Nimbus's audio pipeline + the canonical input shape for every major
streaming STT provider."""

AUDIO_CHUNK_FRAMES: int = 1024
"""sounddevice RawInputStream blocksize. 1024 frames keeps the streaming
WebSocket payload shape consistent across provider swaps."""

# ── Audio level (RMS) filter — drives the waveform widget ──────────────────

AUDIO_POWER_BOOST: float = 10.2
"""Multiplier applied to per-chunk RMS before clamping to [0, 1]. Tuned to
make normal speech register ~0.4-0.8 on the waveform. Tuned empirically."""

AUDIO_POWER_DECAY: float = 0.72
"""Exponential decay floor between chunks: smoothed = max(raw, old * 0.72).
Prevents the UI waveform from jumping DOWN sharply at natural speech pauses —
makes the meter feel responsive to loud sounds but stable at quiet ones."""


# ── TTS (Cartesia Sonic-3 WebSocket streaming) ──────────────────────────────

CARTESIA_MODEL_ID: str = "sonic-3"
"""Cartesia's state-space-model-based TTS. ~90ms model-internal TTFB,
150-250ms real-world through the WebSocket stream + sounddevice playback.
Most expressive 'buddy' voice quality in the cloud TTS field today."""

CARTESIA_VOICE_ID: str = os.getenv(
    "CARTESIA_VOICE_ID",
    "f786b574-daa5-4673-aa0c-cbe3e8534c02",  # "Katie - Friendly Fixer" — Cartesia-recommended for voice agents
)
"""Cartesia voice ID for Sonic-3. The default is a warm, conversational
adult female voice that fits the "buddy next to you" UX.

Swap via .env CARTESIA_VOICE_ID=... to use a different voice. Other strong
candidates from the Cartesia catalog:
  - e8e5fffb-252c-436d-b842-8879b84445b6 — nice young adult female, casual
  - db6b0ed5-d5d3-463d-ae85-518a07d3c2b4 — approachable American female
  - a33f7a4c-100f-41cf-a1fd-5822e8fc253f — expressive, narration/storytelling
  - f786b574-daa5-4673-aa0c-cbe3e8534c02 — enunciating, conversational support
"""

CARTESIA_OUTPUT_SAMPLE_RATE: int = 44_100
"""Cartesia output stream sample rate. 44.1 kHz PCM float32 via sounddevice
OutputStream. Cartesia supports 22.05k / 44.1k / 48k — 44.1k is the most
natural for buddy voice without oversampling cost."""


# ── Provider selection (which subclass app.py constructs at startup) ────────

DEFAULT_LLM_PROVIDER: str = "gemini-native"
"""Single source of truth for the LLM_PROVIDER fallback.

T0-2: three separate call sites used to resolve this setting, and they did NOT
agree — ``config.py`` defaulted to "openai" while both ``app.py`` sites
defaulted to "anthropic". With a populated .env or a completed first-run dialog
the divergence was invisible, but a *cancelled* first-run dialog on a clean
keyring silently selected a different provider than Settings displayed. Every
call site now imports this constant instead of repeating a literal.

**"gemini-native", not "openai".** Two reasons, and the technical one came first.

The native path is simply the better product. ``GeminiNativeClient`` gets the pointer coordinates back
as a **structured function call** (``point_at``, ``draw_box``) rather than as a ``[POINT:x,y]`` tag
parsed out of prose, which is the difference between a contract and a convention. It is also the only
path with per-question thinking budgets, search grounding, Agentic Vision and KB context caching. The
OpenAI path needs the two-stage grid locator to reach comparable accuracy, so defaulting to it meant
shipping the slower, less accurate route by default.

The second reason is cost, measured rather than assumed. Explicit context caching and a zero thinking
budget for perception questions are only available on the native path, and together they are why three
months of development on a product that sends a screenshot with **every single interaction** cost under
twenty dollars in model calls. Defaulting to the OpenAI path would have meant paying full prompt price
for a knowledge base that never changes between questions.

Note the distinction between the two Gemini provider strings: ``gemini-native`` requires a direct
Google AI Studio key and calls the Gemini API; plain ``gemini`` routes through OpenRouter's
compatibility endpoint unless the key it is given happens to be a direct Google one."""

DEFAULT_ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
"""Default Anthropic vision model, in NATIVE dash-versioned form.

T0-1: the previous default was ``model-sonnet-4-6`` — a placeholder left behind
when vendor model names were scrubbed from the repository — so selecting
Anthropic failed on every request. Verified against OpenRouter's live model list
(``anthropic/claude-sonnet-4.6``) and Anthropic's dateless 4.6-generation id
format.

Stored in native form because that is what ``api.anthropic.com`` accepts;
``ai._anthropic_model_for_endpoint`` converts it to OpenRouter's dot-versioned
slug when an ``sk-or-`` key routes there. Override via the Settings model
dropdown (persisted to ``ANTHROPIC_MODEL``) or a ``MODEL_ID`` env var."""

LLM_PROVIDER: str = resolve_setting("LLM_PROVIDER", default=DEFAULT_LLM_PROVIDER)
"""Which AIClient subclass to construct. Defaults to DEFAULT_LLM_PROVIDER;
other providers (Anthropic, Gemini, Ollama) are selectable in the Settings
dialog or via a MODEL_ID env override.

Note: app.py deliberately does NOT import this constant — it calls
resolve_setting fresh so a Settings change applies without a restart. It does
import DEFAULT_LLM_PROVIDER so the fallback stays consistent."""

STT_PROVIDER: str = resolve_setting("STT_PROVIDER", default="assemblyai")
"""Which STT subclass to construct: "assemblyai" (cloud) or
"faster-whisper" (local)."""

TTS_PROVIDER: str = resolve_setting("TTS_PROVIDER", default="cartesia")
"""Which TTS subclass to construct: "cartesia" or "elevenlabs" (cloud),
or "kokoro" (local). User switches via the Settings dialog dropdown."""

DEFAULT_GEMINI_NATIVE_MODEL: str = "gemini-3-flash-preview"
"""Default model for the native Gemini path (T1-1).

Verified live: available on a direct Google key, accepts ``thinking_budget=0`` (the
2.8s time-to-first-token win in T1-7), supports function-calling geometry, and is
the Agentic Vision model for T1-3. ``pro`` models reject a zero budget, which is why
a flash model is the default rather than ``gemini-3.1-pro-preview``.

Separate from ``GEMINI_MODEL_VISION`` because that value carries the ``google/``
namespace required by OpenRouter, while the native SDK wants the bare name."""

GOOGLE_CLOUD_PROJECT: str = resolve_setting("GOOGLE_CLOUD_PROJECT", default="")
"""Google Cloud project id. Setting it switches the native path onto **Vertex AI**.

``google-genai`` reaches two different backends through one SDK: the Gemini API with an
AI Studio key, and Vertex AI with a Google Cloud project. Everything Nimbus depends on
is identical across both — structured function calling, thinking budgets, explicit
context caching, streaming, the Live API — so this is a client-construction change and
nothing else. That is precisely why it is a setting rather than a second code path:
two implementations of "call Gemini" would be one too many.

Empty by default, which keeps the AI Studio key path as the zero-configuration
experience for an individual user. Vertex is what an institution wants, because
authentication is the project's own service account rather than a pasted key, requests
bill and audit against their Cloud account, and data-residency is selectable via
``GOOGLE_CLOUD_LOCATION``.

Authentication is Application Default Credentials, so there is no key to store: either
``gcloud auth application-default login`` on a workstation or an attached service
account on a Cloud host. Nimbus therefore holds **no Google secret at all** on this
path, which is a stronger position than BYOK rather than a weaker one."""

GOOGLE_CLOUD_LOCATION: str = resolve_setting("GOOGLE_CLOUD_LOCATION", default="global")
"""Vertex AI region. Ignored unless ``GOOGLE_CLOUD_PROJECT`` is set.

``global`` is the default because that is where the current Gemini models are served
most reliably; a specific region is the answer when a buyer has a data-residency
requirement, which is exactly the conversation an institutional contract starts with."""

SEARCH_GROUNDING: str = resolve_setting("SEARCH_GROUNDING", default="off")
"""T1-5. Google Search grounding on the native Gemini path.

OFF by default: it adds per-request cost and sends the query to a search backend,
so it must be an explicit opt-in rather than a silent behaviour change. Ignored
entirely by providers that do not support it."""

AGENTIC_VISION: str = resolve_setting("AGENTIC_VISION", default="off")
"""T1-3. Let the model zoom and inspect the screenshot itself instead of Nimbus
running its own second-pass refinement crop.

OFF by default so ``GROUNDING_REFINEMENT`` below keeps today's behaviour until the
T1-8 harness shows the native pass is actually better."""

CODE_EXECUTION: str = resolve_setting("CODE_EXECUTION", default="off")
"""T1-6b. Let the model run code to verify its own arithmetic.

Aimed squarely at the annotation prompt's own worked example — a calculus chain-rule
correction — where the maths is currently model-generated and unverified.

OFF by default for two reasons: it adds sandbox latency, and verified live, enabling it
makes the model switch into document mode and emit LaTeX and markdown headings
(``$f'(x) = u'(x)v(x)$``, ``### 1. Analytical Derivation``). ``ai.strip_non_speech``
removes that noise, but the trade is real, so it stays opt-in."""

KB_CACHE: str = resolve_setting("KB_CACHE", default="on")
"""T1-6a. Cache the knowledge base per app instead of resending it every turn.

ON by default — unlike the other Tier 1 settings — because it is a pure cost and latency
reduction with no behavioural change: an identical prompt is assembled either way. Every
failure path falls back to inline injection, so the worst case is today's behaviour.
Measured: a max-size 60,000-char KB is 10,002 tokens, and caching served 10,008 of
10,013 prompt tokens from cache."""

GROUNDING_REFINEMENT: str = resolve_setting("GROUNDING_REFINEMENT", default="crop")
"""T1-3. How a candidate coordinate gets verified: ``crop`` | ``agentic`` | ``off``.

``crop`` (default) is the existing provider-agnostic two-pass native-resolution
refinement in ``locator.refine_point_via_crop`` — it works today and is kept fully
intact. ``agentic`` delegates to the model's own zoom loop where supported, silently
falling back to ``crop`` otherwise. ``off`` skips verification entirely."""

HISTORY_IMAGE_COUNT: int = resolve_bounded_int_setting(
    "HISTORY_IMAGE_COUNT", default=0, minimum=0, maximum=3,
)
"""T2-4. How many past screenshots to keep in conversation history.

**Defaults to 0, which is exactly today's text-only behaviour** (§1.3). Follow-ups like
*"what about that button you pointed at?"* currently reach a model with no record of the
previous screen, because every history converter drops non-text blocks.

Capped at 3 deliberately. Screenshots dominate token cost, and stale screens actively
mislead: the user has usually moved on, and an old screenshot invites the model to answer
about a window that is no longer there. One is enough for a follow-up.

Interaction with ``T1-6a`` KB caching: none. The cache carries the system instruction only,
while history rides in ``contents``, so a cached prefix is never invalidated by history
images. That was worth checking -- caching a per-turn screenshot would be both wrong and
expensive."""

HISTORY_IMAGE_SCALE: float = 0.5
"""T2-4. Downscale factor applied to a screenshot before it enters history.

A history screenshot only needs to support *recognition* ("the blue button you mentioned"),
not fresh pixel-accurate grounding -- geometry always comes from the current turn's
full-resolution capture. Half scale is a quarter of the pixels and therefore roughly a
quarter of the image tokens."""

KNOWLEDGE_JOURNAL: str = resolve_setting("KNOWLEDGE_JOURNAL", default="on")
"""T3-3. Remember what Nimbus taught you and bring it back on a spaced schedule.

ON by default. It is purely additive -- a new SQLite table alongside the existing ``apps``
table, written only after an interaction has already succeeded -- so nothing existing
changes behaviour, and §1.3's concern does not apply.

Voice commands are matched **locally with no API call**: "quiz me", "what should I review",
"what did we cover today". Turning this off stops both the recording and the commands."""

CAPTIONS: str = resolve_setting("CAPTIONS", default="on")
"""T4-5. Show what Nimbus heard as an on-screen caption.

ON by default. This is the second sanctioned exception to §1.3 alongside ``PRIVACY_GUARD``,
and for a related reason: the capability was already wired and merely printed to a console
that a windowed build does not have, so nobody was relying on the old behaviour. Being
misheard is the most common failure in a voice app and was previously invisible.

Pairs with ``T2-2``: seeing a wrong transcript while the spinner is still turning means Esc
can abort before the wrong answer is spoken.

**How live it is depends on the STT provider, not this setting.** AssemblyAI streams genuine
partials word-by-word; faster-whisper is batch and fires once at release, so the caption
appears next to the thinking spinner instead. Both are useful; only the first is live."""

PRIVACY_GUARD: str = resolve_setting("PRIVACY_GUARD", default="on")
"""T2-1. Suppress screen capture when the foreground window looks sensitive.

**ON by default -- the one deliberate exception to "a new setting must reproduce current
behaviour".** The rule exists to stop a new setting silently changing what users already
rely on. Here the current behaviour is the defect: every push-to-talk captures every
monitor with no content awareness, so a password manager or an open ``.env`` can be sent to
a cloud provider. The Settings dialog's "Nothing leaves your machine" is true of
credentials but not of screen contents, and this makes that claim honest.

Turning it off restores the previous unconditional capture. Policy lives in ``privacy.py``
as a pure function; this only decides whether it is consulted."""

PRIVACY_GUARD_APPS: str = resolve_setting("PRIVACY_GUARD_APPS", default="")
"""T2-1. Extra blocked executables, comma-separated, added to ``privacy.DEFAULT_BLOCKED_APPS``.

Additive rather than replacing, so a user pinning one extra app cannot accidentally
discard the built-in password-manager list."""

PRIVACY_GUARD_TITLES: str = resolve_setting("PRIVACY_GUARD_TITLES", default="")
"""T2-1. Extra blocked window-title regexes, comma-separated. Additive.

An invalid pattern is skipped rather than fatal -- see ``privacy._compile``."""

ANNOTATION_MODE: str = resolve_setting("ANNOTATION_MODE", default="off")
"""Draw-on-screen teaching mode. When 'on', the vision
model is given the annotation system prompt and emits
[ARROW]/[CIRCLE]/[UNDERLINE]/[LABEL] tags that the overlay renders as shapes
(in ADDITION to the [POINT] cursor). When 'off' (default) Nimbus behaves
exactly as before — nothing is overridden. Accuracy comes from the model
(Nimbus is natively precise; GPT-4o/Ollama selectable). Resolved ONCE here at
import (env→keyring→default); app.py reads this cached constant per interaction
rather than calling resolve_setting on the hot path, so there is no
per-interaction keyring read/write latency. Set it in .env and restart to
toggle."""

DIAGNOSTIC_CAPTURE: str = resolve_setting("DIAGNOSTIC_CAPTURE", default="off")
"""Whether Nimbus writes diagnostic screenshots and interaction logs locally.

Disabled by default so normal release use does not retain screen contents or
transcripts. Developers can enable it from Settings when investigating an
issue; the change takes effect after Nimbus restarts.
"""

DIAGNOSTIC_RETENTION_DAYS: int = resolve_bounded_int_setting(
    "DIAGNOSTIC_RETENTION_DAYS", default=7, minimum=1, maximum=365
)
"""How many days enabled diagnostic sessions are retained (1..365)."""


# ── Interface: chat HUD and application shell (SHELL_AND_CHAT.md §10.1) ─────
#
# Declared here so they appear in Settings; both modules already read them through
# ``resolve_setting`` so they worked before this section existed. All are restart-gated and
# carry the ``↻`` marker -- see ``settings_dialog.RESTART_REQUIRED_SETTINGS``.

CHAT_HUD: str = resolve_setting("CHAT_HUD", default="on")
"""Show the floating chat panel with the live conversation (§4).

ON by default. Like ``CAPTIONS``, this is a sanctioned exception to "a new setting must
reproduce current behaviour": nothing existed here before, so nobody can be relying on its
absence, and a voice app that leaves no readable record of what was said is the weaker
default. Turning it off skips constructing the HUD entirely -- no window, no session writes.

The panel is hidden from screen capture (``WDA_EXCLUDEFROMCAPTURE``), so it cannot appear in
the screenshot sent to the model. That is Invariant 1, not a nicety."""

CHAT_HUD_AUTOHIDE_SECONDS: int = resolve_bounded_int_setting(
    "CHAT_HUD_AUTOHIDE_SECONDS", default=45, minimum=0, maximum=3600
)
"""How long the HUD lingers after the last activity. ``0`` = never auto-hide.

45s is long enough to finish reading a spoken answer and short enough that the panel does not
become permanent furniture on a screen the user is trying to work on."""

CHAT_STORE_SCREENSHOTS: str = resolve_setting("CHAT_STORE_SCREENSHOTS", default="off")
"""Keep the screenshot alongside each stored turn.

**OFF, and this is the one in this group to get right.** Everything else here is preference;
this is a privacy commitment. Screen contents on disk is a materially bigger undertaking than
a transcript -- a screenshot can hold a password field, a client's data, a private message --
so it must be an explicit opt-in rather than something inherited from having enabled the HUD.

Independent of ``DIAGNOSTIC_CAPTURE``, which is a developer aid with its own retention."""

CHAT_RETENTION_DAYS: int = resolve_bounded_int_setting(
    "CHAT_RETENTION_DAYS", default=14, minimum=1, maximum=365
)
"""How many days of chat sessions are kept before startup pruning removes them (1..365).

Mirrors ``DIAGNOSTIC_RETENTION_DAYS`` deliberately: two retention windows that behave
differently is a thing to explain, and there is no reason for them to differ."""

SHELL_ON_STARTUP: str = resolve_setting("SHELL_ON_STARTUP", default="on")
"""Open the Nimbus window at launch, rather than starting to the tray.

**ON by default**, reversed from "off". The old reasoning was that "a window appearing uninvited on
every login is how a utility becomes something the user disables" -- sound, but about a situation
Nimbus is not in. Nothing starts Nimbus at login: the installer writes no ``Run`` key and no Startup
shortcut (``installer/nimbus.iss`` has ``[Icons]`` and a post-install ``[Run]`` only). Every launch is
somebody double-clicking a shortcut, and answering that with an invisible process and a tray icon they
have to go hunting for is not restraint, it is the app failing to appear.

The setting stays, because a user who adds Nimbus to their own startup folder wants the old
behaviour, and that is exactly who should be able to turn it off.

Either way push-to-talk works and closing the window only hides it (Invariant 5)."""

NAV_SIDE: str = resolve_setting("NAV_SIDE", default="left")
"""Which side the window's navigation rail sits on: ``left`` | ``right`` (§0.3).

``left`` because every desktop app the user already has puts primary navigation there, and a
right-hand rail conventionally holds *contextual* content -- inspectors, properties, activity.
The brief asked for the right, so this exists to settle that in one value rather than an
argument. Anything other than ``right`` reads as ``left``: an unrecognised value must not
produce a third layout."""

REDUCE_MOTION: str = resolve_setting("REDUCE_MOTION", default="auto")
"""Honour reduced-motion preferences: ``auto`` | ``on`` | ``off``.

``auto`` follows Windows' own ``SPI_GETCLIENTAREAANIMATION``, which is the right default --
vestibular sensitivity is real, Windows already exposes the preference, and an app that
ignores it is making a choice. ``on`` forces motion off, ``off`` forces it on for a user whose
system setting says one thing and whose preference for this app says another.

Read by ``theme.animations_enabled``, which every duration passes through, so a single value
collapses every animation in the shell and the HUD to 0ms rather than stripping them
individually."""


# ── ElevenLabs TTS (opt-in alternative to Cartesia) ─────────────────────────

ELEVENLABS_API_KEY: str | None = resolve_api_key("ELEVENLABS_API_KEY")
"""Optional. Required only when TTS_PROVIDER='elevenlabs'. 10k chars/month
free tier at https://elevenlabs.io/app/sign-up — no credit card."""

ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
"""ElevenLabs Flash v2.5 — ~75ms model TTFB. ElevenLabs officially
recommends Flash over Turbo v2.5 for low-latency voice agents.
Verified against ElevenLabs Python SDK 2.45.0 (
``client.text_to_speech.stream`` accepts ``model_id="eleven_flash_v2_5"``).
"""

ELEVENLABS_VOICE_ID: str = os.getenv(
    "ELEVENLABS_VOICE_ID",
    "21m00Tcm4TlvDq8ikWAM",  # Rachel — American female, conversational
)
"""ElevenLabs voice ID for the buddy persona. Default Rachel matches
Cartesia "Brooke - Big Sister" warmth (conversational adult female).
Verified against ElevenLabs voice catalog
(https://elevenlabs.io/app/voice-library) — Rachel's official voice ID
is ``21m00Tcm4TlvDq8ikWAM``. If swapping to a different voice via env
override, copy the ID from the voice library page (NOT the URL slug)."""

ELEVENLABS_OUTPUT_SAMPLE_RATE: int = int(
    os.getenv("ELEVENLABS_OUTPUT_SAMPLE_RATE", "22050")
)
"""ElevenLabs PCM sample rate. Defaulted to 22050 because 44.1kHz PCM
requires Pro tier. ElevenLabs PCM is int16 (NOT float32 like Cartesia),
so playback path converts inline: np.frombuffer(chunk, np.int16).astype(
np.float32) / 32768.0."""


# ── Ollama (local LLM via Ollama server) ─────────────────────────────

OLLAMA_HOST: str = os.getenv(
    "OLLAMA_HOST", resolve_setting("OLLAMA_HOST", "http://localhost:11434")
)
"""Local Ollama server URL. Default matches Ollama's out-of-the-box
``ollama serve`` binding. Set in .env or Settings dialog to point at a
different host (e.g. another machine on LAN). Supports unauthenticated
local Ollama — no API-key field needed."""

OLLAMA_MODEL_VISION: str = os.getenv(
    "OLLAMA_MODEL_VISION",
    resolve_setting("OLLAMA_MODEL_VISION", "llava:7b"),
)
"""Ollama vision-capable model used when screenshots are present.
Default ``llava:7b`` works on every Ollama version with vision support
(~4.5 GB). ``llama3.2-vision`` is more accurate but needs Ollama
>=0.4.x (uses ``mllama`` arch). User can switch via Settings dialog;
``ollama_health.check_model_compatibility`` warns on mismatch."""

OLLAMA_MODEL_TEXT: str = os.getenv(
    "OLLAMA_MODEL_TEXT",
    resolve_setting("OLLAMA_MODEL_TEXT", "llama3.2"),
)
"""Ollama text-only model used when no screenshots are sent (rare in
Nimbus's PTT flow but kept for parity with the vision/text split).
Defaults to plain ``llama3.2`` (3B, ~2 GB)."""


# ── OpenAI (native API — gpt-5.4 vision + GPT-Realtime) ──────────────

OPENAI_MODEL_VISION: str = os.getenv(
    "OPENAI_MODEL_VISION",
    resolve_setting("OPENAI_MODEL_VISION", "gpt-5.4"),
)
# gpt-5.4 is pixel-accurate at GUI grounding (85.4% on ScreenSpot-Pro) and
# returns precise [POINT] tags directly, so the OpenAI vision path does not
# need the grid-locator (it auto-skips when a [POINT] tag is present). Set
# OPENAI_MODEL_VISION in .env to override (e.g. =gpt-4o).
"""OpenAI vision model for the normal pipeline (LLM_PROVIDER='openai').
Defaults to gpt-5.4, which is pixel-accurate at grounding and emits
[POINT:x,y:label] directly. Weaker models (e.g. gpt-4o) can fall back to
the two-stage grid-locator (locator.py) for pointing. Routed via the
``openai/`` MODEL_ID prefix in create_ai_client."""

OPENAI_REALTIME_MODEL: str = os.getenv(
    "OPENAI_REALTIME_MODEL",
    resolve_setting("OPENAI_REALTIME_MODEL", "gpt-realtime-2"),
)
"""OpenAI GPT-Realtime model for the speech-to-speech path
(LLM_PROVIDER='openai-realtime'). ``gpt-realtime-2`` is GPT-5-class,
continuous-stream voice — near-zero latency, sees the screenshot, reasons,
and emits a pointing target via the point_at function call. This path
bypasses the STT→AIClient→TTS chain entirely (realtime.py owns the
WebSocket session + audio I/O). Coordinates are refined via the
grid-locator, same as the GPT-4o path."""


# -- Local STT (faster-whisper, opt-in offline, no API key) ------------------

FASTER_WHISPER_MODEL: str = os.getenv(
    "FASTER_WHISPER_MODEL", resolve_setting("FASTER_WHISPER_MODEL", "base.en")
)
"""faster-whisper model size. 'base.en' is the low-latency English default
(~150MB, downloads to the HF cache on first use). 'small.en' is more accurate
but slower. Local offline STT needs no API key."""

FASTER_WHISPER_DEVICE: str = os.getenv(
    "FASTER_WHISPER_DEVICE", resolve_setting("FASTER_WHISPER_DEVICE", "cpu")
)
"""'cpu' (portable default) or 'cuda' if the user has an NVIDIA GPU."""

FASTER_WHISPER_COMPUTE: str = os.getenv(
    "FASTER_WHISPER_COMPUTE", resolve_setting("FASTER_WHISPER_COMPUTE", "int8")
)
"""CTranslate2 compute type. 'int8' is fast + low-memory on CPU."""


# -- Local TTS (Kokoro-82M via ONNX, opt-in offline, no API key) -------------

KOKORO_VOICE: str = os.getenv("KOKORO_VOICE", resolve_setting("KOKORO_VOICE", "af_heart"))
"""Kokoro voice id. 'af_heart' is a warm conversational female voice."""

KOKORO_OUTPUT_SAMPLE_RATE: int = 24_000
"""Kokoro-82M output sample rate (24kHz float32)."""

_DEFAULT_KOKORO_DIR = Path.home() / ".nimbus" / "kokoro"
KOKORO_CACHE_DIR: Path = Path(os.getenv("KOKORO_CACHE_DIR", str(_DEFAULT_KOKORO_DIR)))
"""Where the Kokoro onnx + voices files download on first use (~336MB total)."""


# -- Google Gemini (cloud vision via OpenRouter) ------------------

GEMINI_API_KEY: str | None = resolve_api_key("GEMINI_API_KEY")
"""OpenRouter key (sk-or-) for Gemini. Get one at https://openrouter.ai/keys.
Gemini routes through OpenRouter's OpenAI-compat endpoint (see GeminiClient)."""

GEMINI_MODEL_VISION: str = os.getenv(
    "GEMINI_MODEL_VISION",
    resolve_setting("GEMINI_MODEL_VISION", "google/gemini-3.1-pro-preview"),
)
"""Gemini model. Default is 'google/gemini-3.1-pro-preview' — the most
pixel-accurate Gemini for pointing (84.4% ScreenSpot-Pro, the Pro tier).
The default was chosen over 'google/gemini-3.5-flash' because Flash's
coordinates were noticeably off in real use; there is no 3.5-pro, so the
3.1 Pro preview is the strongest grounding option on OpenRouter. The full
'-preview' suffix is the valid OpenRouter slug (bare 'google/gemini-3.1-pro'
404s). Cheaper/faster alternative via env: GEMINI_MODEL_VISION=google/gemini-3.5-flash."""


# ── Memory ───────────────────────────────────────────────────────────────────

_DEFAULT_MEMORY_DIR = Path.home() / ".nimbus"

MEMORY_DIR: Path = Path(os.getenv("MEMORY_DIR", str(_DEFAULT_MEMORY_DIR / "memory")))
"""Where per-app markdown files live. One .md per Windows app executable."""

INDEX_DB_PATH: Path = Path(os.getenv("INDEX_DB_PATH", str(_DEFAULT_MEMORY_DIR / "index.db")))
"""SQLite index at ~/.nimbus/index.db. Fast lookup for apps + interaction counts."""

INSIGHTS_PATH: Path = Path(os.getenv("INSIGHTS_PATH", str(_DEFAULT_MEMORY_DIR / "insights.md")))
"""Path for an optional memory health-check summary."""

MEMORY_RECALL_MAX_CHARS: int = 1500
"""Max characters of recalled memory to inject into the user message per request.
~1500 chars = last 5-6 interactions. Persistent per-app memory is a
differentiator, but too much context slows the model down."""


# ── Knowledge base (user-uploadable per-app curated docs) ────────────────────

def _resolve_kb_dir(candidate: Path, fallback: Path) -> Path:
    """Create the knowledge folder, falling back when Documents is blocked.

    Managed Windows profiles can expose ``~/Documents`` but reject child
    creation with FileNotFoundError. Nimbus must use one consistent writable
    path for both the tray shortcut and runtime KB recall.
    """
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


_KB_REQUESTED_DIR = Path(
    os.getenv("KB_DIR", str(Path.home() / "Documents" / "Nimbus Wiki"))
)
_KB_FALLBACK_DIR = Path(__file__).resolve().parent / "Nimbus Wiki"
KB_DIR: Path = _resolve_kb_dir(_KB_REQUESTED_DIR, _KB_FALLBACK_DIR)
"""User drops a single .md file here per app, named to match the .exe
basename (e.g. ``myapp.exe.md`` for MyApp, ``fusion360.exe.md`` for
Fusion 360). Nimbus reads it on every PTT and injects as authoritative
reference in Nimbus's system prompt.

Default location is visible in File Explorer (NOT a hidden ``.``-prefixed
folder) so users can find + edit + delete the files without terminal
gymnastics. Mirrors memory.py's transparency contract: human-readable,
hand-editable, no vector DB.

A simple flat layout (one file per app), right-sized for this use case. If
Windows blocks the Documents folder, Nimbus uses a visible ``Nimbus Wiki``
folder beside the application instead."""

KB_RECALL_MAX_CHARS: int = 60_000
"""Max characters of curated KB content to inject per request. ~15K
tokens, ~⅓ of Nimbus's context budget. Over-budget files tail-truncate
(same behavior as memory.recall). Anthropic supports up to 4
``cache_control`` breakpoints per request; injecting KB adds a 2nd
system block alongside the persona block, leaving 2 slots for the
user-message memory prefix + the implicit automatic-cache slot."""


# ── Overlay ──────────────────────────────────────────────────────────────────

POINTER_ANIMATION_MS: int = 400
"""QPropertyAnimation duration for pointer movement. 400ms feels responsive,
not jittery."""


# ── Latency targets ──────────────────────────────────────────────────────────

E2E_LATENCY_BUDGET_S: float = 1.5
"""Target perceived latency from hotkey release to first audible word.
Expected breakdown: ~150ms STT (AssemblyAI ForceEndpoint) + ~500-800ms
vision-model TTFT + ~200ms Cartesia Sonic-3 TTFB - ~300ms sentence-
streaming overlap = ~800-1200ms."""
