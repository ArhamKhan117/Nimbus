"""First-launch + tray-menu settings dialog for Nimbus.

Two classes, one implementation (SHELL_AND_CHAT.md `S-4`):

* ``SettingsForm`` -- every setting, as a hostable ``QWidget`` with no scroll area and no
  buttons of its own. Talks to whoever hosts it through ``save() -> bool``,
  ``is_valid()``, ``local_data_cleared`` and three signals.
* ``SettingsDialog`` -- the modal host. Adds the scroll area, the button box **outside** it,
  screen-aware sizing, and accept/reject.

The shell's Settings page (``shell.pages.settings``) is the second host. Deliberately not a
second implementation: this module carries the provider/model/key matrix, the OpenRouter
key-reuse rule, keyring persistence, hotkey capture, the Privacy group, the experimental group
and the restart labels, and rewriting any of it "nicely" for the shell would have dropped
several of those silently.

Modal QDialog with three password fields (Anthropic / AssemblyAI /
Cartesia API keys). Save persists to Windows Credential Manager via
keyring. App refuses to start until at least the three required keys
are present (env or keyring).

The dialog is reusable: it's shown at first-launch when keys are
missing, AND from the tray menu as a "Settings..." entry. Users can
swap keys (rotation) without editing .env.

Ergonomics:
- Password-mode fields (echoed as bullets), but with a checkbox to
  reveal so users can paste-verify the long sk-* / cartesia-* tokens.
- Existing keyring values are pre-populated so users see a partial
  preview (last 4 chars) without exposing the full secret on screen.
- Save button is disabled until all three fields are non-empty.

Threading: this dialog runs on the Qt main thread (it's modal). No
threading concerns. ``config.store_setting`` is synchronous + ~10ms
on Windows DPAPI — no async needed.

Every write goes through ``config.store_setting`` rather than ``keyring.set_password``, and that is
not a wrapper for tidiness. A user reported that nothing in Settings survived a restart, and the
cause was a credential vault whose writes returned success and stored nothing. ``store_setting``
reads back what it wrote and falls back to a sealed file when the vault cannot be believed. See its
docstring for the measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

import keyring

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import theme
from config import KEYRING_SERVICE, store_setting


HINT_QSS = f"color: {theme.TEXT_MUTED};"
"""Explanatory small print inside the form.

Was ``color: gray``, which is a *light*-theme colour: it was chosen when this dialog ran on
the Windows palette, and against the shell's ``BG_BASE`` it reads as low-contrast smudge. The
theme constant is measured at 5.4:1 on ``BG_ELEVATED`` (``theme.TEXT_MUTED``'s docstring), so
the hint text is legible on both hosts without either of them special-casing it."""


_LOCAL_KEYRING_ENTRIES = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "ASSEMBLYAI_API_KEY", "CARTESIA_API_KEY", "ELEVENLABS_API_KEY",
    "LLM_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "ANNOTATION_MODE",
    "HOTKEY", "OLLAMA_HOST", "OLLAMA_MODEL_VISION", "OLLAMA_MODEL_TEXT",
    "OPENAI_MODEL_VISION", "ANTHROPIC_MODEL", "FASTER_WHISPER_MODEL",
    "FASTER_WHISPER_DEVICE", "FASTER_WHISPER_COMPUTE", "KOKORO_VOICE",
    "DIAGNOSTIC_CAPTURE", "DIAGNOSTIC_RETENTION_DAYS",
    # Native Gemini + experimental toggles, so "Clear all Nimbus local data" really
    # does return the app to a first-run state.
    "GEMINI_NATIVE_MODEL", "CODE_EXECUTION", "SEARCH_GROUNDING",
    "AGENTIC_VISION", "GEMINI_LIVE", "GEMINI_LIVE_MODEL",
    "GROUNDING_REFINEMENT", "KB_CACHE",
    # T2-1. Clearing these restores the ON default rather than leaving the guard
    # switched off from a previous session -- a wipe must not silently weaken privacy.
    "PRIVACY_GUARD", "PRIVACY_GUARD_APPS", "PRIVACY_GUARD_TITLES",
)


def clear_local_nimbus_data(data_root: Path, kb_dir: Path) -> list[str]:
    """Clear Nimbus-owned files and saved settings, returning any failures.

    The directory roots themselves are preserved so a running process can
    recreate a database or diagnostics folder cleanly. User-created exports
    are deliberately excluded: they are explicit documents, not app state.
    """
    failures: list[str] = []
    for root in (data_root, kb_dir):
        if not root.exists():
            continue
        try:
            children = list(root.iterdir())
        except OSError as exc:
            failures.append(f"{root}: {exc}")
            continue
        for child in children:
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                failures.append(f"{child}: {exc}")
    for name in _LOCAL_KEYRING_ENTRIES:
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except Exception:
            # Missing entries and locked credential stores are non-fatal; the
            # filesystem result is still useful and reported separately.
            continue
    return failures


# pre-populated Ollama vision model suggestions
# in the dropdown. `llava:7b` first since it's the new default (works
# on all Ollama versions with vision). User can also type a custom
# model name — the combobox is editable.
_OLLAMA_MODEL_SUGGESTIONS: tuple[str, ...] = (
    "llava:7b",
    "llama3.2-vision",
    "qwen2.5-vl",
    "llava-llama3",
)


# T0-1: Anthropic model choices, in NATIVE dash-versioned form (what
# api.anthropic.com accepts). ai._anthropic_model_for_endpoint converts to
# OpenRouter's dot-versioned slug when an sk-or- key routes there. The combo is
# editable so a user can type a newer id without waiting for a Nimbus release.
_ANTHROPIC_MODEL_CHOICES: tuple[tuple[str, str], ...] = (
    ("Claude Sonnet 4.6 (default)", "claude-sonnet-4-6"),
    ("Claude Opus 4.6", "claude-opus-4-6"),
    ("Claude Haiku 4.5", "claude-haiku-4-5"),
)


# T1-1: native Gemini models, BARE names (the native SDK rejects a "google/" slug).
# All verified present on a real direct key. Flash models lead because `pro` models
# reject thinking_budget=0, forfeiting the T1-7 latency win.
# Experimental toggles: (keyring setting, checkbox label, tooltip).
#
# All default OFF. Every tooltip states the trade-off honestly, including where a
# capability measured WORSE than the default — a settings dialog that oversells an
# experimental switch is how users end up blaming the app for a choice it nudged them
# into. Requires the native Gemini provider except where noted.
RESTART_REQUIRED_SETTINGS: frozenset[str] = frozenset({
    # Resolved once at import and cached deliberately, to keep keyring writes off the hot
    # path. See config.py for each.
    "HOTKEY", "ANNOTATION_MODE", "CAPTIONS", "KNOWLEDGE_JOURNAL",
    "PRIVACY_GUARD", "PRIVACY_GUARD_APPS", "PRIVACY_GUARD_TITLES",
    "CODE_EXECUTION", "SEARCH_GROUNDING", "AGENTIC_VISION", "GEMINI_LIVE",
    "GEMINI_LIVE_MODEL", "GROUNDING_REFINEMENT", "KB_CACHE", "HISTORY_IMAGE_COUNT",
    # SHELL_AND_CHAT.md §10.1. The HUD and the window are both constructed once at startup
    # from these, and NAV_SIDE / REDUCE_MOTION are read while building the widget tree.
    "CHAT_HUD", "CHAT_HUD_AUTOHIDE_SECONDS", "CHAT_STORE_SCREENSHOTS",
    "CHAT_RETENTION_DAYS", "SHELL_ON_STARTUP", "NAV_SIDE", "REDUCE_MOTION",
    # Providers and models are read when the client is constructed at startup.
    "LLM_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER",
    "GEMINI_NATIVE_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL_VISION",
    "OLLAMA_MODEL_VISION", "GEMINI_MODEL_VISION", "OLLAMA_HOST",
})
"""Settings that only take effect after a restart (T4-7).

**Why this list exists rather than a live-reload mechanism.** The caching is deliberate:
``resolve_setting`` writes to the keyring whenever a value came from the environment, so
re-resolving per interaction would put a Credential Manager write on the hottest path in the
app. Removing the cache would be the wrong fix.

So the minimum viable version of T4-7 is honesty: say which settings need a restart, rather
than letting the user toggle something, see no change, and conclude it is broken. That
confusion got materially worse as Tiers 1-3 added the experimental group, the Privacy Guard,
captions and the journal -- eleven new toggles, none of them live.

API keys are deliberately absent: they are read per request, so a new key works immediately.
"""

RESTART_MARKER = " \u27f3"
"""Appended to a label whose setting needs a restart. A symbol rather than the word "(restart)" so it
survives being appended to already-long checkbox labels.

``U+27F3`` CLOCKWISE GAPPED CIRCLE ARROW, from ``Segoe UI Symbol`` -- named in
``theme.SYMBOL_FONT_STACK`` rather than left to fallback.

## Three glyphs were tried; the deciding measurement was scale, not shape

Ink height against the **cap height of the surrounding text**, at 10 / 11 / 15pt:

    U+21BB  circular arrow, thin        0.89 / 0.82 / 0.93   28px of ink at 11pt
    U+27F3  circular arrow, gapped      0.89 / 0.82 / 0.86   36px of ink at 11pt
    U+E72C  Refresh, icon font          1.44 / 1.36 / 1.43   65px of ink at 11pt
    U+2192  rightwards arrow            0.44 / 0.45 / 0.36   15px of ink at 11pt

``U+21BB`` shipped first and was reported as pixelated. ``U+2192`` replaced it and is crisp, but a
straight arrow does not say "reloads on next start". ``U+E72C`` from Windows' icon font is the right
*shape* and was reported as too big -- correctly: an icon font is drawn to fill the em box, while a
text character's capitals occupy roughly 70% of it, so any icon glyph inline is ~40% larger than the
letters beside it. There is no way to shrink one run of a plain-text label, and ``QCheckBox`` -- which
carries nine of these markers -- does not support rich text, so a smaller size was not available.

``U+27F3`` is the resolution: the circular shape, at text scale, with **29% more ink than U+21BB** at
the same size because the gapped form is drawn with fewer, heavier strokes. That extra weight is what
the "pixelated" complaint was really about -- a thin open circle at 8px has almost nothing to render.

Verified rather than assumed: 36px of ink against 52px for a guaranteed-notdef codepoint, so it is a
real glyph and not a box; and its tight rect bottom sits at the baseline against a 3px descent, so it
does not clip -- which is what the original "cut off at the bottom" was."""

RESTART_NOTE = (
    f"Changes to settings marked{RESTART_MARKER} take effect the next time Nimbus starts.")
"""Shown once near the save button, so the marker is explained rather than mysterious.

Built from ``RESTART_MARKER`` rather than repeating the character, so the legend cannot end up
explaining a symbol the labels no longer use."""


def restart_marker_for(setting: str) -> str:
    """Return the restart marker for ``setting``, or ``""`` (T4-7).

    Pure lookup so the labelling is testable without constructing the dialog, and so a
    setting can never be marked inconsistently in two places.
    """
    return RESTART_MARKER if setting in RESTART_REQUIRED_SETTINGS else ""


_QT_PURE_MODIFIER_KEYS: frozenset[int] = frozenset({
    0x01000020,  # Key_Shift
    0x01000021,  # Key_Control
    0x01000022,  # Key_Meta
    0x01000023,  # Key_Alt
    0x01001103,  # Key_AltGr
})
"""Keys that are modifiers in their own right (T2-7).

Pressed alone they mean "the user is still assembling the chord", so the capture widget
waits rather than treating them as the final key.

**Belt-and-braces, not load-bearing** -- verified by mutation testing: deleting the check
that uses this set leaves all 45 T2-7 tests green, because every one of these key codes
already falls outside the accepted trigger ranges and reaches the same ``return None`` at
the bottom of the function. It is kept for two reasons: it states the intent at the top of
the function instead of leaving it as an accident of range arithmetic, and it keeps
modifiers excluded if the accepted ranges are ever widened. Labelled honestly so nobody
mistakes it for the thing making the behaviour correct.

Written as literals because the values are stable Qt constants and the set stays readable
in a test failure."""

_QT_SPECIAL_TRIGGERS: dict[int, str] = {
    0x20: "space",        # Key_Space
    0x01000004: "enter",  # Key_Return
    0x01000005: "enter",  # Key_Enter (numpad)
    0x01000001: "tab",    # Key_Tab
    # Key_Backtab. Windows reports Shift+Tab as Backtab rather than Tab, so without this
    # a shift+tab chord would be silently unrecordable. Verified against live Qt values.
    0x01000002: "tab",
}
"""Qt key code -> the token ``hotkey.parse_hotkey`` expects."""

_QT_KEY_F1 = 0x01000030
_QT_KEY_F12 = 0x0100003B


def qt_key_event_to_hotkey_string(key: int, modifiers) -> str | None:
    """Translate a Qt key event into a ``parse_hotkey``-compatible chord (T2-7).

    Returns ``None`` when the event should not end the capture -- a bare modifier press, or
    a final key Nimbus cannot bind. ``None`` means "keep waiting", not "error", so the user
    can hold Ctrl and Alt and then reach for Space without the widget guessing early.

    Pure function taking primitives, so the entire mapping is unit-testable without
    constructing widgets or pumping a Qt event loop -- which is where the real risk lives,
    since this is the one place two key-code vocabularies meet.

    **Two mappings that are not obvious**, both confirmed against live Qt values rather
    than assumed:

    * ``Key_Backtab`` is emitted for Shift+Tab instead of ``Key_Tab``.
    * On Windows, **AltGr arrives as Ctrl+Alt**. That is harmless here rather than
      something to correct: ``hotkey._is_alt`` already lumps ``alt_gr`` with ``alt``, so
      the recorded ``ctrl+alt+<key>`` is exactly what the listener will match at runtime.

    Deliberately does NOT validate the chord. ``parse_hotkey`` owns validation, the
    conflict warnings, and the messages; duplicating any of that here would let the two
    drift apart.
    """
    from PyQt6.QtCore import Qt

    key = int(key)
    if key in _QT_PURE_MODIFIER_KEYS:
        return None

    names = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        names.append("ctrl")
    if modifiers & Qt.KeyboardModifier.AltModifier:
        names.append("alt")
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        names.append("shift")

    trigger = _QT_SPECIAL_TRIGGERS.get(key)
    if trigger is None:
        if _QT_KEY_F1 <= key <= _QT_KEY_F12:
            trigger = f"f{key - _QT_KEY_F1 + 1}"
        elif ord("A") <= key <= ord("Z"):
            trigger = chr(key).lower()
        elif ord("0") <= key <= ord("9"):
            trigger = chr(key)
        else:
            # Anything else (punctuation, media keys, Key_unknown) is not bindable.
            return None

    # Shift+Tab already encodes shift in the Backtab key code; emitting "shift" as well
    # would be correct but reads oddly, and parse_hotkey accepts either. Kept as-is so the
    # displayed chord matches what the user physically pressed.
    return "+".join([*names, trigger])


class HotkeyCaptureButton(QPushButton):
    """Click, press a chord, and it records it (T2-7).

    Replaces typing ``ctrl+alt+space`` into a text box -- a field where the user has to know
    Nimbus's spelling, and where a typo surfaces only as a validation error on save.

    **No new validation logic.** ``parse_hotkey`` already owns the accepted grammar, the
    normalised display form, and the tailored conflict warnings for ``alt+space``,
    ``ctrl+space`` and ``ctrl+shift+space``. This widget captures keystrokes, hands the
    string to ``parse_hotkey``, and shows whatever it says. Re-implementing any of that here
    would let the dialog and the listener disagree about what is legal.

    Emits ``captured(str)`` with the normalised chord on success.
    """

    captured = pyqtSignal(str)

    _PROMPT = "Press a chord\u2026  (Esc to cancel)"

    def __init__(self, initial: str = "", parent=None) -> None:
        super().__init__(parent)
        self._capturing = False
        self._value = initial
        self.setCheckable(True)
        self.setToolTip(
            "Click, then hold the chord you want, for example Ctrl+Alt+Space.\n"
            "Needs a modifier (Ctrl, Alt or Shift) plus Space, Enter, Tab, A-Z, 0-9,\n"
            "or F1-F12. Press Esc to cancel.\n\n"
            "Takes effect the next time Nimbus starts."
        )
        self._refresh_label()
        self.clicked.connect(self._on_clicked)

    # -- public API -----------------------------------------------------------

    def value(self) -> str:
        """The currently displayed chord."""
        return self._value

    def set_value(self, chord: str) -> None:
        self._value = chord
        self._refresh_label()

    @property
    def capturing(self) -> bool:
        return self._capturing

    # -- internals ------------------------------------------------------------

    def _refresh_label(self) -> None:
        self.setText(self._PROMPT if self._capturing else (self._value or "Click to set"))

    def _on_clicked(self) -> None:
        self._capturing = self.isChecked()
        if self._capturing:
            self.setFocus()
        self._refresh_label()

    def _stop_capturing(self) -> None:
        self._capturing = False
        self.setChecked(False)
        self._refresh_label()

    def focusNextPrevChild(self, forward: bool) -> bool:
        """Stop Qt stealing Tab for focus navigation while capturing (T2-7).

        Without this, ``keyPressEvent`` never sees Tab at all -- QWidget consumes it first
        -- so a chord ending in Tab would be impossible to record and the widget would just
        appear to ignore the key.
        """
        if self._capturing:
            return False
        return super().focusNextPrevChild(forward)

    def keyPressEvent(self, event) -> None:
        from PyQt6.QtCore import Qt

        if not self._capturing:
            super().keyPressEvent(event)
            return

        if int(event.key()) == int(Qt.Key.Key_Escape):
            self._stop_capturing()
            event.accept()
            return

        chord = qt_key_event_to_hotkey_string(event.key(), event.modifiers())
        if chord is None:
            # Still assembling the chord, or an unbindable key. Swallow it either way:
            # letting it propagate while armed would trigger dialog buttons.
            event.accept()
            return

        try:
            from hotkey import parse_hotkey
            normalised = parse_hotkey(chord).display
        except ValueError as exc:
            # Show parse_hotkey's own message and stay armed so the user can simply try
            # another chord without clicking again.
            self.setText(str(exc))
            self.setStyleSheet(f"color: {theme.DANGER};")
            event.accept()
            return

        self._value = normalised
        self.setStyleSheet("")
        self._stop_capturing()
        self.captured.emit(normalised)
        event.accept()


_EXPERIMENTAL_TOGGLES: tuple[tuple[str, str, str], ...] = (
    (
        "CODE_EXECUTION",
        "Verify maths by running code",
        "Lets the model actually execute code to check its arithmetic instead of\n"
        "reasoning it out. Good for maths and data questions — it computes a\n"
        "derivative and numerically verifies it rather than guessing.\n\n"
        "Cost: adds sandbox latency. Also makes the model write more formally, so\n"
        "Nimbus strips the LaTeX and markdown it starts producing before speaking.\n\n"
        "Requires: Google Gemini (native SDK).",
    ),
    (
        "SEARCH_GROUNDING",
        "Search the web for current information",
        "Lets the model run a Google search when a question needs up-to-date facts,\n"
        "such as current version numbers or whether an API is deprecated. Sources\n"
        "are written to the debug log, never spoken aloud.\n\n"
        "NOT RECOMMENDED YET — measured honestly: on its own this works and returns\n"
        "correct, cited answers. Combined with Nimbus's personality prompt AND a\n"
        "screenshot, citations disappeared and one answer came back WRONG (said\n"
        "Python 3.12 when the correct answer was 3.14). Enable only to help\n"
        "investigate that interaction.\n\n"
        "Requires: Google Gemini (native SDK).",
    ),
    (
        "AGENTIC_VISION",
        "Let the model zoom into the screen itself",
        "Instead of Nimbus cropping a region and asking a second time, the model\n"
        "inspects the screenshot itself — zooming in on small icons and fine text\n"
        "before answering where something is.\n\n"
        "May improve accuracy on small targets, and replaces an extra round trip.\n"
        "Unmeasured: the benchmark harness exists but has not been run, so this is\n"
        "genuinely untested against the current crop-and-recheck approach. If\n"
        "pointing gets worse, turn it back off.\n\n"
        "Requires: Google Gemini (native SDK).",
    ),
    (
        "GEMINI_LIVE",
        "Live voice conversation (speech-to-speech)",
        "Replaces the record-then-respond loop with a continuous voice session: you\n"
        "speak and the model answers in its own voice in real time, with no separate\n"
        "speech-to-text or text-to-speech step.\n\n"
        "Lowest possible latency and the most natural feel, but it bypasses your\n"
        "chosen STT and TTS providers entirely and is the least tested path in\n"
        "Nimbus. If you hear nothing, or the microphone appears stuck, turn this off\n"
        "and restart — the normal pipeline is unaffected.\n\n"
        "Requires: Google Gemini (native SDK) and a working microphone.",
    ),
)


_GEMINI_NATIVE_MODEL_CHOICES: tuple[tuple[str, str], ...] = (
    ("Gemini 3 Flash (default, fastest)", "gemini-3-flash-preview"),
    ("Gemini 3.1 Flash Lite", "gemini-3.1-flash-lite"),
    ("Gemini 3 Pro", "gemini-3-pro-preview"),
    ("Gemini 3.1 Pro", "gemini-3.1-pro-preview"),
)


# --- provider category data model ---------------------------------
#
# Drives 3-row progressive-disclosure UX in the dialog: pick provider per
# category (LLM/STT/TTS) from a dropdown, only that provider's API key field
# is visible. Fixes the previous flat 3-required-field layout that would
# have grown to 6 fields with ElevenLabs (and 7+ with Deepgram).


@dataclass(frozen=True)
class _Model:
    """One selectable model for a provider. ``model_id`` is the bare model
    string passed to the SDK / stored in the provider's model_setting slot
    (e.g. "gpt-5.4", "llava:7b")."""

    display_name: str           # e.g. "GPT-5.4 (default)"
    model_id: str               # e.g. "gpt-5.4"


@dataclass(frozen=True)
class _Provider:
    """Single provider in a category. ``provider_id`` is the lowercase
    string used as the value of LLM_PROVIDER / STT_PROVIDER / TTS_PROVIDER
    config + the dropdown's data slot. ``api_key_env_var`` is BOTH the
    env-var name AND the keyring slot name (they share namespace by
    convention — see config.resolve_api_key).

    optional per-provider model picker. ``models`` (if non-empty)
    drives a contextual model dropdown shown only when this provider is
    selected; the chosen model_id persists to ``model_setting`` (a keyring
    slot like "ANTHROPIC_MODEL"). ``models_editable`` lets the user type a
    custom model (Ollama). ``hides_other_categories`` collapses the STT+TTS
    rows when selected (GPT-Realtime does speech end-to-end)."""

    provider_id: str            # e.g. "anthropic", "elevenlabs"
    display_name: str           # e.g. "Anthropic", "ElevenLabs"
    api_key_env_var: str        # e.g. "ANTHROPIC_API_KEY"
    signup_url: str
    models: tuple[_Model, ...] = ()
    model_setting: str = ""           # keyring slot for the chosen model
    models_editable: bool = False     # True → user can type a custom model
    hides_other_categories: bool = False  # True → collapse STT+TTS (realtime)
    requires_key: bool = True         # False → key not required (Save not gated)
    hide_key_field: bool = False      # True → no key field at all (pure-local)
    key_hint: str = ""                # custom empty-field placeholder text


@dataclass(frozen=True)
class _ProviderCategory:
    """A row group in the dialog. ``category_key`` is the prefix of
    the provider-selection config (e.g. "LLM" → LLM_PROVIDER setting)."""

    category_key: str           # "LLM", "STT", "TTS"
    label: str                  # "LLM (vision)", etc.
    providers: tuple[_Provider, ...]
    default_index: int


_PROVIDER_CATEGORIES: tuple[_ProviderCategory, ...] = (
    _ProviderCategory(
        category_key="LLM",
        label="LLM (vision)",
        providers=(
            # OpenAI native vision is the default LLM. Direct sk-... key (or an
            # OpenRouter sk-or- key). Model is set via OPENAI_MODEL_VISION.
            _Provider(
                provider_id="openai",
                display_name="OpenAI",
                api_key_env_var="OPENAI_API_KEY",
                signup_url="https://platform.openai.com/api-keys",
                key_hint="OpenAI key (sk-...) or an OpenRouter key (sk-or-)",
            ),
            # T0-1: this provider had no `models` tuple and no `model_setting`,
            # so the model dropdown never rendered, ANTHROPIC_MODEL was never
            # written from the UI, and the (broken) default was the only value
            # ever used. Model ids are the NATIVE dash-versioned form;
            # ai._anthropic_model_for_endpoint adapts them for OpenRouter.
            _Provider(
                provider_id="anthropic",
                display_name="Anthropic",
                api_key_env_var="ANTHROPIC_API_KEY",
                signup_url="https://console.anthropic.com/settings/keys",
                key_hint="Anthropic key (sk-ant-) or an OpenRouter key (sk-or-)",
                models=tuple(
                    _Model(display, model_id)
                    for display, model_id in _ANTHROPIC_MODEL_CHOICES
                ),
                model_setting="ANTHROPIC_MODEL",
                models_editable=True,
            ),
            # GPT-Realtime is intentionally NOT in this dropdown. It's
            # an experimental speech-to-speech path with known audio issues
            # (no transcription / no playback on some setups). Still reachable
            # for advanced use via LLM_PROVIDER=openai-realtime in .env, just not
            # surfaced as a working option.
            # Local Ollama. No API key — instead the "API key" field
            # stores the OLLAMA_HOST URL (default http://localhost:11434).
            # Repurposing the field as a host URL keeps the dialog uniform
            # (single field per provider) without adding a separate "host"
            # input row. Pixel-pointing for local vision models is handled
            # by locator.py's two-stage grid pattern (see ai.OllamaClient).
            _Provider(
                provider_id="ollama",
                display_name="Ollama (local)",
                api_key_env_var="OLLAMA_HOST",
                signup_url="https://ollama.com/download",
                models=tuple(_Model(m, m) for m in _OLLAMA_MODEL_SUGGESTIONS),
                model_setting="OLLAMA_MODEL_VISION",
                models_editable=True,
            ),
            # Google Gemini via OpenRouter. ONE option, no model sub-picker
            # (minimal UX) — defaults to 3.1 Pro (most pixel-accurate Gemini).
            # requires_key=False so Save isn't gated: leave the field BLANK and
            # it reuses your existing OpenRouter (sk-or-) key from the Anthropic
            # slot (see app._resolve_llm_credentials gemini branch). The field is
            # still SHOWN (hide_key_field stays False) so a user who wants a
            # separate OpenRouter key for Gemini can paste one.
            _Provider(
                provider_id="gemini",
                display_name="Google Gemini (via OpenRouter)",
                api_key_env_var="GEMINI_API_KEY",
                signup_url="https://aistudio.google.com/apikey",
                requires_key=False,
                key_hint="Google AI Studio key, or an OpenRouter key (sk-or-); blank reuses your OpenRouter key",
            ),
            # T1-1: native SDK path. Distinct provider rather than auto-detection
            # inside the existing one, so the user can see and choose which
            # transport they get — the native path is the only one offering
            # structured geometry, thinking budgets, grounding, and Agentic Vision.
            _Provider(
                provider_id="gemini-native",
                display_name="Google Gemini (native SDK) — recommended",
                api_key_env_var="GEMINI_API_KEY",
                signup_url="https://aistudio.google.com/apikey",
                key_hint="Google AI Studio key (AIza... or AQ...) — required for the native path",
                models=tuple(
                    _Model(display, model_id)
                    for display, model_id in _GEMINI_NATIVE_MODEL_CHOICES
                ),
                model_setting="GEMINI_NATIVE_MODEL",
                models_editable=True,
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="STT",
        label="STT (speech-to-text)",
        providers=(
            _Provider(
                provider_id="assemblyai",
                display_name="AssemblyAI",
                api_key_env_var="ASSEMBLYAI_API_KEY",
                signup_url="https://www.assemblyai.com/dashboard/signup",
            ),
            # Local offline STT (faster-whisper). No API key; model weights
            # download on first use. requires_key=False so Save isn't gated on
            # a credential and the startup modal never forces one.
            _Provider(
                provider_id="faster-whisper",
                display_name="Local (faster-whisper)",
                api_key_env_var="FASTER_WHISPER_LOCAL",
                signup_url="https://github.com/SYSTRAN/faster-whisper",
                requires_key=False,
                hide_key_field=True,  # truly local — no key field at all
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="TTS",
        label="TTS (text-to-speech)",
        providers=(
            _Provider(
                provider_id="cartesia",
                display_name="Cartesia",
                api_key_env_var="CARTESIA_API_KEY",
                signup_url="https://play.cartesia.ai/sign-in",
            ),
            _Provider(
                provider_id="elevenlabs",
                display_name="ElevenLabs",
                api_key_env_var="ELEVENLABS_API_KEY",
                signup_url="https://elevenlabs.io/app/sign-up",
            ),
            # Local offline TTS (Kokoro-82M). No API key; model files download
            # on first use. requires_key=False (see faster-whisper note).
            _Provider(
                provider_id="kokoro",
                display_name="Local (Kokoro)",
                api_key_env_var="KOKORO_LOCAL",
                signup_url="https://github.com/thewh1teagle/kokoro-onnx",
                requires_key=False,
                hide_key_field=True,  # truly local — no key field at all
            ),
        ),
        default_index=0,
    ),
)


def _mask(value: str | None) -> str:
    """Return a privacy-preserving preview like 'sk-...****abc4' for an
    existing key. Empty input → empty string."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:5]}{'*' * 6}{value[-4:]}"


class SettingsForm(QWidget):
    """Every Nimbus setting, as a plain widget with no host of its own (S-4).

    **Extracted from ``SettingsDialog._build_ui`` as a pure refactor**, so there is one
    settings implementation with two hosts: the first-launch modal (``SettingsDialog``,
    below) and the shell's Settings page (``shell.pages.settings``). The acceptance
    criterion for that extract was that every pre-existing settings test keeps passing
    untouched, which is why the widget-building code here is a move rather than a rewrite --
    it carries the provider/model/key matrix, the OpenRouter key-reuse rule, keyring
    persistence, the hotkey capture widget, the Privacy group, the experimental group and the
    restart labels, and a "nicer" reimplementation would have silently dropped several.

    **This widget deliberately contains no ``QScrollArea`` and no button box.** Both belong
    to the host: the dialog measured 742px of content against 728 usable on a 1366x768
    laptop while being modal at first launch, so the scroll wrapper plus an always-visible
    Save *outside* it is load-bearing (see ``TestSettingsFitsSmallScreens``). Putting a
    scroll area in here as well would nest one inside the other.

    Hosts talk to it through three signals and two accessors:

    * ``sig_validity_changed(bool)`` -- whether Save should be enabled. Replaces the old
      direct poke at ``self._buttons``, which only worked because the dialog owned both.
    * ``sig_local_data_cleared()`` -- the user wiped local data, so the host must close/
      restart. The shell has to *react* to this, not merely record it.
    * ``sig_saved()`` -- a successful persist.
    * ``save() -> bool`` -- ``False`` means nothing was written (invalid hotkey, or the user
      cancelled the Ollama compatibility warning), so the host must not close.
    * ``local_data_cleared`` -- the same boolean ``app.py`` already reads off the dialog.
    """

    sig_validity_changed = pyqtSignal(bool)
    sig_local_data_cleared = pyqtSignal()
    sig_saved = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._dropdowns: dict[str, QComboBox] = {}
        self._key_inputs: dict[str, QLineEdit] = {}
        self._signup_buttons: dict[str, QPushButton] = {}
        # generic per-provider model picker (generalized from the
        # Ollama-only row). One model combo + row per category that has
        # any provider with models (in practice just LLM). The row is shown
        # only when the selected provider has models; the combo is repopulated
        # on provider change.
        self._model_combos: dict[str, QComboBox] = {}
        self._model_rows: dict[str, QWidget] = {}
        # per-category container widgets, so the realtime provider can
        # collapse the STT + TTS rows (it does speech end-to-end).
        self._category_widgets: dict[str, QWidget] = {}
        self._realtime_note: QLabel | None = None
        self._draw_checkbox: QCheckBox | None = None
        self._hotkey_input: QLineEdit | None = None
        self._hotkey_capture: "HotkeyCaptureButton | None" = None  # T2-7
        self._diagnostic_capture_checkbox: QCheckBox | None = None
        self._diagnostic_retention_days: QSpinBox | None = None
        # Experimental toggles, keyed by their keyring setting name.
        self._experimental_checkboxes: dict[str, QCheckBox] = {}
        # T2-1 Privacy Guard widgets, created in _build_privacy_group.
        self._privacy_checkbox: QCheckBox | None = None
        self._privacy_apps_field: QLineEdit | None = None
        self._privacy_titles_field: QLineEdit | None = None
        # SHELL_AND_CHAT.md §10.1 interface settings, created in _build_interface_group.
        self._chat_hud_checkbox: QCheckBox | None = None
        self._chat_autohide_seconds: QSpinBox | None = None
        self._chat_screenshots_checkbox: QCheckBox | None = None
        self._chat_retention_days: QSpinBox | None = None
        self._shell_startup_checkbox: QCheckBox | None = None
        self._nav_side_combo: QComboBox | None = None
        self._reduce_motion_combo: QComboBox | None = None
        self._local_data_cleared = False
        self._build_ui()

    # ---------- UI construction -----------------------------------------

    def _build_ui(self) -> None:
        # Content only. The host supplies the scroll area and the Save button, for the
        # reason in the class docstring: this widget wants ~742px, more than a 1366x768
        # laptop has, so whoever hosts it must keep Save outside the scrolling region.
        outer = QVBoxLayout(self)
        # Sections need air between them. At Qt's default spacing every group, row and panel sat
        # directly against the next one, so the form read as one undifferentiated column -- "one
        # thing right after the other". `SPACE[2]` is the step that separates unrelated blocks
        # elsewhere in the shell; the tighter spacing *inside* each group is what then makes a group
        # read as a group.
        outer.setSpacing(theme.SPACE[2])
        outer.setContentsMargins(
            theme.SPACE[1], theme.SPACE[1], theme.SPACE[1], theme.SPACE[1])

        # Lean privacy framing — one sentence (USER decision,
        # rejected the multi-line splash version as too loud / suspicious).
        # Wording revised from "No server, no telemetry." after
        # USER feedback that "telemetry" is jargon for non-tech users.
        privacy = QLabel(
            "Stored locally, encrypted via Windows Credential Manager. "
            "Nothing leaves your machine."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet(HINT_QSS + "padding-bottom: 4px;")
        outer.addWidget(privacy)

        for category in _PROVIDER_CATEGORIES:
            category_widget = self._build_category_row(category)
            self._category_widgets[category.category_key] = category_widget
            outer.addWidget(category_widget)

        # draw-on-screen teaching mode toggle. Single checkbox, off by
        # default. Persists ANNOTATION_MODE to keyring (config reads it). When
        # on, Nimbus circles/arrows/underlines answers on screen.
        from config import resolve_setting
        # Teaching mode, presented as the feature it is rather than as a line of small print.
        #
        # It was one long checkbox label in a column of other checkboxes -- "Draw on screen —
        # boxes, arrows, highlights and numbered steps (teaching mode)" -- which is the most
        # capable thing in this dialog described in the least prominent way available. The
        # explanation that was hidden in the tooltip is now on screen, where a user deciding
        # whether to turn it on can actually read it.
        self._draw_checkbox = QCheckBox(
            "Teaching mode" + restart_marker_for("ANNOTATION_MODE"))  # T4-7
        self._draw_checkbox.setObjectName("FeatureToggle")
        self._draw_checkbox.setToolTip(
            "Nimbus draws on your screen instead of only moving the pointer.\n\n"
            "Frames a control with a box, dims everything except the area you need,\n"
            "numbers multi-step answers, and draws arrows from a mistake to the fix."
        )
        self._draw_checkbox.setChecked(
            resolve_setting("ANNOTATION_MODE", "off") == "on"
        )

        feature = QFrame()
        feature.setObjectName("FeatureRow")
        feature_layout = QVBoxLayout(feature)
        feature_layout.setContentsMargins(
            theme.SPACE[2], theme.SPACE[2], theme.SPACE[2], theme.SPACE[2])
        feature_layout.setSpacing(2)
        feature_layout.addWidget(self._draw_checkbox)
        feature_blurb = QLabel(
            "Nimbus draws on the screen instead of only moving the pointer \u2014 a box round "
            "the control you need, everything else dimmed, numbered steps for a sequence, and "
            "an arrow from a mistake to the fix.")
        feature_blurb.setWordWrap(True)
        feature_blurb.setObjectName("FeatureBlurb")
        feature_layout.addWidget(feature_blurb)
        outer.addWidget(feature)
        # Extra air after the accent-edged panel specifically. It is the one block on this form with
        # a coloured fill, so the hotkey row sitting flush against it read as belonging to it.
        outer.addSpacing(theme.SPACE[1])

        # Global PTT chord. It is saved separately from provider credentials
        # but uses the same keyring namespace. It takes effect after restart,
        # because the low-level keyboard listener is installed at startup.
        stored_hotkey = resolve_setting("HOTKEY", "ctrl+alt+space")
        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(theme.SPACE[1])
        hotkey_row.addWidget(
            QLabel("Push-to-talk hotkey:" + restart_marker_for("HOTKEY")))  # T4-7
        # T2-7: press-the-chord capture is the primary control. The text field stays as an
        # advanced fallback -- it is scriptable, pasteable, and the only option if a chord
        # cannot physically be pressed on the current keyboard.
        self._hotkey_capture = HotkeyCaptureButton(stored_hotkey)
        self._hotkey_capture.captured.connect(self._on_hotkey_captured)
        hotkey_row.addWidget(self._hotkey_capture, stretch=1)
        self._hotkey_input = QLineEdit()
        self._hotkey_input.setPlaceholderText("Ctrl+Alt+Space")
        self._hotkey_input.setToolTip(
            "Advanced: type the chord instead of pressing it. Modifier + key — "
            "Ctrl/Alt/Shift with Space, Enter, Tab, A-Z, 0-9, or F1-F12. "
            "Takes effect after restart."
        )
        self._hotkey_input.setText(stored_hotkey)
        hotkey_row.addWidget(self._hotkey_input, stretch=1)
        outer.addLayout(hotkey_row)
        # T4-7: one note covering every restart-gated setting, replacing the
        # hotkey-only line. The marker is meaningless without it.
        restart_note = QLabel(RESTART_NOTE)
        restart_note.setWordWrap(True)
        restart_note.setStyleSheet(HINT_QSS + "padding-bottom: 2px;")
        outer.addWidget(restart_note)

        outer.addWidget(self._build_privacy_group())  # T2-1
        outer.addWidget(self._build_interface_group())  # SHELL_AND_CHAT.md §10.1
        outer.addWidget(self._build_experimental_group())

        diagnostic_row = QHBoxLayout()
        self._diagnostic_capture_checkbox = QCheckBox(
            "Save diagnostic screenshots and interaction logs"
        )
        self._diagnostic_capture_checkbox.setChecked(
            resolve_setting("DIAGNOSTIC_CAPTURE", "off") == "on"
        )
        self._diagnostic_capture_checkbox.setToolTip(
            "Off by default. Enable only while troubleshooting; captures may contain sensitive screen content."
        )
        diagnostic_row.addWidget(self._diagnostic_capture_checkbox, stretch=1)
        diagnostic_row.addWidget(QLabel("Keep for:"))
        self._diagnostic_retention_days = QSpinBox()
        self._diagnostic_retention_days.setRange(1, 365)
        self._diagnostic_retention_days.setSuffix(" days")
        try:
            saved_retention = int(resolve_setting("DIAGNOSTIC_RETENTION_DAYS", "7"))
        except ValueError:
            saved_retention = 7
        self._diagnostic_retention_days.setValue(max(1, min(365, saved_retention)))
        diagnostic_row.addWidget(self._diagnostic_retention_days)
        outer.addLayout(diagnostic_row)
        self._diagnostic_capture_checkbox.toggled.connect(
            self._diagnostic_retention_days.setEnabled
        )
        self._diagnostic_retention_days.setEnabled(
            self._diagnostic_capture_checkbox.isChecked()
        )

        # T3-2: the knowledge base is the most powerful feature nobody discovers, because it
        # lives in a folder with a non-guessable naming convention. A button that opens it
        # (with the guide already seeded inside) is the cheapest possible fix.
        kb_button = QPushButton("Open knowledge base folder…")
        kb_button.setToolTip(
            "Teach Nimbus about software it does not know — an in-house tool, a\n"
            "company workflow, a plugin with no public docs.\n\n"
            "Drop a Markdown file named after the program (for example\n"
            "orionflow.exe.md), or a folder of the same name holding .md, .txt,\n"
            ".pdf and .docx files.\n\n"
            "The folder contains a README explaining the naming and formats.\n"
            "Picked up on your next question — no restart needed."
        )
        kb_button.clicked.connect(self._on_open_kb_folder)
        outer.addWidget(kb_button)

        clear_button = QPushButton("Clear all Nimbus local data…")
        clear_button.setToolTip(
            "Deletes Nimbus memory, diagnostics, Knowledge Folder contents, and saved Nimbus settings/API keys."
        )
        clear_button.clicked.connect(self._on_clear_local_data)
        outer.addWidget(clear_button)

        self._reveal = QCheckBox("Show keys in plain text (paste-verify)")
        self._reveal.toggled.connect(self._on_reveal_toggled)
        outer.addWidget(self._reveal)

        # Apply the initial realtime collapse (if LLM provider is realtime).
        self._apply_realtime_collapse()
        self._update_save_enabled()

    # ---------- host contract -------------------------------------------

    def is_valid(self) -> bool:
        """Whether every required key field is filled, i.e. whether Save should be enabled.

        A method rather than only a signal so a host can set its button's initial state
        without waiting for a change it may already have missed during construction.
        """
        collapsed = self._collapsed_categories()
        return all(
            key_input.text().strip()
            for key, key_input in self._key_inputs.items()
            if key not in collapsed and self._selected_requires_key(key)
        )

    @property
    def local_data_cleared(self) -> bool:
        """True once the user has wiped local data and Nimbus must be restarted."""
        return self._local_data_cleared

    def _on_hotkey_captured(self, chord: str) -> None:
        """Mirror a captured chord into the text field (T2-7).

        The text field remains the single value the save path reads, so the two controls
        cannot disagree about what will be persisted -- and the user can see, in Nimbus's own
        spelling, exactly what their key press was understood as.
        """
        if self._hotkey_input is not None:
            self._hotkey_input.setText(chord)

    def _build_privacy_group(self) -> QWidget:
        """Build the Privacy Guard group (T2-1).

        Deliberately NOT inside the collapsed experimental group and not off by default.
        This one is on, visible, and expanded, because it is the setting that makes the
        dialog's "Nothing leaves your machine" line honest about screen contents rather
        than only about credentials.
        """
        from config import resolve_setting

        group = QGroupBox("Privacy")
        layout = QVBoxLayout(group)

        intro = QLabel(
            "Nimbus sends a screenshot to your chosen model on every question. "
            "Your API keys never leave this machine, but screen contents do when you "
            "use a cloud provider."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(HINT_QSS + "padding-bottom: 4px;")
        layout.addWidget(intro)

        self._privacy_checkbox = QCheckBox(
            "Skip the screenshot in sensitive windows (recommended)"
            + restart_marker_for("PRIVACY_GUARD"))  # T4-7
        self._privacy_checkbox.setToolTip(
            "When the window in front looks sensitive, Nimbus answers your question\n"
            "without taking a screenshot, and tells you it did so.\n\n"
            "Covers password managers, sign-in and two-factor pages, banking and\n"
            "checkout pages, private browsing windows, and files that usually hold\n"
            "secrets (.env, id_rsa, secrets.yaml, .pem).\n\n"
            "Cost: for those windows Nimbus is answering blind, so it cannot point at\n"
            "anything or describe what is on screen. Everywhere else it behaves exactly\n"
            "as before.\n\n"
            "If foreground detection fails, Nimbus captures as normal rather than\n"
            "silently stopping — blocking is based on positively recognising a\n"
            "sensitive window, never on uncertainty."
        )
        self._privacy_checkbox.setChecked(resolve_setting("PRIVACY_GUARD", "on") == "on")
        layout.addWidget(self._privacy_checkbox)

        layout.addWidget(QLabel("Also skip these programs (comma-separated .exe names):"))
        self._privacy_apps_field = QLineEdit()
        self._privacy_apps_field.setPlaceholderText("MyVault.exe, AnotherApp.exe")
        self._privacy_apps_field.setToolTip(
            "Added to the built-in list, never replacing it, so you cannot lose the\n"
            "default password-manager coverage by adding one program of your own."
        )
        self._privacy_apps_field.setText(resolve_setting("PRIVACY_GUARD_APPS", ""))
        layout.addWidget(self._privacy_apps_field)

        layout.addWidget(QLabel("Also skip windows whose title matches (comma-separated):"))
        self._privacy_titles_field = QLineEdit()
        self._privacy_titles_field.setPlaceholderText("salary, medical record")
        self._privacy_titles_field.setToolTip(
            "Matched anywhere in the window title, ignoring case. Regular expressions\n"
            "work. An invalid pattern is ignored rather than breaking Nimbus."
        )
        self._privacy_titles_field.setText(resolve_setting("PRIVACY_GUARD_TITLES", ""))
        layout.addWidget(self._privacy_titles_field)

        return group

    def _build_interface_group(self) -> QWidget:
        """Build the Interface group: the chat HUD and the window (SHELL_AND_CHAT.md §10.1).

        Visible and expanded rather than tucked into the experimental group, because none of
        these is experimental -- they are ordinary preferences about how Nimbus presents
        itself, and two of them (the HUD, the window at startup) are the first things a user
        will want to change.

        ``CHAT_STORE_SCREENSHOTS`` sits here rather than in Privacy on purpose: it is a
        property of the chat history, and separating it from the thing it describes would make
        it harder to find, not safer. Its own label carries the warning.
        """
        from config import resolve_setting

        group = QGroupBox("Interface")
        layout = QVBoxLayout(group)

        intro = QLabel(
            "The chat panel shows the conversation as it happens; the Nimbus window is where "
            "everything configurable lives. Both are optional — the hotkey works either way."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(HINT_QSS + "padding-bottom: 4px;")
        layout.addWidget(intro)

        self._chat_hud_checkbox = QCheckBox(
            "Show the chat panel during a conversation"
            + restart_marker_for("CHAT_HUD"))
        self._chat_hud_checkbox.setToolTip(
            "A small floating panel showing what you asked and what Nimbus answered.\n\n"
            "It is hidden from screen capture, so it never appears in the screenshot sent\n"
            "to the model — Nimbus cannot end up describing or pointing at its own panel.\n\n"
            "Turning this off also stops Nimbus recording chat history."
        )
        self._chat_hud_checkbox.setChecked(resolve_setting("CHAT_HUD", "on") == "on")
        layout.addWidget(self._chat_hud_checkbox)

        autohide_row = QHBoxLayout()
        autohide_row.addWidget(QLabel("Hide the panel after:"
                                      + restart_marker_for("CHAT_HUD_AUTOHIDE_SECONDS")))
        self._chat_autohide_seconds = QSpinBox()
        self._chat_autohide_seconds.setRange(0, 3600)
        self._chat_autohide_seconds.setSuffix(" seconds")
        self._chat_autohide_seconds.setSpecialValueText("never")
        self._chat_autohide_seconds.setToolTip(
            "How long the panel stays after the last thing said. Set it to 0 for 'never',\n"
            "which keeps the conversation on screen until you close it yourself."
        )
        try:
            autohide = int(resolve_setting("CHAT_HUD_AUTOHIDE_SECONDS", "45"))
        except ValueError:
            autohide = 45
        self._chat_autohide_seconds.setValue(max(0, min(3600, autohide)))
        autohide_row.addWidget(self._chat_autohide_seconds)
        autohide_row.addStretch(1)
        layout.addLayout(autohide_row)

        retention_row = QHBoxLayout()
        self._chat_screenshots_checkbox = QCheckBox(
            "Also keep the screenshot with each saved message"
            + restart_marker_for("CHAT_STORE_SCREENSHOTS"))
        self._chat_screenshots_checkbox.setToolTip(
            "OFF by default, deliberately. A transcript is text; a screenshot can hold a\n"
            "password field, a client's data or a private message, and keeping those on\n"
            "disk is a bigger commitment than keeping the words.\n\n"
            "Enable it only if you want to look back at what was on screen. Images are\n"
            "removed on the same schedule as the transcript."
        )
        self._chat_screenshots_checkbox.setChecked(
            resolve_setting("CHAT_STORE_SCREENSHOTS", "off") == "on"
        )
        retention_row.addWidget(self._chat_screenshots_checkbox, stretch=1)
        retention_row.addWidget(QLabel("Keep chats for:"
                                       + restart_marker_for("CHAT_RETENTION_DAYS")))
        self._chat_retention_days = QSpinBox()
        self._chat_retention_days.setRange(1, 365)
        self._chat_retention_days.setSuffix(" days")
        self._chat_retention_days.setToolTip(
            "Older conversations are deleted when Nimbus starts. Matches the diagnostic\n"
            "retention setting so there is only one rule to remember."
        )
        try:
            chat_retention = int(resolve_setting("CHAT_RETENTION_DAYS", "14"))
        except ValueError:
            chat_retention = 14
        self._chat_retention_days.setValue(max(1, min(365, chat_retention)))
        retention_row.addWidget(self._chat_retention_days)
        layout.addLayout(retention_row)

        self._shell_startup_checkbox = QCheckBox(
            "Open the Nimbus window when Nimbus starts"
            + restart_marker_for("SHELL_ON_STARTUP"))
        # Reads through `should_open_on_startup` rather than comparing the raw string to "on", so
        # the checkbox cannot disagree with the code that actually decides. It did: the default
        # moved to "on" and a literal `resolve_setting(..., "off") == "on"` here would have shown
        # the box unticked on a machine whose window opens every time.
        self._shell_startup_checkbox.setToolTip(
            "On by default: launching Nimbus shows the window.\n\n"
            "Turn it off if you add Nimbus to your own Windows startup folder and would\n"
            "rather it began in the tray.\n\n"
            "Either way, left-click the tray icon to open the window, and closing it only\n"
            "hides it — push-to-talk keeps working."
        )
        from shell.window import should_open_on_startup
        self._shell_startup_checkbox.setChecked(should_open_on_startup())
        layout.addWidget(self._shell_startup_checkbox)

        chrome_row = QHBoxLayout()
        chrome_row.addWidget(QLabel("Navigation on the:" + restart_marker_for("NAV_SIDE")))
        self._nav_side_combo = QComboBox()
        for label_text, value in (("Left", "left"), ("Right", "right")):
            self._nav_side_combo.addItem(label_text, value)
        self._nav_side_combo.setToolTip(
            "Which side of the window the navigation rail sits on."
        )
        stored_side = resolve_setting("NAV_SIDE", "left").strip().lower()
        self._nav_side_combo.setCurrentIndex(1 if stored_side == "right" else 0)
        chrome_row.addWidget(self._nav_side_combo)

        chrome_row.addWidget(QLabel("Animation:" + restart_marker_for("REDUCE_MOTION")))
        self._reduce_motion_combo = QComboBox()
        for label_text, value in (
            ("Follow Windows", "auto"), ("Reduce motion", "on"), ("Always animate", "off"),
        ):
            self._reduce_motion_combo.addItem(label_text, value)
        self._reduce_motion_combo.setToolTip(
            "'Follow Windows' honours your system's animation setting, which is the right\n"
            "default — motion sensitivity is real and Windows already knows your preference.\n"
            "The other two override it for Nimbus only."
        )
        stored_motion = resolve_setting("REDUCE_MOTION", "auto").strip().lower()
        motion_index = self._reduce_motion_combo.findData(stored_motion)
        self._reduce_motion_combo.setCurrentIndex(motion_index if motion_index >= 0 else 0)
        chrome_row.addWidget(self._reduce_motion_combo)
        chrome_row.addStretch(1)
        layout.addLayout(chrome_row)

        # The panel's own settings mean nothing with the panel off, and a live spinbox next
        # to a cleared checkbox reads as "this still applies".
        def _sync_hud_children(enabled: bool) -> None:
            for widget in (self._chat_autohide_seconds, self._chat_screenshots_checkbox,
                           self._chat_retention_days):
                if widget is not None:
                    widget.setEnabled(enabled)

        self._chat_hud_checkbox.toggled.connect(_sync_hud_children)
        _sync_hud_children(self._chat_hud_checkbox.isChecked())
        return group

    def _build_experimental_group(self) -> QWidget:
        """Build the 'Experimental' group of opt-in capabilities.

        Every toggle here defaults OFF and is honestly labelled with its trade-off. Grouped rather
        than mixed into the main settings because each one changes cost, latency or reliability, and
        each is unproven enough that surfacing it as a normal setting would imply more confidence
        than is warranted.

        **Not collapsible any more.** It used to be a checkable ``QGroupBox`` whose check state hid
        its own contents, and that was a bad use of the control twice over: a group box's check
        indicator means "this whole group is enabled", not "expanded", so a user could reasonably
        read the unchecked state as *the features being off* -- when in truth the toggles inside had
        their own state and were merely hidden. It also put a checkbox in the heading, which is what
        made this section look like a loose checkbox floating between two panels, and it was the one
        control still drawing the platform's dotted focus rectangle.

        The options are simply listed now. They are still all off by default, which is the part that
        actually protects someone who has not gone looking.
        """
        from config import resolve_setting

        group = QGroupBox("Experimental / developer options")
        group.setToolTip(
            "Opt-in capabilities that are still being evaluated. All default to OFF.\n"
            "Each one trades something — cost, latency, or reliability — so read the\n"
            "tooltip on a toggle before enabling it."
        )

        layout = QVBoxLayout(group)
        layout.setSpacing(theme.SPACE[1])
        intro = QLabel(
            "All off by default. Hover any option to see what it does and what it costs. "
            "Changes take effect the next time Nimbus starts."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(HINT_QSS + "padding-bottom: 4px;")
        layout.addWidget(intro)

        for setting, label, tooltip in _EXPERIMENTAL_TOGGLES:
            checkbox = QCheckBox(label + restart_marker_for(setting))  # T4-7
            checkbox.setToolTip(tooltip)
            checkbox.setChecked(resolve_setting(setting, "off") == "on")
            self._experimental_checkboxes[setting] = checkbox
            layout.addWidget(checkbox)

        return group

    def _build_category_row(self, category: _ProviderCategory) -> QWidget:
        """Build one (label + dropdown + Get-key + key-field) row group."""
        from config import resolve_setting

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 4, 0, 8)

        # T4-7: providers and models are read when the client is built at startup.
        label = QLabel(
            f"<b>{category.label}</b>"
            + restart_marker_for(f"{category.category_key}_PROVIDER")
        )
        v.addWidget(label)

        # Resolve currently-selected provider for this category.
        selected_provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        try:
            selected_index = next(
                i for i, p in enumerate(category.providers)
                if p.provider_id == selected_provider_id
            )
        except StopIteration:
            selected_index = category.default_index

        # Dropdown + Get-key button on one horizontal row.
        h = QHBoxLayout()
        dropdown = QComboBox()
        for provider in category.providers:
            dropdown.addItem(provider.display_name, provider.provider_id)
        dropdown.setCurrentIndex(selected_index)
        dropdown.currentIndexChanged.connect(
            lambda idx, c=category: self._on_provider_changed(c, idx)
        )
        self._dropdowns[category.category_key] = dropdown
        h.addWidget(dropdown, stretch=1)

        signup_button = QPushButton("Get key →")
        signup_button.clicked.connect(
            lambda _checked=False, c=category: self._on_signup_clicked(c)
        )
        self._signup_buttons[category.category_key] = signup_button
        h.addWidget(signup_button)
        v.addLayout(h)

        # API key field.
        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.textChanged.connect(self._update_save_enabled)
        self._key_inputs[category.category_key] = key_input
        v.addWidget(key_input)

        # Pre-populate the key field with masked existing value (if any).
        self._refresh_key_field_for_category(category)

        # generic per-provider model picker row (generalized from the
        # Ollama-only row). Built if ANY provider in this category has
        # models; shown only when the selected provider has models. Populated
        # for the current provider.
        if any(p.models for p in category.providers):
            model_row = self._build_model_row(category)
            v.addWidget(model_row)
            self._model_rows[category.category_key] = model_row
            current_provider = category.providers[selected_index]
            self._populate_model_combo(category, current_provider)
            model_row.setVisible(bool(current_provider.models))

        # realtime note — shown under the LLM row when the realtime
        # provider is selected (it collapses STT+TTS; tell the user why).
        if category.category_key == "LLM":
            note = QLabel(
                "Realtime handles speech end-to-end (lowest latency). "
                "STT and TTS aren't used in this mode."
            )
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {theme.ACCENT}; padding-top: 2px;")
            v.addWidget(note)
            self._realtime_note = note
            current_provider = category.providers[selected_index]
            note.setVisible(current_provider.hides_other_categories)

        return container

    def _build_model_row(self, category: _ProviderCategory) -> QWidget:
        """Build the per-provider 'Model:' combobox row (generalized
        from the Ollama-only row). The combo is (re)populated for the
        selected provider by _populate_model_combo. Stored in _model_combos."""
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 4, 0, 0)
        h.addWidget(QLabel("Model:"))
        combo = QComboBox()
        h.addWidget(combo, stretch=1)
        self._model_combos[category.category_key] = combo
        return container

    def _populate_model_combo(
        self, category: _ProviderCategory, provider: _Provider
    ) -> None:
        """Fill the category's model combo with the provider's models and
        select the stored choice (resolve_setting on provider.model_setting,
        default = the provider's first model). Editable for Ollama (custom)."""
        from config import resolve_setting

        combo = self._model_combos.get(category.category_key)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.setEditable(provider.models_editable)
        for m in provider.models:
            combo.addItem(m.display_name, m.model_id)
        if provider.models and provider.model_setting:
            default_id = provider.models[0].model_id
            stored = resolve_setting(provider.model_setting, default_id)
            idx = combo.findData(stored)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif provider.models_editable:
                combo.addItem(stored, stored)
                combo.setCurrentText(stored)
            else:
                combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _selected_model_id(self, category: _ProviderCategory, provider: _Provider) -> str:
        """The chosen model id for a provider.

        Fixed combos read the selected item's data (the bare id). Editable combos allow a
        typed custom id, so the text is honoured — but **only when it is not one of our own
        display labels.**

        That caveat is a bug fix, not a refinement. The native Gemini provider is editable
        (Google ships new preview names faster than Nimbus releases) and its items are
        labelled for humans: "Gemini 3 Flash (default, fastest)" displaying
        ``gemini-3-flash-preview``. Returning ``currentText()`` unconditionally persisted
        the *label* as the model id, so picking a model in Settings either silently did
        nothing or produced a 404 on the next request. Verified: the keyring held
        ``'Gemini 3 Flash (default, fastest)'``.

        So: match the text against the combo's items first and prefer that item's data.
        Fall through to the raw text only for genuinely custom entries, which is what keeps
        Ollama's "type any local model name" behaviour working.
        """
        combo = self._model_combos.get(category.category_key)
        if combo is None:
            return ""
        if provider.models_editable:
            text = combo.currentText().strip()
            index = combo.findText(text)
            if index >= 0:
                data = combo.itemData(index)
                if data:
                    return str(data)
            return text
        data = combo.currentData()
        return data if data else combo.currentText().strip()

    def _selected_requires_key(self, category_key: str) -> bool:
        """True if the category's currently-selected provider needs an API key.
        Local providers (faster-whisper / kokoro) return False."""
        dropdown = self._dropdowns.get(category_key)
        category = next(
            (c for c in _PROVIDER_CATEGORIES if c.category_key == category_key), None
        )
        if dropdown is None or category is None:
            return True
        return category.providers[dropdown.currentIndex()].requires_key

    def _collapsed_categories(self) -> set[str]:
        """Categories collapsed because the selected LLM provider does speech
        end-to-end (realtime). Returns {"STT","TTS"} or an empty set."""
        llm_dropdown = self._dropdowns.get("LLM")
        if llm_dropdown is None:
            return set()
        llm_cat = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        provider = llm_cat.providers[llm_dropdown.currentIndex()]
        return {"STT", "TTS"} if provider.hides_other_categories else set()

    def _apply_realtime_collapse(self) -> None:
        """Hide/show the STT+TTS rows + the realtime note based on the current
        LLM provider. Called on construction + on LLM provider change."""
        collapsed = self._collapsed_categories()
        for key in ("STT", "TTS"):
            widget = self._category_widgets.get(key)
            if widget is not None:
                widget.setVisible(key not in collapsed)
        if self._realtime_note is not None:
            self._realtime_note.setVisible(bool(collapsed))

    def _cached_openrouter_key(self) -> str:
        """An sk-or- OpenRouter key already saved for any LLM provider slot.
        One OpenRouter key serves all LLM providers, so reuse it (cache +
        reuse) instead of making the user re-enter it per provider."""
        for slot in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            k = keyring.get_password(KEYRING_SERVICE, slot) or ""
            if k.startswith("sk-or-"):
                return k
        return ""

    def _refresh_key_field_for_category(self, category: _ProviderCategory) -> None:
        """Read the keyring slot for the dropdown's currently-selected
        provider, set the key field's text + placeholder accordingly.
        Called on dialog construction AND on dropdown change."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        existing = keyring.get_password(KEYRING_SERVICE, provider.api_key_env_var) or ""
        # cache + reuse: one OpenRouter (sk-or-) key works for every LLM
        # provider. If this provider's own slot is empty but you pasted an
        # OpenRouter key for another LLM provider, reuse it here so you don't
        # re-enter it and Save isn't gated on an empty field. The _API_KEY
        # filter skips Ollama (its field holds OLLAMA_HOST, not a key).
        if (not existing and category.category_key == "LLM"
                and provider.api_key_env_var.endswith("_API_KEY")):
            existing = self._cached_openrouter_key()
        key_input = self._key_inputs[category.category_key]
        key_input.setText(existing)
        if existing:
            placeholder = _mask(existing)
        elif provider.key_hint:
            placeholder = provider.key_hint
        else:
            placeholder = f"paste {provider.api_key_env_var} here"
        key_input.setPlaceholderText(placeholder)
        # Pure-local providers (faster-whisper / kokoro) need no key — hide the
        # key field + Get-key button entirely. Everything else shows the field
        # (Gemini shows an OPTIONAL field: blank reuses the OpenRouter key).
        key_input.setVisible(not provider.hide_key_field)
        self._signup_buttons[category.category_key].setVisible(not provider.hide_key_field)

    # ---------- Slots ----------------------------------------------------

    def _on_provider_changed(self, category: _ProviderCategory, _index: int) -> None:
        """Dropdown changed — swap the key field to the newly-selected
        provider's stored key, repopulate + show/hide the model row, and (for
        the LLM category) collapse STT+TTS when realtime is selected."""
        self._refresh_key_field_for_category(category)
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]

        # Repopulate + show/hide the per-provider model row.
        model_row = self._model_rows.get(category.category_key)
        if model_row is not None:
            self._populate_model_combo(category, provider)
            model_row.setVisible(bool(provider.models))

        # LLM realtime collapse (hide STT+TTS rows + show the note).
        if category.category_key == "LLM":
            self._apply_realtime_collapse()

        self._update_save_enabled()

    def _on_signup_clicked(self, category: _ProviderCategory) -> None:
        """User clicked 'Get key →' — open selected provider's signup URL
        in default browser via QDesktopServices."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        QDesktopServices.openUrl(QUrl(provider.signup_url))

    def _on_reveal_toggled(self, checked: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        for key_input in self._key_inputs.values():
            key_input.setEchoMode(mode)

    def _update_save_enabled(self) -> None:
        """Tell the host whether Save should be enabled.

        Was a direct ``self._buttons.button(Save).setEnabled(...)``, which only worked
        because one object owned both the fields and the button. It is now a signal so the
        dialog's button box and the shell page's Save button can both follow the same rule.

        Fires during construction, before any host has connected -- harmless for a Qt signal,
        and hosts read ``is_valid()`` once after construction to catch up. That replaces the
        old ``hasattr(self, "_buttons")`` guard, which existed for exactly this ordering.

        Defensive ``hasattr`` on ``_key_inputs``: ``textChanged`` fires from inside
        ``_build_category_row`` while the dicts are still filling.
        """
        if not hasattr(self, "_key_inputs"):
            return
        self.sig_validity_changed.emit(self.is_valid())

    def save(self) -> bool:
        """Persist provider selection + currently-selected provider's key
        for each category to keyring.

        Returns False when nothing was written -- an invalid hotkey, or the user cancelling
        the Ollama compatibility warning -- so the host knows not to close.

        if user picks Ollama + a model that needs
        a newer Ollama version than they have, show a non-blocking warning
        BEFORE persisting. User can override and save anyway, or cancel.
        Compatibility check runs against live ``/api/version`` ping — if
        Ollama is unreachable we skip the check entirely (don't conflate
        "Ollama down" with "incompatible model").
        """
        # Validate before any compatibility dialogs or keyring writes: an
        # invalid chord must leave all existing settings untouched.
        hotkey_value = self._hotkey_input.text().strip() if self._hotkey_input else ""
        try:
            from hotkey import parse_hotkey
            hotkey_value = parse_hotkey(hotkey_value).display
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid hotkey", str(exc))
            if self._hotkey_input is not None:
                self._hotkey_input.setFocus()
            return False
        # T2-7: keep the capture button showing the normalised form that was actually
        # saved. Typing "CONTROL+ALT+space" in the advanced field would otherwise leave the
        # button displaying stale text while the keyring holds "ctrl+alt+space".
        if self._hotkey_capture is not None:
            self._hotkey_capture.set_value(hotkey_value)

        # Pre-save compatibility check for Ollama LLM.
        llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        llm_dropdown = self._dropdowns["LLM"]
        llm_provider = llm_category.providers[llm_dropdown.currentIndex()]
        if llm_provider.provider_id == "ollama":
            model = self._selected_model_id(llm_category, llm_provider)
            if model and not self._confirm_ollama_compat(model):
                return False  # user cancelled — abort save, no writes

        for category in _PROVIDER_CATEGORIES:
            dropdown = self._dropdowns[category.category_key]
            provider = category.providers[dropdown.currentIndex()]

            # 1. Persist provider selection (e.g. "TTS_PROVIDER" → "elevenlabs")
            store_setting(
                f"{category.category_key}_PROVIDER",
                provider.provider_id,
            )

            # 2. Persist the API key for the selected provider.
            key_value = self._key_inputs[category.category_key].text().strip()
            if key_value:
                store_setting( provider.api_key_env_var, key_value,
                )

            # 3. : persist the chosen model for providers with a model
            # picker (Anthropic→ANTHROPIC_MODEL, OpenAI→OPENAI_MODEL_VISION,
            # Ollama→OLLAMA_MODEL_VISION). Only the selected provider's model
            # combo is live, so we only persist that one.
            if provider.models and provider.model_setting:
                model_id = self._selected_model_id(category, provider)
                if model_id:
                    store_setting( provider.model_setting, model_id,
                    )

        # Experimental toggles. Written as explicit "on"/"off" strings rather than
        # deleting the key when off, so an intentional OFF is distinguishable from
        # "never configured" and cannot be silently re-defaulted later.
        for setting, checkbox in self._experimental_checkboxes.items():
            store_setting( setting, "on" if checkbox.isChecked() else "off",
            )

        # T2-1 Privacy Guard. Same explicit "on"/"off" rule as above, which matters more
        # here than anywhere else: this setting defaults ON, so treating "absent" as OFF
        # would silently disable a privacy feature the user believes is active.
        if self._privacy_checkbox is not None:
            store_setting(
                "PRIVACY_GUARD",
                "on" if self._privacy_checkbox.isChecked() else "off",
            )
        for field, setting in (
            (self._privacy_apps_field, "PRIVACY_GUARD_APPS"),
            (self._privacy_titles_field, "PRIVACY_GUARD_TITLES"),
        ):
            if field is not None:
                store_setting( setting, field.text().strip())

        # persist the draw-on-screen toggle.
        if self._draw_checkbox is not None:
            store_setting(
                "ANNOTATION_MODE",
                "on" if self._draw_checkbox.isChecked() else "off",
            )
        if self._diagnostic_capture_checkbox is not None:
            store_setting(
                "DIAGNOSTIC_CAPTURE",
                "on" if self._diagnostic_capture_checkbox.isChecked() else "off",
            )
        if self._diagnostic_retention_days is not None:
            store_setting(
                "DIAGNOSTIC_RETENTION_DAYS",
                str(self._diagnostic_retention_days.value()),
            )

        # SHELL_AND_CHAT.md §10.1. Same explicit "on"/"off" rule as everything above, which
        # matters most for CHAT_HUD: it defaults ON, so writing nothing when the user turns it
        # off would silently switch it back on at the next launch.
        for checkbox, setting in (
            (self._chat_hud_checkbox, "CHAT_HUD"),
            (self._chat_screenshots_checkbox, "CHAT_STORE_SCREENSHOTS"),
            (self._shell_startup_checkbox, "SHELL_ON_STARTUP"),
        ):
            if checkbox is not None:
                store_setting( setting, "on" if checkbox.isChecked() else "off",
                )
        for spinbox, setting in (
            (self._chat_autohide_seconds, "CHAT_HUD_AUTOHIDE_SECONDS"),
            (self._chat_retention_days, "CHAT_RETENTION_DAYS"),
        ):
            if spinbox is not None:
                store_setting( setting, str(spinbox.value()))
        for combo, setting in (
            (self._nav_side_combo, "NAV_SIDE"),
            (self._reduce_motion_combo, "REDUCE_MOTION"),
        ):
            if combo is not None:
                store_setting( setting, str(combo.currentData()))

        store_setting( "HOTKEY", hotkey_value)
        self.sig_saved.emit()
        return True

    def _on_open_kb_folder(self) -> None:
        """Open the knowledge-base folder in Explorer, seeding the guide first (T3-2).

        ``ensure_guide`` runs here as well as at startup, so the guide is present even if the
        user deleted it or the folder was created before this version. It only writes when the
        file is absent, so their own edits are safe.

        Failures are reported rather than swallowed: the user explicitly clicked a button and
        deserves to know it did not work. That is the opposite of the startup call, which is
        silent because nobody asked for it.
        """
        try:
            import kb
            from config import KB_DIR

            # Derive the folder to open from where the guide was ACTUALLY written, rather
            # than reading KB_DIR a second time. Two independent reads could disagree --
            # kb.py holds its own `from config import KB_DIR` reference — and then Nimbus
            # would seed one folder and open another.
            guide = kb.ensure_guide()
            folder = guide.parent if guide is not None else Path(KB_DIR)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                QMessageBox.information(
                    self, "Knowledge base",
                    f"Nimbus could not open the folder. It is here:\n\n{folder}",
                )
        except Exception as exc:
            QMessageBox.warning(
                self, "Knowledge base",
                f"Could not open the knowledge base folder.\n\n{exc}",
            )

    def _on_clear_local_data(self) -> None:
        """Confirm then remove Nimbus-owned local state and restart cleanly."""
        from config import KB_DIR, MEMORY_DIR

        answer = QMessageBox.question(
            self,
            "Clear all Nimbus local data?",
            "This permanently deletes Nimbus memories, diagnostics, Knowledge "
            "Folder contents, and saved Nimbus settings/API keys.\n\n"
            "Session-history exports are not deleted. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failures = clear_local_nimbus_data(Path(MEMORY_DIR).parent, Path(KB_DIR))
        self._local_data_cleared = True
        details = "\n\nSome items could not be removed:\n" + "\n".join(failures) if failures else ""
        QMessageBox.information(
            self,
            "Nimbus local data cleared",
            "Nimbus will now close. Reopen it to start with a clean setup."
            + details,
        )
        # The host closes: the dialog accepts (app.py then reads local_data_cleared and
        # restarts), the shell page surfaces a restart prompt. Emitting rather than calling
        # accept() is what makes the wipe path work from both.
        self.sig_local_data_cleared.emit()

    def _confirm_ollama_compat(self, model: str) -> bool:
        """Pre-save Ollama compatibility check.

        Returns True if the save should proceed, False if the user
        cancelled. Pings the user's Ollama server for its version,
        checks against the known mllama-supports-from table. Shows a
        QMessageBox warning ONLY if there's a confirmed incompatibility
        — silent on success or when Ollama is unreachable.
        """
        from config import resolve_setting
        from ollama_health import check_model_compatibility, detect_ollama_version

        host = resolve_setting("OLLAMA_HOST", "http://localhost:11434")
        ollama_version = detect_ollama_version(host)
        warning = check_model_compatibility(model, ollama_version)
        if warning is None:
            return True  # compatible OR can't check — proceed silently

        reply = QMessageBox.warning(
            self,
            "Ollama compatibility warning",
            warning + "\n\nSave anyway?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Save


class SettingsDialog(QDialog):
    """Modal dialog for entering / rotating BYOK API keys.

    Constructor doesn't block — call ``exec()`` to show modally and
    wait for OK/Cancel. Returns ``QDialog.DialogCode.Accepted`` on
    Save, ``QDialog.DialogCode.Rejected`` on Cancel.

    Saved values land in Windows Credential Manager under service
    ``KEYRING_SERVICE`` ("nimbus"), one entry per env-var name.

    **Now a host for ``SettingsForm`` rather than the owner of the widgets (S-4).** It keeps
    three things that are genuinely the dialog's own job and were never the form's:

    1. the ``QScrollArea`` around the content, with the button box **outside** it, so Save
       cannot fall below the fold on a 1366x768 laptop -- the dialog is modal at first launch,
       so an unreachable Save means an unusable app;
    2. ``_size_to_screen``, which opens at the content's natural height clamped to 88% of the
       screen (a scrollable dialog otherwise happily opens at its ~111px minimum);
    3. accept/reject semantics, including the "local data cleared -> restart" path that
       ``app.py`` drives off ``_local_data_cleared``.

    Every ``_``-prefixed widget attribute below is an **alias of the form's own**, not a copy,
    so ``dlg._key_inputs["TTS"].setText(...)`` reaches the live widget exactly as before and
    the 40+ existing tests did not need touching. They are plain attributes rather than
    properties on purpose: ``tests/test_experimental.py`` builds a ``SettingsDialog`` via
    ``__new__`` and *assigns* ``_model_combos`` on it, which a read-only property would break.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nimbus — API Keys")
        self.setModal(True)
        self.setMinimumWidth(520)
        # Use the tray icon as the window icon for visual consistency.
        # Path resolved via __file__ so it works inside both the dev
        # checkout (CWD = repo root) AND the bundled EXE (CWD =
        # wherever the user launched from). Plain "assets/..." would
        # be CWD-relative — broken in the bundled case.
        try:
            # The orange mark via ``brand.py``, so this dialog matches the window it was opened
            # from. It used to load ``nimbus_tray.ico`` directly, which is the old blue artwork.
            import brand

            self.setWindowIcon(brand.window_icon())
        except Exception:
            pass  # icon missing in dev install; not critical

        self._local_data_cleared = False
        self._form = SettingsForm()
        self._form.sig_local_data_cleared.connect(self._on_local_data_cleared)
        self._form.sig_validity_changed.connect(self._set_save_enabled)
        self._alias_form_widgets()

        # The settings content is scrollable, with the Save/Cancel box pinned OUTSIDE it.
        #
        # This dialog grew substantially across Tiers 1-3 -- the Privacy group, the
        # experimental group, the restart note, the hotkey capture row, the knowledge-base
        # button. Measured at 742px collapsed and ~870px with the experimental group open,
        # which does NOT fit a 1366x768 laptop: the Save button would land off-screen with
        # no way to reach it, and the dialog is modal on first launch.
        #
        # Keeping the button box out of the scroll area is the load-bearing part. A fully
        # scrolled dialog can still hide Save below the fold; this way it is always visible
        # regardless of how many settings are added later.
        scroll = QScrollArea()
        scroll.setWidget(self._form)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.addWidget(scroll)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        # Added to the shell, NOT to the scrolling page, so Save is reachable on any screen
        # height no matter how much the settings list grows.
        shell.setContentsMargins(12, 0, 12, 12)
        shell.addWidget(self._buttons)
        self._set_save_enabled(self._form.is_valid())
        self._size_to_screen()

        # Last, so it catches the form's checkboxes and the button box as well. Stops a click
        # leaving the platform style's dotted white focus frame on whatever was clicked, without
        # taking anything off the keyboard -- see ``theme.focus_visible_only``. Save keeps its
        # place in the tab order, which matters here because this dialog is modal at first launch.
        theme.focus_visible_only(self)

    # ---------- form aliases --------------------------------------------

    def _alias_form_widgets(self) -> None:
        """Point the dialog's historical attribute names at the form's live widgets.

        Aliases, not copies: the dicts are the same objects the form mutates, and the scalar
        widgets are bound once during ``SettingsForm._build_ui`` and never rebound. That
        second fact is what makes plain assignment safe here, so if a future change starts
        *replacing* one of these widgets after construction it must update this method too.
        """
        form = self._form
        self._dropdowns = form._dropdowns
        self._key_inputs = form._key_inputs
        self._signup_buttons = form._signup_buttons
        self._model_combos = form._model_combos
        self._model_rows = form._model_rows
        self._category_widgets = form._category_widgets
        self._experimental_checkboxes = form._experimental_checkboxes
        self._realtime_note = form._realtime_note
        self._draw_checkbox = form._draw_checkbox
        self._hotkey_input = form._hotkey_input
        self._hotkey_capture = form._hotkey_capture
        self._diagnostic_capture_checkbox = form._diagnostic_capture_checkbox
        self._diagnostic_retention_days = form._diagnostic_retention_days
        self._privacy_checkbox = form._privacy_checkbox
        self._privacy_apps_field = form._privacy_apps_field
        self._privacy_titles_field = form._privacy_titles_field
        self._reveal = form._reveal
        # _size_to_screen asks the CONTENT how tall it wants to be. A QScrollArea reports its
        # own small sizeHint, not its child's, so sizing from the dialog's layout opens a
        # letterbox.
        self._page = form

    # Delegated so `SettingsDialog._selected_model_id(instance, ...)` keeps working on an
    # instance built with __new__ and a hand-assigned `_model_combos`
    # (tests/test_experimental.py does exactly that to pin a real bug). Sharing the function
    # object rather than reimplementing it means the two can never diverge.
    _selected_model_id = SettingsForm._selected_model_id

    def _on_hotkey_captured(self, chord: str) -> None:
        self._form._on_hotkey_captured(chord)

    def _on_open_kb_folder(self) -> None:
        self._form._on_open_kb_folder()

    def _on_clear_local_data(self) -> None:
        self._form._on_clear_local_data()

    # ---------- dialog behaviour ----------------------------------------

    def _set_save_enabled(self, enabled: bool) -> None:
        button = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        if button is not None:
            button.setEnabled(enabled)

    def _on_local_data_cleared(self) -> None:
        """The wipe path: record it for ``app.py`` and close so Nimbus can restart clean."""
        self._local_data_cleared = True
        self.accept()

    def _on_save(self) -> None:
        """Persist through the form, and only close if it actually wrote something."""
        if self._form.save():
            self.accept()

    def _size_to_screen(self) -> None:
        """Open at the content's natural height, capped to the screen.

        Scrolling alone is not enough. It removes the "Save is off-screen" failure, but a
        scrollable dialog will also happily open at its *minimum* size, which after the
        scroll area is about 111px -- a letterbox nobody can use.

        So: ask for the content height, then clamp to 88% of the available screen so there is
        always visible desktop around the dialog. On a large screen nothing scrolls; on a
        1366x768 laptop it scrolls and every control stays reachable.
        """
        from PyQt6.QtWidgets import QApplication

        content = (
            self._page.sizeHint().height()
            + self._buttons.sizeHint().height()
            + 24  # shell margins
        )
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(max(520, self.width()), content)
            return
        available = screen.availableGeometry().height()
        self.resize(max(520, self.width()), min(content, int(available * 0.88)))


def required_keys_present() -> bool:
    """Probe — does every required-provider's API key resolve?

    "required" = the currently-SELECTED provider per category
    (resolved via resolve_setting on LLM_PROVIDER / STT_PROVIDER /
    TTS_PROVIDER). The probe is what the launcher uses to decide whether
    to show the modal at start.

    special-case for OLLAMA_HOST — it's a config setting with a
    working default (http://localhost:11434), NOT an API key the user
    must provide. If the selected LLM provider is Ollama, this probe
    treats OLLAMA_HOST as always-present (because the default works
    out-of-the-box when Ollama is running locally). Without this
    special-case, picking Ollama in the Settings dropdown would force
    the user back into the first-launch modal forever even though they
    don't need any actual credential.
    """
    from config import resolve_api_key, resolve_setting

    def _selected(category: _ProviderCategory) -> _Provider:
        provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        return next(
            (p for p in category.providers if p.provider_id == provider_id),
            category.providers[category.default_index],
        )

    # if the selected LLM provider does speech end-to-end (realtime),
    # the STT + TTS keys aren't required — realtime never uses them.
    llm_category = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
    realtime = _selected(llm_category).hides_other_categories

    for category in _PROVIDER_CATEGORIES:
        if realtime and category.category_key in ("STT", "TTS"):
            continue
        provider = _selected(category)
        # OLLAMA_HOST is a config knob with a working default, not
        # a credential the user must supply. config.OLLAMA_HOST always
        # resolves to at least "http://localhost:11434" via resolve_setting,
        # so consider it always-present from the launcher's perspective.
        if provider.api_key_env_var == "OLLAMA_HOST":
            continue
        if not provider.requires_key:
            continue  # local provider (faster-whisper / kokoro) — no key needed
        if not resolve_api_key(provider.api_key_env_var):
            return False
    return True
