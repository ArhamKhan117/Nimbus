"""Nimbus orchestrator — wires all 7 building blocks into the PTT loop.

One sequential pipeline worker thread per PTT press, cancel-on-re-press.

Threading rule: only pyqtSignal crosses thread boundaries. Worker thread
NEVER calls overlay methods directly.

Top-to-bottom order (so `python -m app` works):
    1. Module docstring
    2. Imports
    3. Constants + sentence splitter
    4. get_foreground_app() ctypes helper
    5. NimbusApp(QObject) orchestrator class
    6. __main__ block
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import signal
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PIL import Image

import kb
from ai import (
    _NIMBUS_ANNOTATION_SYSTEM_PROMPT,
    _NIMBUS_SYSTEM_PROMPT,
    GeminiClient,
    OllamaClient,
    OpenAIVisionClient,
    create_ai_client,
)
from annotations import parse_annotations
from prompts import addendum_for_app
from debug_log import DebugSession
from locator import locate_via_grid, refine_point_via_crop
from capture import (
    capture_all_screens,
    get_cursor_position,
    list_monitors,
    monitor_containing,
    unscale_model_coords,
)
from config import (
    ANNOTATION_MODE,
    ANTHROPIC_API_KEY,
    CAPTIONS,
    HISTORY_IMAGE_COUNT,
    KNOWLEDGE_JOURNAL,
    ASSEMBLYAI_API_KEY,
    CARTESIA_API_KEY,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL_VISION,
    HOTKEY,
    MODEL_ID,
    OLLAMA_HOST,
    OLLAMA_MODEL_VISION,
    OPENAI_API_KEY,
    OPENAI_MODEL_VISION,
    resolve_api_key,
    resolve_setting,
)
# Note: LLM_PROVIDER is intentionally NOT imported as a module-level
# constant — _resolve_llm_credentials() calls resolve_setting fresh on
# every invocation so any change the user made in the Settings dialog
# (which writes to keyring) is picked up without an app restart. The
# module-level constant would be frozen at import time.
from hotkey import PushToTalkHotkey
from memory import MemoryStore
from overlay import OverlayController
from stt import AssemblyAIStreamingSTT, create_stt_client
from tts import CartesiaSonicTTS


# --- Constants + sentence splitter --------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]\s")

JOURNAL_ENABLED = KNOWLEDGE_JOURNAL.strip().lower() == "on"
"""T3-3. Cached at import, same reasoning as CAPTIONS_ENABLED."""

CAPTIONS_ENABLED = CAPTIONS.strip().lower() == "on"
"""T4-5. Resolved once at import, matching ANNOTATION_MODE's pattern.

Deliberately cached rather than re-resolved per callback: partials arrive many times per
second on a streaming provider, and ``resolve_setting`` writes to the keyring whenever the
value came from the environment. That would put a Credential Manager write on the hottest
path in the app."""

_MAX_HISTORY_EXCHANGES = 10

CHAT_HUD_ENABLED = resolve_setting("CHAT_HUD", default="on").strip().lower() == "on"
"""SHELL_AND_CHAT.md §4. Whether the chat panel **shows itself** when an interaction starts.

**No longer whether it is built.** It used to gate construction, which made the Home switch a lie
in one direction: starting Nimbus with the setting off meant no panel existed, so turning the
switch on could only answer "not until you restart" -- reported, fairly, as the switch being
broken. A hidden panel costs a widget and a SQLite handle, so the panel is now always constructed
and this decides whether it appears on its own. Both directions of the switch then work live, and
``set_chat_visible`` has no restart path left to explain."""

_CAPTURE_SETTLE_FALLBACK = 0.05
"""How long to wait for the compositor when ``DwmFlush`` is unavailable. The old fixed guess."""


def _wait_for_compositor() -> None:
    """Block until the compositor has finished the frame that hides the overlay.

    ## Why this is not `S-9`

    `S-9` was "delete the overlay hide/show cycle", on the premise that capture exclusion makes it
    unnecessary. **Measured, and the premise does not hold.** `SetWindowDisplayAffinity` returns 0 on
    the overlay: its ex-style is `0x080800A8`, which includes `WS_EX_LAYERED`, and
    `chat_hud.py` already records that exclusion fails outright on a layered window. The overlay has
    `WA_TranslucentBackground` because it draws a pointer over arbitrary desktop content, so unlike
    the chat panel it cannot trade transparency for exclusion.

    So the orange pointer is absent from diagnostic screenshots *because of* this cycle, not despite
    it. Deleting it would feed Nimbus its own pointer and reopen the feedback loop Invariant 3 exists
    to prevent. The cycle stays.

    ## What was reclaimable

    Almost all of the cost, as it turns out. It was a hard-coded 50ms sleep -- a guess at how long
    the compositor needs -- and `DwmFlush()` is the primitive that actually answers the question: it
    blocks until the next present completes. Measured over 7 capture cycles each:

        fixed 50ms sleep   median 174.9 ms
        DwmFlush           median 119.8 ms   -> 55 ms per interaction

    And it is exactly as safe, which is the part that had to be proved rather than assumed. Counting
    Nimbus-orange pixels in the grab, with the overlay pointing at the screen:

        overlay visible          413 px
        hidden + 50 ms sleep     332 px  (five runs, identical)
        hidden + DwmFlush        332 px  (five runs, identical)

    332 is Nimbus's own window, legitimately on screen. The overlay contributes 81 px and both waits
    remove all of them.

    Falls back to the old sleep if `DwmFlush` is unavailable. A missing compositor call must cost
    latency, never Invariant 3.
    """
    try:
        if ctypes.windll.dwmapi.DwmFlush() == 0:
            return
    except Exception:
        pass
    threading.Event().wait(_CAPTURE_SETTLE_FALLBACK)


_MAX_RECENT_TURNS = 5
"""How many completed turns Home's Recent table can show. The live list is capped at this; the
durable remainder comes from the session store, which is the same data the chat panel lists."""

_USAGE_WINDOW_DAYS = 7
"""The window for Home's "this week" counters."""

def _documents_dir() -> Path:
    """Return Windows' writable Documents known folder.

    ``Path.home() / 'Documents'`` is wrong on installations where OneDrive
    redirects the Documents known folder. Qt asks Windows for the resolved,
    writable location and keeps the fallback for unusual headless setups.
    """
    resolved = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return Path(resolved) if resolved else Path.home() / "Documents"


SESSION_EXPORT_DIR = _documents_dir()
"""Resolved Windows Documents folder for user-requested Markdown exports."""

SESSION_EXPORT_FALLBACK_DIR = Path(__file__).resolve().parent / "exports"
"""Recoverable export location when Windows blocks the Documents folder."""

_REUSE_THRESHOLD_PX = 150
"""Max cursor movement between press and release for reusing press-time
captures. Raised from 50 → 150 after real-session logs
showed 100-150px cursor hovers were re-capturing unnecessarily.
150px = ~3cm on a 200% DPI laptop display — within 'target hover'
intent, not 'user repositioned intentionally'."""


def flush_sentences(buffer: str) -> tuple[list[str], str]:
    """Split buffer into complete sentences and leftover.

    Returns (list_of_complete_sentences, remaining_buffer).
    Splits on .!? followed by whitespace. The system prompt tells Nimbus
    to avoid abbreviations like 'e.g.' so false splits are rare.
    """
    sentences: list[str] = []
    while (m := _SENTENCE_END_RE.search(buffer)):
        end = m.end()
        sentences.append(buffer[:end].strip())
        buffer = buffer[end:]
    return sentences, buffer


def _history_message_text(message: dict) -> str:
    """Return the human-readable text from one in-memory history message.

    Nimbus stores OpenAI-style content blocks in ``_history``. Exporting only
    text deliberately avoids writing image payloads to the user's Documents
    folder while remaining tolerant of a simple string-shaped test message.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()


def _history_image_from_capture(cursor_capture) -> dict:
    """Downscale a capture and wrap it as a history image block (T2-4).

    Downscaled by ``HISTORY_IMAGE_SCALE`` because a history screenshot only has to support
    *recognition* -- "the blue button you mentioned" -- never fresh grounding. Geometry
    always comes from the current turn's full-resolution capture, so detail spent here buys
    nothing and costs tokens on every subsequent request in the conversation.
    """
    from io import BytesIO

    from ai import history_image_block
    from config import HISTORY_IMAGE_SCALE

    image = cursor_capture.image
    width = max(1, int(image.width * HISTORY_IMAGE_SCALE))
    height = max(1, int(image.height * HISTORY_IMAGE_SCALE))
    buf = BytesIO()
    image.resize((width, height)).save(buf, format="JPEG", quality=75)
    return history_image_block(buf.getvalue())


def _evict_old_history_images(history: list, max_images: int) -> None:
    """Keep at most ``max_images`` image blocks in ``history``, oldest evicted first.

    Mutates in place, and strips images only -- the text of an old turn is preserved, so
    the conversation still reads continuously while only recent screens remain visible.
    That distinction is the point: text is cheap and stays useful, screenshots are
    expensive and go stale as soon as the user moves on.
    """
    seen = 0
    for message in reversed(history):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        kept: list = []
        for block in content:
            is_image = isinstance(block, dict) and block.get("type") == "image"
            if not is_image:
                kept.append(block)
                continue
            if seen < max_images:
                seen += 1
                kept.append(block)
        message["content"] = kept


# --- Grid-locator fallback (Ollama pixel-pointing) --------------------

_DIRECTIONAL_QUERY_WORDS = (
    "where", "click", "show me", "find", "point", "open", "select", "press",
    "navigate", "locate", "tap", "look at", "go to",
)
"""Words/phrases that suggest the user wants Nimbus to point at a UI element.
Grid-locator only fires for queries containing one of these — skips
conceptual asks like 'what is HTML' that don't have a UI target."""


def _looks_directional(query: str) -> bool:
    """True if the query contains a directional word like 'where', 'click', 'show me'.

    Used as a cheap pre-filter before firing the grid-locator (which is 2 extra
    LLM calls — expensive on local Ollama). For conceptual questions Nimbus
    should just answer with TTS and not try to point anywhere.
    """
    if not query:
        return False
    q_lower = query.lower()
    return any(word in q_lower for word in _DIRECTIONAL_QUERY_WORDS)


_CURSOR_REFERENCE_WORDS = (
    "cursor", "mouse", "pointer", "this", "here", "right here", "this one",
)
"""Words that make the captured mouse position useful as a refinement seed."""


def _references_cursor_area(query: str) -> bool:
    """Whether the user is likely referring to the area around their mouse."""
    return bool(query) and any(word in query.lower() for word in _CURSOR_REFERENCE_WORDS)


def _refine_model_coordinate(
    *, ai_client, capture, model_x: int, model_y: int, target: str,
    query: str, dbg=None,
) -> tuple[int, int] | None:
    """Run a high-detail crop verification and return model-space coordinates.

    The first vision pass retains the full-screen context. This second pass is
    deliberately narrow and native-resolution, so it is used only after a
    directional direct point (or when the user explicitly refers to the mouse
    area). Failure is non-destructive: callers keep the first point.
    """
    source = getattr(capture, "source_image", None)
    if not isinstance(source, Image.Image):
        source = getattr(capture, "image", None)
    if not isinstance(source, Image.Image):
        return None

    target_w, target_h = capture.target_width, capture.target_height
    seed_x = round(model_x * source.width / target_w)
    seed_y = round(model_y * source.height / target_h)
    if _references_cursor_area(query):
        cursor = getattr(capture, "cursor_physical", None)
        monitor = capture.monitor
        if cursor is not None:
            cursor_x = cursor[0] - monitor["left"]
            cursor_y = cursor[1] - monitor["top"]
            if 0 <= cursor_x < source.width and 0 <= cursor_y < source.height:
                seed_x, seed_y = cursor_x, cursor_y
                if dbg is not None:
                    dbg.log("GROUNDING: using captured mouse area as verification seed")

    refined = refine_point_via_crop(
        llm_client=ai_client,
        source_image=source,
        seed_x=seed_x,
        seed_y=seed_y,
        target=target or query,
        debug_log=(dbg.log if dbg is not None else None),
    )
    if refined is None:
        return None
    refined_x = max(0, min(target_w - 1, round(refined[0] * target_w / source.width)))
    refined_y = max(0, min(target_h - 1, round(refined[1] * target_h / source.height)))
    return refined_x, refined_y


def _refine_annotations(ai_client, annotations: list, capture, query: str, dbg=None) -> list:
    """Refine up to two Tutor-mode circle/arrow anchors in detail crops.

    A Tutor response already supplies the useful teaching geometry (circle
    radius and arrow start). The verifier replaces only its target anchor:
    circle centre or arrow head. That keeps the explanation's intent while
    making small controls materially easier to hit.
    """
    from annotations import Arrow, Circle

    refined_count = 0
    output: list = []
    for annotation in annotations:
        if refined_count >= 2 or not isinstance(annotation, (Circle, Arrow)):
            output.append(annotation)
            continue
        if isinstance(annotation, Circle):
            candidate = _refine_model_coordinate(
                ai_client=ai_client, capture=capture, model_x=annotation.x,
                model_y=annotation.y, target=annotation.label, query=query, dbg=dbg,
            )
            if candidate is not None:
                annotation = Circle(candidate[0], candidate[1], annotation.r, annotation.label)
                refined_count += 1
        else:
            candidate = _refine_model_coordinate(
                ai_client=ai_client, capture=capture, model_x=annotation.x2,
                model_y=annotation.y2, target=query, query=query, dbg=dbg,
            )
            if candidate is not None:
                annotation = Arrow(annotation.x1, annotation.y1, candidate[0], candidate[1])
                refined_count += 1
        output.append(annotation)
    return output


def _annotations_to_physical(annotations: list, cursor_capture) -> list:
    """Map screenshot-pixel annotation coords -> physical virtual-desktop
    coords using the SAME proven transform the [POINT] cursor uses
    (capture.unscale_model_coords: clamp -> *scale -> +monitor-origin). The
    marker proved this exact transform correct (253,52 -> 569,117 landed
    on the button). No grid-locator — Nimbus is natively accurate at coords;
    GPT-4o/Ollama are selectable and forgiving for big worksheet regions.

    Lengths (circle radius, underline width) only scale by scale_x (they're
    sizes, not positions — no origin, no clamp). Returns NEW annotation objects.

    **T3-5 also fixed a latent bug here:** ``Rect`` was added in T1-2 to carry structured
    ``box_2d`` output, but neither this function nor ``overlay.annotations_to_local``
    learned about it. Any rectangle therefore fell through both dispatches and was silently
    discarded — the ``draw_box`` tool could fire and nothing would ever appear on screen.
    Widths use ``scale_x`` and heights ``scale_y``; after T2-8 those are always equal, but
    writing them correctly means the code stays right if that ever changes.
    """
    from annotations import (
        Arrow, Circle, Highlight, Label, Rect, StepBadge, Underline,
    )

    if not annotations:
        return []

    cap = cursor_capture

    def pt(x: int, y: int) -> tuple[int, int]:
        return unscale_model_coords(
            model_x=x,
            model_y=y,
            scale_x=cap.scale_x,
            scale_y=cap.scale_y,
            monitor_left=cap.monitor["left"],
            monitor_top=cap.monitor["top"],
            target_w=cap.target_width,
            target_h=cap.target_height,
        )

    out: list = []
    for a in annotations:
        if isinstance(a, Circle):
            x, y = pt(a.x, a.y)
            out.append(Circle(x, y, int(a.r * cap.scale_x), a.label))
        elif isinstance(a, Arrow):
            x1, y1 = pt(a.x1, a.y1)
            x2, y2 = pt(a.x2, a.y2)
            out.append(Arrow(x1, y1, x2, y2))
        elif isinstance(a, Underline):
            x, y = pt(a.x, a.y)
            out.append(Underline(x, y, int(a.w * cap.scale_x)))
        elif isinstance(a, Label):
            x, y = pt(a.x, a.y)
            out.append(Label(x, y, a.text))
        # T3-5. Rect/Highlight share position-plus-lengths, so they map identically.
        elif isinstance(a, (Rect, Highlight)):
            x, y = pt(a.x, a.y)
            out.append(type(a)(
                x, y, int(a.w * cap.scale_x), int(a.h * cap.scale_y), a.label,
            ))
        elif isinstance(a, StepBadge):
            x, y = pt(a.x, a.y)
            out.append(StepBadge(x, y, a.n, a.label))
    return out


def _maybe_locate_via_grid(
    *,
    ai_client,
    result,
    cursor_capture,
    query: str,
    dbg=None,
):
    """Grid-locator fallback for weak-vision responses lacking a [POINT:x,y] tag.

    Triggers ONLY if:
        1. ai_client is a weak-at-pixel-coords vision model — OllamaClient
           (local) or OpenAIVisionClient (GPT-4o is weaker than Nimbus at
           raw coordinates, same as Ollama; both lean on the grid-locator)
        2. result.coordinate is None (model didn't emit a usable [POINT:x,y])
        3. query is directional (contains 'where' / 'click' / 'show me' / etc.)

    Returns (phys_x, phys_y) in PHYSICAL virtual-desktop coords (matching the
    output of unscale_model_coords) or None if any condition fails / locator
    can't find a target.

    The output is in physical coords (not logical) so the caller can pass it
    straight to overlay.sig_point_at.emit() — same convention the existing
    Nimbus-coordinate path uses.

    Args:
        ai_client: the active AIClient (OllamaClient / AnthropicClient / etc.)
        result: PointParseResult from stream.final_result() — used to check
            if coordinate is already set
        cursor_capture: capture.LabeledCapture for the primary screen
        query: user's transcript (the question they asked)
        dbg: optional DebugSession for logging the grid-locator outcome
    """
    if not isinstance(ai_client, (OllamaClient, OpenAIVisionClient, GeminiClient)):
        return None
    if result.coordinate is not None:
        return None
    if not _looks_directional(query):
        if dbg is not None:
            dbg.log(
                f"GRID-LOCATOR: skipped (query not directional): {query!r}"
            )
        return None

    # Convert the PIL screenshot to base64 JPEG for the locator
    import io
    import base64
    buf = io.BytesIO()
    cursor_capture.image.save(buf, format="JPEG", quality=85)
    jpeg_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    monitor = cursor_capture.monitor
    # locate_via_grid returns coords pre-divided by dpi_scale. We want
    # PHYSICAL virtual-desktop coords (matching the existing app.py pipeline),
    # so pass dpi_scale=1.0 — locator returns (vx, vy) i.e. physical coords.
    # thread dbg.log into the locator so transport
    # failures (Ollama timeout, image-decode error) are distinguishable from
    # model uncertainty (cell 0 / unparseable reply) in the debug log.
    # Without this, a broken Ollama looked identical to "model said no UI
    # element" — operator couldn't tell whether to debug their Ollama setup
    # or just rephrase the question.
    phys_xy = locate_via_grid(
        llm_client=ai_client,
        screenshot_jpeg_b64=jpeg_b64,
        original_size=(cursor_capture.target_width, cursor_capture.target_height),
        physical_size=(monitor["width"], monitor["height"]),
        physical_origin=(monitor["left"], monitor["top"]),
        dpi_scale=1.0,   # We want PHYSICAL coords; overlay handles logical conversion
        query=query,
        debug_log=(dbg.log if dbg is not None else None),
    )

    if dbg is not None:
        if phys_xy is None:
            dbg.log("GRID-LOCATOR: ran but returned None (LLM unsure or conceptual)")
        else:
            dbg.log(f"GRID-LOCATOR: hit physical=({phys_xy[0]},{phys_xy[1]})")

    return phys_xy


# --- Foreground app detection -------------------------------------------------

def get_foreground_app() -> tuple[str, str]:
    """Return (app_name, window_title) of the foreground window via ctypes.

    app_name is the .exe basename (e.g. 'EXCEL.EXE').
    window_title is the full title bar text.
    Returns ('unknown', '') if detection fails.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ("unknown", "")

    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buf, length + 1)
    window_title = title_buf.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    app_name = "unknown"
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if handle:
        try:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            kernel32.QueryFullProcessImageNameW(
                handle, 0, exe_buf, ctypes.byref(size)
            )
            app_name = os.path.basename(exe_buf.value) or "unknown"
        finally:
            kernel32.CloseHandle(handle)

    return (app_name, window_title)


# --- NimbusApp orchestrator ---------------------------------------------------

class NimbusApp(QObject):
    """Main orchestrator. Owns all services + signals + worker lifecycle."""

    sig_pressed = pyqtSignal()
    sig_released = pyqtSignal()
    sig_hide_overlay = pyqtSignal()
    sig_show_overlay = pyqtSignal()
    sig_point_at = pyqtSignal(int, int, dict)
    sig_record_memory = pyqtSignal(str, str, str, str, list)
    # LISTENING-state signals + audio-level forwarding.
    sig_show_waveform = pyqtSignal(int, int, dict)
    sig_hide_waveform = pyqtSignal()
    sig_audio_level = pyqtSignal(float)
    # THINKING-state spinner: shown between release and
    # Nimbus returning a coordinate, so the user sees feedback during the
    # ~4-7s LLM wait (instead of the cursor just sitting there).
    sig_show_spinner = pyqtSignal(int, int, dict)
    sig_hide_spinner = pyqtSignal()
    # draw-on-screen teaching annotations. Carries a list of
    # annotation dataclasses (PHYSICAL coords) + the target monitor dict.
    sig_show_annotations = pyqtSignal(list, dict)
    # Clear all teaching shapes (fired at the start of each press so stale
    # annotations never survive a no-speech / cancelled / errored turn).
    sig_clear_annotations = pyqtSignal()
    sig_cancel = pyqtSignal()
    """T2-2: Esc pressed while a response was in flight. Fired from the pynput
    listener thread, handled on the Qt main thread."""
    sig_caption = pyqtSignal(str)
    """T4-5: partial transcript text for the live caption.

    MUST be a signal, not a direct call: ``on_partial_transcript`` fires on the AssemblyAI
    WebSocket thread (or the faster-whisper stop path), and touching a QWidget off the Qt
    main thread is the §1.6 invariant that produces intermittent crashes rather than clean
    failures."""
    # Tray callbacks may evolve beyond the Qt main thread. Route session
    # exports through a signal so MemoryStore reads and the file write always
    # happen in this QObject's main-thread slot.
    sig_export_session_history = pyqtSignal()
    sig_show_toast = pyqtSignal(str, str)

    # SHELL_AND_CHAT.md §4. The chat HUD is fed exclusively through signals, because the three
    # producers of content -- the pipeline worker, the pynput listener and the STT WebSocket --
    # are all off the Qt main thread, and none of them may touch a widget.
    sig_chat_message = pyqtSignal(object)
    """A completed ``sessions.ChatMessage``. ``object`` rather than a typed signal so
    ``sessions`` stays a lazy import: the HUD is optional and so is its cost."""
    sig_chat_delta = pyqtSignal(str)
    """Text appended to the open Nimbus turn as the reply streams in."""
    sig_chat_state = pyqtSignal(str)
    """``listening`` | ``thinking`` | ``speaking`` | ``idle``."""

    sig_toggle_chat = pyqtSignal()
    """Ctrl+Alt+H. Show the chat panel, or hide it if it is already up (§4 item 8)."""
    sig_new_chat_requested = pyqtSignal()
    """Ctrl+Alt+N. Start a fresh session, clearing ``_history`` with it (Invariant 7)."""

    sig_listening_changed = pyqtSignal(bool)
    """Push-to-talk state changed (`S-3`). **The one signal that drives all three views** --
    the window's toggle, the tray's Pause item and the tray icon. Emitted only by
    ``set_listening``, which is the only writer of ``hotkey.enabled``, so the three cannot
    disagree: none of them holds its own copy."""

    sig_chat_visible_changed = pyqtSignal(bool)
    """Chat-panel visibility changed. Same single-writer arrangement as ``sig_listening_changed``:
    emitted only by ``set_chat_visible``, and the Home switch is a view of it."""

    sig_licence_gate_required = pyqtSignal()
    """The licence this process was admitted on is gone; ask again now (§5 `S-10`).

    The gate runs once, in ``__main__``, before the hotkey installs. That is the right place for
    it, but it made "Sign out" a promise about the *next* launch: the running process kept its
    microphone, its global hook and its window, and closing the window only hides it, so for most
    people the next launch never came. Emitting this makes the gate re-run in place, which is what
    the button appears to do."""

    sig_show_window = pyqtSignal()
    """Someone asked for the window: a tray click, or a second launch of Nimbus.

    Emitted from a Win32 wait thread, so it must stay a signal -- Qt marshals it onto the main
    thread, and touching a widget from that thread directly would be a crash waiting for a slow
    day."""

    def __init__(
        self,
        ai_client=None,
        stt_client=None,
        tts_client=None,
        memory_store=None,
        overlay_controller=None,
        hotkey_instance=None,
    ) -> None:
        super().__init__()

        # respect LLM_PROVIDER setting (Settings dialog dropdown).
        # _resolve_llm_credentials returns the effective model_id + api_key
        # based on whether the user picked Anthropic or Ollama in Settings.
        # Without this branch the dropdown was cosmetic — see helper docstring.
        if ai_client is None:
            _model_id, _api_key = _resolve_llm_credentials()
            ai_client = create_ai_client(
                model_id=_model_id,
                api_key=_api_key,
                ollama_host=OLLAMA_HOST,
            )
        self._ai = ai_client

        # GPT-Realtime speech-to-speech mode. Selected via
        # LLM_PROVIDER='openai-realtime'. This is a PARALLEL pipeline — when
        # active, _handle_press/_handle_release branch to the realtime session
        # (mic streams to the WS, model speaks back + points) instead of the
        # STT->AI->TTS chain. The realtime session's rough point_at coordinate
        # is refined by the grid-locator (via a GPT-4o client). Everything is
        # fail-safe: a realtime setup failure logs + leaves _realtime None so
        # the app still runs (just without realtime).
        self._realtime = None
        self._realtime_vision = None  # OpenAIVisionClient for grid-locator refinement
        self._realtime_capture = None  # cursor-screen capture for the current turn
        _provider = resolve_setting("LLM_PROVIDER", default=DEFAULT_LLM_PROVIDER)
        # T1-4: Gemini Live is opted into via the experimental GEMINI_LIVE toggle rather
        # than being its own provider, so a user can switch it off and keep every other
        # Gemini setting exactly as it was.
        if (
            _provider == "gemini-native"
            and resolve_setting("GEMINI_LIVE", default="off").strip().lower() == "on"
        ):
            self._setup_gemini_live()
        elif _provider == "openai-realtime":
            self._setup_realtime()
        self._stt = stt_client or AssemblyAIStreamingSTT(
            api_key=ASSEMBLYAI_API_KEY
        )
        self._tts = tts_client or CartesiaSonicTTS(api_key=CARTESIA_API_KEY)
        self._memory = memory_store or MemoryStore()
        self._overlay = overlay_controller
        self._hotkey = hotkey_instance

        self._history: list[dict] = []

        # SHELL_AND_CHAT.md §3/§4. All optional and all injected from main() after
        # QApplication exists -- never constructed here, because NimbusApp is built in tests
        # that have no display and must not open windows or a session database.
        self._hud = None
        self._window = None
        self._sessions = None
        self._session_id = 0

        # Home's three numbers (§3 `S-2`). Kept as timestamps rather than counters so the
        # "this week" window is a filter rather than something that needs resetting, and so a
        # long-running session cannot drift.
        self._recent_turns: list[dict] = []
        self._question_times: list[datetime] = []
        self._privacy_skips: list[datetime] = []

        self._cancel_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._current_app: str = "unknown"
        self._current_title: str = ""

        # Press-time capture state. Shifts capture + memory
        # recall off the release-time critical path — saves ~250ms wall-clock.
        # Release-time pipeline re-captures only if cursor moved >50px
        # (user intentionally repositioned mid-utterance).
        self._press_captures: list | None = None
        self._press_memory: str = ""
        self._press_cursor_pos: tuple[int, int] | None = None
        self._capture_thread: threading.Thread | None = None
        # T0-7: guards the three _press_* fields above. They are WRITTEN by the
        # nimbus-press-capture thread and READ by nimbus-release-capture (and,
        # on the fallback path, by nimbus-pipeline). _release_capture_worker
        # joins the press thread first, which usually orders things — but on
        # join timeout it would otherwise read a half-written result. A Lock is
        # used rather than a Queue refactor because the press fields are part of
        # the tested surface; see _read_press_state().
        self._press_lock = threading.Lock()

        self.sig_pressed.connect(self._handle_press)
        self.sig_released.connect(self._handle_release)
        self.sig_hide_overlay.connect(self._on_hide_overlay)
        self.sig_show_overlay.connect(self._on_show_overlay)
        self.sig_point_at.connect(self._on_point_at)
        self.sig_record_memory.connect(self._on_record_memory)
        self.sig_show_waveform.connect(self._on_show_waveform)
        self.sig_hide_waveform.connect(self._on_hide_waveform)
        self.sig_audio_level.connect(self._on_audio_level)
        self.sig_show_spinner.connect(self._on_show_spinner)
        self.sig_hide_spinner.connect(self._on_hide_spinner)
        self.sig_show_annotations.connect(self._on_show_annotations)
        self.sig_clear_annotations.connect(self._on_clear_annotations)
        self.sig_cancel.connect(self._on_cancel)  # T2-2
        self.sig_caption.connect(self._on_caption)  # T4-5
        self.sig_export_session_history.connect(self._on_export_session_history)
        self.sig_show_toast.connect(self._on_show_toast)
        self.sig_toggle_chat.connect(self._on_toggle_chat)
        self.sig_new_chat_requested.connect(self.start_new_chat)

    def start(self) -> None:
        """Initialize overlay + hotkey and begin listening.

        Hotkey callbacks fire on the pynput listener thread, so they emit
        sig_pressed/sig_released which Qt marshals to _handle_press/_handle_release
        on the main thread. This is the pyqtSignal-only thread crossing rule.
        """
        if self._overlay is None:
            self._overlay = OverlayController()
        if self._hotkey is None:
            self._hotkey = PushToTalkHotkey(
                on_press=lambda: self.sig_pressed.emit(),
                on_release=lambda: self.sig_released.emit(),
                hotkey=HOTKEY,
                # T2-2: Esc aborts an in-flight response. Marshalled to the Qt main
                # thread like every other listener-thread callback -- the slot touches
                # TTS and the overlay, neither of which is safe off the main thread.
                on_cancel=lambda: self.sig_cancel.emit(),
                is_in_flight=self._is_response_in_flight,
                # SHELL_AND_CHAT.md §4 item 8. Both emit rather than act: they fire on the
                # pynput listener thread and both touch widgets, so they cross to the Qt main
                # thread the same way every other listener callback does.
                shortcuts={
                    "h": lambda: self.sig_toggle_chat.emit(),
                    "n": lambda: self.sig_new_chat_requested.emit(),
                },
            )
        # Wire RMS audio-level → Qt-thread-safe signal → overlay waveform.
        # stt's callback runs on the portaudio thread; pyqtSignal marshals
        # to the Qt main thread where _on_audio_level calls overlay.set_audio_level.
        self._stt.on_audio_level(lambda lvl: self.sig_audio_level.emit(lvl))

        self._hotkey.start()
        _log(f"Listening for {HOTKEY}...")

    # --- Push-to-talk power state (SHELL_AND_CHAT.md §3 `S-3`) --------------

    @property
    def is_listening(self) -> bool:
        """Whether push-to-talk is accepting callbacks, read straight from the hotkey.

        A property over ``hotkey.enabled`` rather than a field: this is the source of truth,
        and the moment it is copied the window and the tray can disagree about it.
        """
        return bool(self._hotkey is not None and self._hotkey.enabled)

    def set_listening(self, on: bool) -> None:
        """**The only writer of ``hotkey.enabled``** (`S-3`). Qt main thread.

        Both the window's toggle and the tray's Pause item route here rather than writing the
        state themselves, and the resulting ``sig_listening_changed`` is what updates every
        view. Three views, one writer, one notification -- so a stale checkmark is not
        possible rather than merely unlikely.

        Verified: ``set_enabled`` gates callbacks without uninstalling the keyboard hook
        (``listener.stop()`` is never called), so this is instant and needs no restart, unlike
        the settings marked ``↻``.
        """
        on = bool(on)
        if self._hotkey is not None:
            self._hotkey.set_enabled(on)
        if not on:
            # Pausing must also silence whatever is in flight. Leaving Nimbus talking after
            # the user asked it to stop listening reads as the pause having failed.
            self._tts.stop()
            self.sig_hide_waveform.emit()
            self.sig_hide_spinner.emit()
            self.sig_chat_state.emit("idle")
        _log("Push-to-talk " + ("resumed." if on else "paused."))
        # Emit the state actually achieved, not the state requested. With no hotkey installed
        # nothing changed, and saying otherwise would leave every view lying.
        self.sig_listening_changed.emit(self.is_listening)

    # --- Chat panel visibility, live ---------------------------------------

    @property
    def is_chat_visible(self) -> bool:
        """Whether the chat panel is on screen. Read from the widget, never from a copy.

        Same reasoning as ``is_listening``: three things move this panel -- the Home switch,
        Ctrl+Alt+H, and the 45s auto-hide -- so a cached boolean would go stale the first time the
        panel hid itself while the window was open.
        """
        try:
            return bool(self._hud is not None and self._hud.isVisible())
        except Exception:
            return False

    def set_chat_visible(self, on: bool) -> None:
        """**The only writer of chat-panel visibility.** Qt main thread.

        Answers the complaint directly: the panel reappeared on every question with no way to stop
        it short of restarting with ``CHAT_HUD=off``. Off, the panel hides now *and* stops bringing
        itself back -- ``ChatHud.set_auto_reveal`` is the switch for the second half, and without
        it the panel would return on the very next question and the toggle would look broken.

        The conversation is unaffected either way. Turns still stream into the transcript and still
        land in the session store, so turning the panel back on shows what was said while it was
        away rather than an empty panel. The panel is a view (Invariant 10); hiding a view must not
        change what is recorded.

        Persisted so the choice survives a restart, and applied in memory in the same call so it
        does not need one. ``CHAT_HUD`` is the setting it writes -- the same one Settings shows --
        rather than a second key that could disagree with it.

        **No restart, in either direction.** ``CHAT_HUD`` used to gate whether the panel was
        *built*, so starting with it off left nothing to show and this method could only answer
        "not until Nimbus restarts" -- which is not what a switch does. ``build_chat_hud`` now
        always constructs the panel and the setting only decides whether it reveals itself, so the
        toast below is a genuine failure path (construction raised) rather than a routine one.
        """
        on = bool(on)
        try:
            from config import persist_setting
            persist_setting("CHAT_HUD", "on" if on else "off")
        except Exception as exc:
            # A locked vault costs the user the persistence, not the toggle.
            _log(f"CHAT HUD: preference not saved - {type(exc).__name__}: {exc}")

        if self._hud is not None:
            try:
                self._hud.set_auto_reveal(on)
                if on:
                    if self._hud.collapsed:
                        self._hud.set_collapsed(False)
                    self._hud.reveal()
                else:
                    self._hud.hide()
            except Exception as exc:
                _log(f"CHAT HUD: visibility change failed - {type(exc).__name__}: {exc}")
        elif on:
            # Only reachable when the panel failed to construct, which is logged with a reason by
            # `build_chat_hud`. Saying so is better than a switch that moves and does nothing.
            self.sig_show_toast.emit(
                "The chat panel could not start. See the Nimbus log for the reason.", "error")

        _log("Chat panel " + ("shown." if on else "hidden."))
        # The state achieved, not the state requested -- with no panel built, nothing changed.
        self.sig_chat_visible_changed.emit(self.is_chat_visible)

    # --- Licence actions (SHELL_AND_CHAT.md §5 `S-10`) ----------------------

    def deactivate_device(self) -> None:
        """Release this machine's seat, then tell the user what happened. Qt main thread.

        The local licence is cleared either way -- see ``licensing.deactivate_device``. The two
        outcomes are reported differently because they mean different things to someone who is about
        to reinstall: a released seat is immediately reusable, an unreleased one frees up on the next
        server-side revalidation.
        """
        try:
            import licensing

            released = licensing.deactivate_device()
        except Exception as exc:
            _log(f"LICENCE: deactivation failed - {type(exc).__name__}: {exc}")
            self.sig_show_toast.emit("Nimbus could not deactivate this device.", "error")
            return
        self.sig_show_toast.emit(
            "This device has been deactivated."
            if released else
            "Signed out locally. The seat frees up the next time the licence service is reached.",
            "info")
        self._refresh_window()
        # Same reasoning as ``sign_out_licence``: the local licence is gone either way, so this
        # process must stop running on it now rather than at some later launch that may not come.
        self.sig_licence_gate_required.emit()

    def sign_out_licence(self) -> None:
        """Forget the licence on this machine without touching the seat. Qt main thread.

        Then **ask again immediately**, rather than saying "next time it starts".

        That sentence was true of the code and false in practice. The gate runs once in
        ``__main__``; this process was already past it, kept its licence-gated hotkey and mic, and
        `Invariant 5` means closing the window hides it rather than quitting. So "next time" arrived
        only if the user found Quit in the tray -- and until then Nimbus went on working for someone
        who had just signed out, which is the one thing a sign-out button must not do.
        """
        try:
            import licensing

            licensing.sign_out()
        except Exception as exc:
            _log(f"LICENCE: sign out failed - {type(exc).__name__}: {exc}")
            self.sig_show_toast.emit("Nimbus could not sign out on this device.", "error")
            return
        self._refresh_window()
        self.sig_licence_gate_required.emit()

    def revalidate_licence_async(self) -> None:
        """The silent 7-day check, off the main thread (§5).

        A daemon thread rather than a timer, and it does nothing at all when
        ``should_revalidate`` says the last check was recent -- §5's rule is "do not phone home on
        every launch", because that makes startup depend on our uptime.

        Failure is silent by design: ``licensing.revalidate`` leaves a good cached licence alone on a
        network error, so there is nothing to tell the user about.
        """
        def worker() -> None:
            try:
                import licensing

                if not licensing.should_revalidate():
                    return
                state = licensing.revalidate()
                _log(f"LICENCE: revalidated - {state.detail}")
            except Exception as exc:
                _log(f"LICENCE: revalidation skipped - {type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True, name="nimbus-licence-check").start()

    def _refresh_window(self) -> None:
        """Re-read the shell's injected sources, if a window exists."""
        window = getattr(self, "_window", None)
        refresh = getattr(window, "refresh", None)
        if refresh is not None:
            try:
                refresh()
            except Exception:
                pass

    # --- Home's numbers (SHELL_AND_CHAT.md §3 `S-2`) ------------------------

    def _week_cutoff(self) -> datetime:
        from datetime import timedelta

        return datetime.now() - timedelta(days=_USAGE_WINDOW_DAYS)

    def _durable_count(self, method: str) -> int | None:
        """Ask the session store for a count, or ``None`` when there is no store to ask.

        ``self.__dict__.get`` rather than ``getattr``: several tests build a ``NimbusApp`` through
        ``__new__`` without running ``__init__``, and ``getattr`` on a ``QObject`` in that state
        raises ``RuntimeError: super-class __init__() ... was never called``. Reading the instance
        dict asks the same question without going through Qt's lookup.
        """
        store = self.__dict__.get("_sessions")
        reader = getattr(store, method, None)
        if reader is None:
            return None
        try:
            return int(reader(self._week_cutoff()))
        except Exception as exc:
            _log(f"HOME: {method} unavailable - {type(exc).__name__}: {exc}")
            return None

    def questions_this_week(self) -> int:
        """Completed questions in the last 7 days.

        **Durable**, read from the session store. It used to be an in-memory list, which meant the
        card said "this week" while counting only since the last restart -- a label that is wrong
        most of the time it is read, since Nimbus is a background tool people leave running for a
        day and restart the next.

        Counted from the stored messages rather than a tally of its own, so this and the chat panel's
        transcript cannot disagree about what happened. The in-memory list is kept as the fallback
        for a session with no store (the panel failed to build), where a session-scoped number is
        better than none.
        """
        durable = self._durable_count("count_questions_since")
        if durable is not None:
            return durable
        return len(self._within_window(self._question_times))

    def screenshots_skipped_this_week(self) -> int:
        """Screenshots the Privacy Guard suppressed in the last 7 days.

        The most trust-building number on Home, precisely because it is an observation rather than a
        promise: it counts times Nimbus chose *not* to look at the screen. Which is also why it had
        to become durable -- a trust-building number that quietly resets to zero on every restart
        undermines the thing it is there to build.
        """
        durable = self._durable_count("count_privacy_skips_since")
        if durable is not None:
            return durable
        return len(self._within_window(self._privacy_skips))

    def recent_turns(self) -> list[dict]:
        """The last few completed turns, newest first, for Home's Recent table.

        Built where a turn completes rather than derived from ``_history``: ``_history`` is the
        model's conversation record and carries neither the app name nor a timestamp, and adding
        them there would change what gets sent to the model.

        **Falls back to the session store**, which is the durable record. The in-memory list is
        session-scoped, so Home showed "Nothing yet" after every restart to a user with a week of
        conversations behind them -- reported, correctly, as the table being broken. The store is
        the same data the chat panel lists, so the two surfaces cannot disagree.

        In-memory entries come first and the store fills the rest, rather than one source or the
        other. The live list has the app name and pointer target of the turn that just happened,
        which is more than the store row carries, and a user who has asked something this session
        should see it at the top.
        """
        turns = list(self._recent_turns)
        if len(turns) >= _MAX_RECENT_TURNS:
            return turns[:_MAX_RECENT_TURNS]
        store = self.__dict__.get("_sessions")
        reader = getattr(store, "recent_turns", None)
        if reader is None:
            return turns
        try:
            stored = reader(_MAX_RECENT_TURNS)
        except Exception as exc:
            _log(f"HOME: recent turns unavailable - {type(exc).__name__}: {exc}")
            return turns
        seen = {(entry.get("question") or "").strip() for entry in turns}
        for entry in stored:
            question = (entry.get("question") or "").strip()
            if question in seen:
                # The current session's turns are already persisted, so without this the same
                # question appears twice -- once live, once from the store.
                continue
            seen.add(question)
            turns.append(entry)
            if len(turns) >= _MAX_RECENT_TURNS:
                break
        return turns[:_MAX_RECENT_TURNS]

    @staticmethod
    def _within_window(stamps: list[datetime]) -> list[datetime]:
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=_USAGE_WINDOW_DAYS)
        return [stamp for stamp in stamps if stamp >= cutoff]

    # --- Chat HUD feed (SHELL_AND_CHAT.md §4) -------------------------------

    def _emit_chat_message(self, role: str, text: str, *, coordinate=None,
                           image=None, privacy_skipped: bool = False,
                           error: str = "") -> None:
        """Send one turn to the HUD. Called from the pipeline thread; never raises.

        Returns immediately when no HUD exists -- ``CHAT_HUD`` off, or construction failed --
        so the pipeline pays nothing for a panel that is not there. That is Invariant 10 in
        practice: the HUD is downstream of the answer and must never be able to affect it.

        ``sessions`` is imported here rather than at module scope so a build with the HUD
        disabled never loads it.

        ``privacy_skipped`` must be the *same* boolean the Privacy Guard produced. Passing the
        image with the flag set is safe and deliberate: ``add_message`` treats the flag as a
        hard stop, so a screenshot the Guard refused cannot reach the disk even if a caller
        forgets. Inventing the flag here instead would make Invariant 6 decorative.
        """
        if self._hud is None:
            return
        try:
            from sessions import ChatMessage

            self.sig_chat_message.emit(ChatMessage(
                role=role,
                text=text or "",
                coordinate=coordinate,
                image=image,
                privacy_skipped=bool(privacy_skipped),
                error=error,
            ))
        except Exception as exc:
            _log(f"CHAT HUD: message dropped - {type(exc).__name__}: {exc}")

    # --- Things the HUD and the window ask for (SHELL_AND_CHAT.md §4, §3) ----

    def repoint_at(self, model_x: int, model_y: int) -> None:
        """Fly the cursor to a Space C coordinate from an earlier turn. Qt main thread.

        The stored coordinate is Space C -- the model's declared-resolution space -- not
        physical pixels, and that distinction is the whole reason re-pointing works at all. A
        physical coordinate is only valid for the monitor layout and DPI that produced it, so a
        stored one would send the cursor to the wrong place after the user moved a window,
        changed scaling or docked a laptop.

        So the transform is recomputed against a **fresh** capture of the screen the cursor is
        on now, using the same ``unscale_model_coords`` call the live pipeline uses. Nothing is
        cached, and the capture goes through ``_capture_screens_guarded``, which means the
        Privacy Guard applies here too: re-pointing must not become a way to photograph a
        password manager.
        """
        try:
            captures = self._capture_screens_guarded()
            if not captures:
                self.sig_show_toast.emit(
                    "Nimbus needs to see the screen to point at that again.", "info")
                return
            target = next(
                (c for c in captures if getattr(c, "is_cursor_screen", False)), captures[0])
            phys_x, phys_y = unscale_model_coords(
                model_x=int(model_x),
                model_y=int(model_y),
                scale_x=target.scale_x,
                scale_y=target.scale_y,
                monitor_left=target.monitor["left"],
                monitor_top=target.monitor["top"],
                target_w=target.target_width,
                target_h=target.target_height,
            )
            self.sig_point_at.emit(phys_x, phys_y, target.monitor)
        except Exception as exc:
            _log(f"REPOINT: skipped - {type(exc).__name__}: {exc}")

    def retry_transcript(self, transcript: str) -> None:
        """Re-run the pipeline for ``transcript`` with no recording (§4 `S-6b`).

        The user is saying "you misheard me" or "try again", so the words are taken as given
        and only everything downstream is redone -- a fresh capture, a fresh model call.

        Deliberately refuses while a turn is in flight. Two pipeline workers writing the same
        ``_history`` and moving the same cursor is a race, and the honest answer is to say so.
        """
        transcript = (transcript or "").strip()
        if not transcript:
            return
        if self._is_response_in_flight():
            self.sig_show_toast.emit("Nimbus is still working on the last question.", "info")
            return
        self._cancel_event = threading.Event()
        capture_queue: queue.Queue = queue.Queue(maxsize=1)
        # An empty result makes the worker take its documented press-time fallback path, which
        # captures for itself. That is exactly the behaviour wanted here.
        capture_queue.put((None, "", "retry - no press-time capture"))
        # The recorded transcript replaces STT for this run only. Restored in a finally block
        # inside the shim so a failed retry cannot leave the app permanently deaf.
        self._worker_thread = threading.Thread(
            target=self._retry_worker,
            args=(transcript, capture_queue),
            daemon=True,
            name="nimbus-retry",
        )
        self._worker_thread.start()

    def _retry_worker(self, transcript: str, capture_queue: "queue.Queue") -> None:
        """Run one pipeline pass with ``transcript`` substituted for the microphone.

        Swapping ``stop_recording`` rather than adding a parameter to ``_pipeline_worker``
        keeps the pipeline itself untouched -- §0.2's rule is that the interaction path is not
        rewritten for the sake of the UI, and a retry is not a new kind of interaction.
        """
        original = self._stt.stop_recording
        try:
            self._stt.stop_recording = lambda: transcript  # type: ignore[method-assign]
            self._pipeline_worker(
                self._current_app, self._current_title, self._cancel_event, capture_queue)
        except Exception as exc:
            _log(f"RETRY: failed - {type(exc).__name__}: {exc}")
        finally:
            self._stt.stop_recording = original  # type: ignore[method-assign]

    def _on_toggle_chat(self) -> None:
        """Ctrl+Alt+H: show the chat panel, or hide it if it is already up. Qt main thread.

        A toggle rather than a show, because the panel is always-on-top over whatever the user is
        working in -- a shortcut that could only summon it and never dismiss it would be a
        shortcut people learn not to press.

        Also un-collapses. A collapsed panel is technically visible, so a plain visibility toggle
        would "show" a bar with no transcript and look like it had done nothing.
        """
        if self._hud is None:
            return
        try:
            if self._hud.isVisible() and not self._hud.collapsed:
                self._hud.hide()
                return
            if self._hud.collapsed:
                self._hud.set_collapsed(False)
            self._hud.show()
            self._hud.note_activity()
        except Exception as exc:
            _log(f"CHAT HUD: toggle failed - {type(exc).__name__}: {exc}")

    def start_new_chat(self) -> None:
        """Begin a fresh session, clearing ``_history`` with it (Invariant 7).

        The clear is not optional and not separate: a "new chat" that starts a fresh visual
        thread while still sending the model the last ten exchanges is a lie.
        ``sessions.start_new_session`` does both in one call precisely so a caller cannot do
        half of it, which is why this method delegates rather than reimplementing.
        """
        if self._sessions is None:
            return
        try:
            import sessions

            self._session_id = sessions.start_new_session(
                self._sessions, self._current_app, self._history)
            if self._hud is not None:
                self._hud.set_session(self._session_id, "")
        except Exception as exc:
            _log(f"CHAT: new session failed - {type(exc).__name__}: {exc}")

    def open_chat_session(self, session_id: int) -> None:
        """Switch to a stored session and rebuild ``_history`` from it (Invariant 7)."""
        if self._sessions is None:
            return
        try:
            import sessions

            sessions.switch_session(
                self._sessions, int(session_id), self._history,
                max_exchanges=_MAX_HISTORY_EXCHANGES, image_count=HISTORY_IMAGE_COUNT)
            self._session_id = int(session_id)
            if self._hud is not None:
                self._hud.set_session(self._session_id, "")
        except Exception as exc:
            _log(f"CHAT: session switch failed - {type(exc).__name__}: {exc}")

    def start_review(self) -> None:
        """Run the spoken "quiz me" review from the window's Journal page (T3-3).

        Routed through the existing local journal intent rather than a second implementation,
        so the button and the spoken command can never answer differently.
        """
        if not JOURNAL_ENABLED:
            self.sig_show_toast.emit(
                "The knowledge journal is switched off in Settings.", "info")
            return
        try:
            reply = self._handle_journal_intent("quiz me", self._current_app)
            if not reply:
                return
            self._emit_chat_message("nimbus", reply)
            self._tts.speak(reply)
        except Exception as exc:
            _log(f"REVIEW: could not start - {type(exc).__name__}: {exc}")

    def open_memory_folder(self) -> None:
        """Open the per-app memory folder in Explorer. Inherited from the tray (`S-5`)."""
        from config import MEMORY_DIR

        try:
            Path(MEMORY_DIR).mkdir(parents=True, exist_ok=True)
            os.startfile(str(MEMORY_DIR))
        except OSError as exc:
            _log(f"MEMORY: could not open folder - {exc}")
            self.sig_show_toast.emit("Nimbus could not open the memory folder.", "error")

    def _record_turn(self, app_name: str, transcript: str, spoken_text: str,
                     target: str = "") -> None:
        """Note a completed turn for Home. Never raises: a status card is not worth a turn.

        Called from the pipeline worker thread, and deliberately only touches plain Python
        lists -- no widget, no signal, no database. The window reads them through injected
        callables on the main thread when it refreshes.
        """
        try:
            now = datetime.now()
            self._question_times.append(now)
            self._question_times = self._within_window(self._question_times)
            self._recent_turns.insert(0, {
                "question": (transcript or "").strip(),
                "app": app_name or "",
                "when": now,
                "target": target or "",
                "answer": (spoken_text or "").strip(),
            })
            del self._recent_turns[_MAX_RECENT_TURNS:]
        except Exception as exc:
            _log(f"HOME: turn not recorded - {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        """Clean shutdown of all services."""
        if self._hotkey:
            self._hotkey.stop()
        self._cancel_event.set()
        self._tts.stop()
        self._stt.disconnect()
        if self._realtime is not None:
            try:
                self._realtime.close()
            except Exception:
                pass
        # T1-6a: release billed KB caches. Leaking them past exit costs the user money
        # for storage they cannot use. Optional method, so other providers are unaffected.
        closer = getattr(self._ai, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
        # SHELL_AND_CHAT.md §3/§4. Hidden rather than left for interpreter teardown: a
        # top-level Qt window still on screen while the pipeline is being dismantled looks
        # like a hang, and the HUD is always-on-top so it would be the last thing visible.
        for surface in (self._hud, self._window):
            if surface is None:
                continue
            try:
                surface.hide()
            except Exception:
                pass
        _log("Shutdown complete.")

    # --- Hotkey handlers (called on Qt main thread via pyqtSignal) ---

    # --- GPT-Realtime (parallel pipeline) ---------------------------

    def _setup_realtime(self) -> None:
        """Build + connect the GPT-Realtime session. Fail-safe: any error
        leaves self._realtime None and logs, so the app still runs."""
        try:
            from realtime import RealtimeSession
            from ai import OpenAIVisionClient
            key = OPENAI_API_KEY or ""
            # Accurate vision client for the realtime PIXEL pass — gpt-5.4 by
            # default (pixel-accurate grounding, no grid-locator needed). Uses
            # the same OPENAI_MODEL_VISION the standard OpenAI path uses.
            self._realtime_vision = OpenAIVisionClient(
                api_key=key, model_id=f"openai/{OPENAI_MODEL_VISION}",
            )
            self._realtime = RealtimeSession(
                api_key=key,
                on_coordinate=self._realtime_on_coordinate,
                on_audio_start=lambda: self.sig_hide_spinner.emit(),
            )
            self._realtime.connect()
            _log("REALTIME: session connected (gpt-realtime speech-to-speech mode)")
        except Exception as exc:
            self._realtime = None
            _log(f"REALTIME: setup failed, falling back to normal pipeline — {exc}")

    def _setup_gemini_live(self) -> None:
        """Build + connect a Gemini Live session (T1-4). Fail-safe.

        Any error leaves ``self._realtime`` as None and logs, so Nimbus falls back to the
        normal push-to-talk pipeline rather than starting broken. That fail-safe is why
        this is safe to expose as an experimental toggle at all.
        """
        model = ""
        try:
            from ai import vertex_settings
            from config import resolve_setting
            from gemini_live import DEFAULT_LIVE_MODEL, GeminiLiveSession

            model = resolve_setting(
                "GEMINI_LIVE_MODEL", default=DEFAULT_LIVE_MODEL
            ).strip() or DEFAULT_LIVE_MODEL
            vertex_project, vertex_location = vertex_settings()
            self._realtime = GeminiLiveSession(
                api_key=GEMINI_API_KEY or "",
                model=model,
                vertex_project=vertex_project,
                vertex_location=vertex_location,
                on_coordinate=self._gemini_live_on_coordinate,
                on_audio_start=lambda: self.sig_hide_spinner.emit(),
            )
            self._realtime.connect()
            _log(f"GEMINI LIVE: session connected (speech-to-speech) model={model}")
        except Exception as exc:
            self._realtime = None
            # Name the model in the failure. The Live API serves a SEPARATE, much smaller
            # model set than generateContent, so "model not found" here almost always
            # means a normal chat model was configured rather than a Live one — and that
            # is impossible to diagnose from the raw SDK error alone.
            _log(
                f"GEMINI LIVE: setup failed, using normal pipeline "
                f"(model={model or 'unresolved'}) - {exc}"
            )
            if "bidiGenerateContent" in str(exc) or "not found" in str(exc):
                _log(
                    "GEMINI LIVE: that model does not serve the Live protocol. Set "
                    "GEMINI_LIVE_MODEL to a Live-capable model "
                    "(e.g. gemini-3.1-flash-live-preview)."
                )

    def _gemini_live_on_coordinate(self, norm_y: int, norm_x: int, label: str) -> None:
        """Live session emitted point_at with NORMALISED 0-1000 coordinates (T1-4).

        Conversion happens here, not in the session, because only the app knows the
        capture dimensions for this turn. Runs on the Live receive thread, so the overlay
        is reached exclusively through a signal.
        """
        cap = self._realtime_capture
        if cap is None:
            return
        try:
            from ai import normalised_point_to_space_c

            model_x, model_y = normalised_point_to_space_c(
                norm_y=norm_y, norm_x=norm_x,
                target_w=cap.target_width, target_h=cap.target_height,
            )
            phys = unscale_model_coords(
                model_x=model_x, model_y=model_y,
                scale_x=cap.scale_x, scale_y=cap.scale_y,
                monitor_left=cap.monitor["left"], monitor_top=cap.monitor["top"],
                target_w=cap.target_width, target_h=cap.target_height,
            )
            self.sig_point_at.emit(phys[0], phys[1], cap.monitor)
            _log(f"GEMINI LIVE: pointed at {phys} (label={label!r})")
        except Exception as exc:
            _log(f"GEMINI LIVE: coordinate transform failed - {exc}")

    def _realtime_on_coordinate(self, x: int, y: int, label: str) -> None:
        """Realtime called point_at(x,y,label) — the user wants visual help.
        Runs on the realtime recv thread. We DISCARD the model's rough (x,y)
        (realtime is weak at pixels) and do an ACCURATE vision pass (gpt-5.4)
        on the screenshot instead: when draw mode is on, render shapes;
        otherwise point the cursor. Both via gpt-5.4 — drops the old gpt-4o
        grid-locator entirely."""
        cap = self._realtime_capture
        if cap is None:
            return
        try:
            if ANNOTATION_MODE == "on":
                shapes = self._realtime_render_annotations(
                    label, cap, self._realtime_vision
                )
                if shapes:
                    self.sig_show_annotations.emit(shapes, cap.monitor)
                    _log(f"REALTIME: drew {len(shapes)} shapes (label={label!r})")
                    return
            phys = self._realtime_locate_point(label, cap, self._realtime_vision)
            if phys is not None:
                self.sig_point_at.emit(phys[0], phys[1], cap.monitor)
                _log(f"REALTIME: pointed at {phys} (label={label!r})")
        except Exception as exc:
            _log(f"REALTIME: vision pass failed — {exc}")

    def _realtime_render_annotations(self, label, cap, vision_client) -> list:
        """Accurate annotation vision pass for realtime draw mode: send the
        screenshot + the annotation system prompt to gpt-5.4, parse the shape
        tags, map to physical coords. Returns physical shapes (possibly empty).

        Uses ask_stream (not ask) because only ask_stream accepts a
        system_prompt override. The spoken text is discarded — GPT-Realtime
        already speaks; this pass is pixels-only."""
        img_label = (
            f"primary focus (image dimensions: "
            f"{cap.target_width}x{cap.target_height} pixels)"
        )
        query = (
            f"circle or point at: {label}" if label
            else "point at what the user just asked about"
        )
        with vision_client.ask_stream(
            images=[(cap.image, img_label)],
            transcript=query,
            history=[],
            system_prompt=_NIMBUS_ANNOTATION_SYSTEM_PROMPT,
        ) as stream:
            for _ in stream.text_deltas():
                pass
            result = stream.final_result()
        _, anns = parse_annotations(result.spoken_text)
        anns = _refine_annotations(vision_client, anns, cap, query)
        return _annotations_to_physical(anns, cap)

    def _realtime_locate_point(self, label, cap, vision_client):
        """Accurate single-point vision pass for realtime cursor mode — gpt-5.4
        returns a precise [POINT] directly. Returns physical (x,y) or None."""
        query = (
            f"where is: {label}" if label
            else "where is what the user just asked about"
        )
        result = vision_client.ask(
            cap.image, query, [], cap.target_width, cap.target_height,
        )
        points = result.get("points") or []
        if not points:
            return None
        p = points[0]
        refined = _refine_model_coordinate(
            ai_client=vision_client,
            capture=cap,
            model_x=p["x"],
            model_y=p["y"],
            target=p.get("label", label),
            query=query,
        )
        if refined is not None:
            p = {**p, "x": refined[0], "y": refined[1]}
        return unscale_model_coords(
            model_x=p["x"],
            model_y=p["y"],
            scale_x=cap.scale_x,
            scale_y=cap.scale_y,
            monitor_left=cap.monitor["left"],
            monitor_top=cap.monitor["top"],
            target_w=cap.target_width,
            target_h=cap.target_height,
        )

    def _handle_press(self) -> None:
        """Hotkey pressed: kill TTS + start recording + capture foreground app."""
        import time
        if self._hotkey is not None and not getattr(self._hotkey, "enabled", True):
            return
        # GPT-Realtime mode takes a separate path — stream mic to the
        # realtime WS instead of STT. The normal STT/AI/TTS chain is skipped.
        if self._realtime is not None:
            _log("PRESS handler START (realtime mode)")
            try:
                self._realtime.stop()  # cancel any in-flight response
                self._realtime.start_turn()
                cursor_x, cursor_y = get_cursor_position()
                # T0-7: guarded write. No press-capture thread runs in realtime
                # mode, but use the same accessor so every writer is consistent.
                self._write_press_state(None, "", (cursor_x, cursor_y))
                mon = monitor_containing(cursor_x, cursor_y, list_monitors())
                if mon is not None:
                    self.sig_show_waveform.emit(cursor_x, cursor_y, mon)
            except Exception as exc:
                _log(f"REALTIME: press failed — {exc}")
            return
        _log("PRESS handler START")
        t0 = time.time()
        # Cancel any in-flight worker from the PREVIOUS turn BEFORE clearing, so
        # it can't race past its cancel guards and repaint stale annotations /
        # pointer after this clear. (Without this, the old worker is only
        # cancelled at release, leaving a window during the new press-hold where
        # it could emit sig_show_annotations again.) Mirrors _handle_release's
        # cancel; the new worker gets a fresh cancel_event at release.
        if self._worker_thread and self._worker_thread.is_alive():
            self._cancel_event.set()
        # Clear any stale spinner from a prior interaction (defensive — if the
        # previous pipeline errored before hide_spinner fired, we don't want
        # to leave a spinner spinning when a new PTT starts).
        self.sig_hide_spinner.emit()
        # Same for stale teaching annotations: clearing on every press means
        # old shapes never survive a no-speech / cancelled / errored prior turn
        # (they'd otherwise linger until the 30s auto-clear timer). Cheap no-op
        # when there were no annotations.
        self.sig_clear_annotations.emit()
        # T4-5: clear the previous turn's caption so it cannot be mistaken for this one's.
        self.sig_caption.emit("")
        self._tts.stop()
        # Prevent TTS speaker decay from leaking into this PTT's transcript
        # (acoustic feedback loop). 200ms window tuned to real laptop-mic decay.
        # MUST be set before the chime — on cold-start the chime triggers a
        # 400-500ms numpy/sounddevice cold-path init we don't want to count
        # against the grace window start time.
        self._stt.set_tts_grace_until(time.time() + 0.200)
        # Play listening chime (async / non-blocking) so user hears instant
        # "mic is hot" feedback. First call triggers sample generation
        # (~5ms CPU) + sounddevice cold-path init (~400ms on fresh portaudio).
        # Both are one-time costs amortized across the session.
        _play_feedback_tone_async("listening")
        # SHELL_AND_CHAT.md §4: the panel's state strip follows the interaction, so it appears
        # the moment recording starts rather than when the answer arrives.
        self.sig_chat_state.emit("listening")
        # Check if TTS thread actually died
        tts_thread = self._tts._current_thread
        tts_alive = tts_thread.is_alive() if tts_thread else False
        _log(f"  tts.stop() called, old thread alive={tts_alive}")
        self._current_app, self._current_title = get_foreground_app()
        _log(f"  app: {self._current_app}")
        try:
            self._stt.start_recording()
            _log(f"  start_recording() in {(time.time()-t0)*1000:.0f}ms")
        except RuntimeError as exc:
            self.sig_show_toast.emit("Microphone couldn't start. Check Settings and try again.", "error")
            _play_feedback_tone_async("error")
            _log(f"ERROR: STT start failed — {exc}")
            return

        # Kick off capture + memory recall in the background so they overlap
        # with the user speaking. Release-time pipeline uses cached result.
        # T0-7: reset + record the press cursor as one atomic write. Keep the
        # value in a local so the waveform dispatch below does not have to
        # re-read shared state the capture thread may already be touching.
        press_cursor = get_cursor_position()
        self._write_press_state(None, "", press_cursor)
        self._capture_thread = threading.Thread(
            target=self._press_time_capture,
            args=(self._current_app,),
            daemon=True,
            name="nimbus-press-capture",
        )
        self._capture_thread.start()

        # Show the LISTENING-state waveform at the cursor position.
        # cursor polygon hides; bars replace it for the duration of the utterance.
        cursor_x, cursor_y = press_cursor
        try:
            mon = monitor_containing(cursor_x, cursor_y, list_monitors())
            if mon is not None:
                self.sig_show_waveform.emit(cursor_x, cursor_y, mon)
        except Exception as exc:
            _log(f"WARN: show_waveform dispatch failed — {exc}")

    def _read_press_state(self) -> tuple[list | None, str, tuple[int, int] | None]:
        """Atomically snapshot the press-time capture result (T0-7).

        Returns ``(captures, memory, cursor_pos)`` read under ``_press_lock`` so
        a reader can never observe a partially-written result — for example when
        ``_release_capture_worker``'s join times out while
        ``_press_time_capture`` is still assigning.
        """
        with self._press_lock:
            return self._press_captures, self._press_memory, self._press_cursor_pos

    def _write_press_state(
        self,
        captures: list | None,
        memory: str,
        cursor_pos: tuple[int, int] | None,
    ) -> None:
        """Atomically publish the press-time capture result (T0-7)."""
        with self._press_lock:
            self._press_captures = captures
            self._press_memory = memory
            self._press_cursor_pos = cursor_pos

    def _press_time_capture(self, app_name: str) -> None:
        """Background thread launched at press time. Captures screens + recalls
        memory while the user is still speaking. Result stored on self for
        the release-time pipeline worker to consume.

        Invariant #3 preserved: overlay.hide_for_capture() fires BEFORE the
        mss.grab() call via sig_hide_overlay (Qt signal to main thread).
        """
        # T0-7: the cursor position was recorded synchronously in _handle_press
        # (before this thread started), so preserve it across the publish rather
        # than overwriting it with None.
        _, _, press_cursor = self._read_press_state()
        try:
            # T2-1: routed through the guarded helper, which suppresses capture in
            # sensitive contexts. Returns [] rather than raising, so the turn continues
            # voice-only.
            captures = self._capture_screens_guarded()
            memory_context = self._memory.recall(app_name)
            # Publish captures + memory as ONE atomic write so a reader can
            # never see captures without their matching memory context.
            self._write_press_state(captures, memory_context, press_cursor)
        except Exception as exc:
            _log(f"ERROR: press-time capture failed — {type(exc).__name__}: {exc}")
            # Release-time path falls back to re-capture.
            self._write_press_state(None, "", press_cursor)

    def _release_capture_worker(
        self,
        release_cursor: tuple[int, int],
        app_name: str,
        result_queue: "queue.Queue",
    ) -> None:
        """Background thread launched at hotkey release. Runs in parallel with
        stt.stop_recording() so re-capture wall-clock is hidden under STT
        finalize latency. Makes the reuse-vs-recapture decision itself to
        preserve 'no flicker on cursor-still sessions' UX — mirrors the
        serial logic in _pipeline_worker pre-refactor.

        Pushes a tuple (captures, memory_context, reason_log_str) to
        result_queue. On exception pushes (None, None, error_str) so the
        main thread's queue.get() never hangs.

        Invariant #3 preserved: overlay.hide_for_capture() fires BEFORE every
        mss.grab() via sig_hide_overlay (Qt signal to main thread).
        """
        try:
            # If press-time capture is still running, wait briefly for it to
            # finish before making the reuse decision. Avoids overlay-hide
            # collision between press-time + release-time captures.
            press_thread = self._capture_thread
            if press_thread is not None and press_thread.is_alive():
                press_thread.join(timeout=0.5)

            # T0-7: single atomic snapshot. Reading the three fields
            # independently could mix a fresh captures list with a stale memory
            # string or cursor position if the press thread is still writing
            # (reachable when the join above times out).
            press_captures, press_memory, press_cursor = self._read_press_state()

            # Compute cursor delta at release.
            cursor_moved_px = 9999
            if press_cursor is not None:
                dx = release_cursor[0] - press_cursor[0]
                dy = release_cursor[1] - press_cursor[1]
                cursor_moved_px = int((dx * dx + dy * dy) ** 0.5)

            if (
                press_captures is not None
                and cursor_moved_px <= _REUSE_THRESHOLD_PX
            ):
                reason = (
                    f"reusing press-time captures "
                    f"(cursor moved {cursor_moved_px}px, "
                    f"threshold {_REUSE_THRESHOLD_PX}px)"
                )
                result_queue.put((press_captures, press_memory, reason))
                return

            # Re-capture path — fire invariant-preserving hide → grab → show.
            if press_captures is None:
                reason_suffix = "no press-time capture available"
            else:
                reason_suffix = (
                    f"cursor moved {cursor_moved_px}px > "
                    f"{_REUSE_THRESHOLD_PX}px threshold"
                )
            reason = f"re-capturing on release ({reason_suffix})"

            captures = self._capture_screens_guarded()  # T2-1
            memory_context = self._memory.recall(app_name)
            result_queue.put((captures, memory_context, reason))
        except Exception as exc:
            _log(
                f"ERROR: release capture worker failed — "
                f"{type(exc).__name__}: {exc}"
            )
            result_queue.put(
                (None, None, f"error: {type(exc).__name__}: {exc}")
            )

    @property
    def _journal(self):
        """Lazily-built review queue (T3-3).

        Lazy so a user with the journal disabled never creates the table, and so the first
        interaction is not slowed by opening a database it may not need.
        """
        if getattr(self, "_journal_store", None) is None:
            from review import ReviewQueue
            self._journal_store = ReviewQueue()
        return self._journal_store

    def _record_journal_entry(self, app_name, transcript, spoken_text, result, dbg) -> None:
        """Queue a completed exchange for spaced review (T3-3).

        ``target_label`` is populated from the element Nimbus pointed at, which is what makes
        an item *positional*: it can later be asked as "show me where the export button is"
        and graded against a real grounding call rather than against remembered prose. That
        is the capability no flashcard app has, and it costs nothing to capture here.

        Every failure is swallowed and logged. This runs after the user already has their
        answer; losing a journal entry is invisible, while raising here would surface as a
        failed interaction.
        """
        if not JOURNAL_ENABLED:
            return
        try:
            label = getattr(result, "element_label", None) or ""
            item_id = self._journal.add(
                app_name=app_name,
                question=transcript,
                answer=spoken_text,
                target_label=label,
            )
            if item_id is not None:
                dbg.log(f"JOURNAL: queued item {item_id} for review")
        except Exception as exc:
            dbg.log(f"JOURNAL: skipped - {type(exc).__name__}: {exc}")

    def _handle_journal_intent(self, transcript: str, app_name: str) -> str | None:
        """Answer a journal command locally, or return None to run the normal pipeline (T3-3).

        Returns the text to speak. No API call and no screenshot: navigating your own journal
        should be free and instant, and it is the one class of question Nimbus can answer
        entirely from local data.
        """
        if not JOURNAL_ENABLED:
            return None
        try:
            from review import classify_review_intent, format_recap_for_speech

            intent = classify_review_intent(transcript)
            if intent is None:
                return None
            if intent == "recap":
                return format_recap_for_speech(self._journal.recap())
            due = self._journal.due()
            if not due:
                return (
                    "nothing's due for review right now. ask me about something and "
                    "i'll bring it back in a day or two."
                )
            if intent == "due":
                count = len(due)
                subject = "topic" if count == 1 else "topics"
                first = due[0]["question"].rstrip("?").strip().lower()
                return (
                    f"you've got {count} {subject} to review. "
                    f"first one is {first}. say quiz me when you're ready."
                )
            # intent == "quiz"
            item = due[0]
            self._pending_quiz_id = item["id"]
            if item["target_label"]:
                return (
                    f"alright. show me where the {item['target_label']} is, "
                    f"then tell me what it does."
                )
            return f"alright. {item['question'].rstrip('?').strip()}?"
        except Exception as exc:
            _log(f"JOURNAL: intent handling failed - {type(exc).__name__}: {exc}")
            return None

    def _on_caption(self, text: str) -> None:
        """Render a live transcript caption (T4-5). Qt main thread.

        Positioned on the monitor holding the press-time cursor, so the caption appears on
        the screen the user was asking about rather than wherever the mouse has drifted to.
        Falls back to the live cursor position if no press was recorded.
        """
        if self._overlay is None or not CAPTIONS_ENABLED:
            return
        # §6.1: the HUD already shows what was heard, in a panel the user can read at leisure.
        # Two copies of the same words on one screen is noise, and the caption is the one that
        # has nowhere to go. The HUD is asked rather than assumed, because it stands down
        # itself when it is hidden or auto-hidden.
        if self._hud is not None:
            try:
                if self._hud.is_showing_transcript():
                    self._overlay.clear_captions()
                    return
            except Exception:
                pass  # a HUD that cannot answer must not suppress the caption
        if not text.strip():
            self._overlay.clear_captions()
            return
        _, _, press_cursor = self._read_press_state()  # T0-7 atomic read
        cx, cy = press_cursor or get_cursor_position()
        monitor = monitor_containing(cx, cy, list_monitors())
        try:
            self._overlay.set_caption(text, cx, cy, monitor)
        except Exception as exc:
            # A caption is decoration. It must never take down an interaction.
            _log(f"CAPTION: skipped - {type(exc).__name__}: {exc}")

    def _privacy_verdict(self) -> tuple[bool, str]:
        """Ask the Privacy Guard whether this turn may capture the screen (T2-1).

        Reads the foreground app recorded at press time rather than calling
        ``get_foreground_app()`` again: by the time a capture thread runs, the foreground
        window may have changed, and the decision must be about the window the user was
        actually looking at when they asked.
        """
        from config import PRIVACY_GUARD, PRIVACY_GUARD_APPS, PRIVACY_GUARD_TITLES
        from privacy import (
            DEFAULT_BLOCKED_APPS,
            DEFAULT_BLOCKED_TITLE_PATTERNS,
            should_skip_capture,
        )

        def _extra(raw: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in raw.split(",") if part.strip())

        return should_skip_capture(
            app_name=self._current_app or "",
            window_title=self._current_title or "",
            enabled=PRIVACY_GUARD.strip().lower() == "on",
            blocked_apps=DEFAULT_BLOCKED_APPS + _extra(PRIVACY_GUARD_APPS),
            blocked_title_patterns=(
                DEFAULT_BLOCKED_TITLE_PATTERNS + _extra(PRIVACY_GUARD_TITLES)
            ),
        )

    def _capture_screens_guarded(self) -> list:
        """Hide the overlay, grab every screen, show it again -- unless the Privacy Guard
        says no (T2-1). Returns ``[]`` when capture is suppressed.

        **This is the single choke point for capture.** There were three call sites
        (press-time, release-time re-capture, and the realtime path), all repeating the
        same hide/wait/grab/show dance. The audit said two; verification found three, which
        is exactly why the gate belongs in one shared helper rather than being applied at
        each site by hand -- a fourth site added later inherits the guard for free.

        Invariant #3 is preserved unchanged: ``overlay.hide_for_capture()`` fires via
        ``sig_hide_overlay`` BEFORE ``mss.grab()``, and the overlay is always restored.

        An empty list means "no screenshot this turn", NOT "abort". Callers continue and the
        interaction proceeds voice-only, because the user asked a question and deserves an
        answer even if Nimbus must answer it blind.
        """
        skip, reason = self._privacy_verdict()
        if skip:
            _log(f"PRIVACY GUARD: capture suppressed - {reason}")
            # Counted here, at the one choke point, so the number on Home is the number of
            # actual suppressions rather than an estimate assembled from log lines. Written to the
            # session store as well as kept in memory: the in-memory list is the fallback for a run
            # with no store, and the store is what makes "this week" mean this week.
            self._privacy_skips.append(datetime.now())
            self._privacy_skips = self._within_window(self._privacy_skips)
            store = self.__dict__.get("_sessions")
            recorder = getattr(store, "record_privacy_skip", None)
            if recorder is not None:
                try:
                    recorder(reason)
                except Exception:
                    pass  # a counter must never cost the user their answer
            self.sig_show_toast.emit(
                f"Screenshot skipped - {reason}. Answering without seeing your screen.",
                "info",
            )
            return []
        self.sig_hide_overlay.emit()
        _wait_for_compositor()
        try:
            return capture_all_screens()
        finally:
            # Restore the overlay even if grab() raises, or the user is left with a
            # permanently invisible pointer for the rest of the session.
            self.sig_show_overlay.emit()

    def _is_response_in_flight(self) -> bool:
        """Whether Esc should be treated as cancel right now (T2-2).

        Two independent conditions, because a response occupies two distinct phases and
        the user perceives both as "Nimbus is busy":

        * the pipeline worker is still running (thinking / streaming), and
        * TTS is still speaking, which outlives the worker.

        Anything else and Esc is left completely alone. Esc is among the most-pressed keys
        on the keyboard, so a false positive here means Nimbus interfering with every
        dialog dismissal and vim escape in the session.

        Called from the pynput listener thread on every Esc press, so it must be cheap and
        must not raise -- both are satisfied by only reading thread liveness flags.
        """
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return True
        tts_thread = getattr(self._tts, "_current_thread", None)
        return bool(tts_thread is not None and tts_thread.is_alive())

    def _on_cancel(self) -> None:
        """Abort the in-flight response (T2-2). Qt main thread.

        Performs exactly the sequence ``_handle_press`` already uses to abandon a previous
        turn, minus anything that would start a NEW interaction. Reusing that sequence is
        deliberate: it is already proven against the 11 cancel checkpoints in the pipeline
        worker, so cancel has no new failure modes of its own.

        The TTS grace window is the subtle one. ``tts.stop()`` cuts playback mid-word but
        the speaker keeps decaying for a few tens of milliseconds; without the grace window
        that decay is picked up by the microphone and contaminates the *next* transcript.
        The press path sets it for the same reason.

        Nothing is written to memory: the turn was abandoned, and recording a partial
        interaction would pollute per-app memory with an answer the user rejected.
        """
        import time  # module-local, matching _handle_press / _handle_release

        _log("CANCEL: Esc pressed - aborting in-flight response")
        self._cancel_event.set()
        self._tts.stop()
        self._stt.set_tts_grace_until(time.time() + 0.200)
        self.sig_hide_spinner.emit()
        self.sig_chat_state.emit("idle")
        self.sig_clear_annotations.emit()
        # T4-5: the caption showed what was heard. Once the turn is abandoned it is stale,
        # and leaving it up implies Nimbus is still working on it.
        self.sig_caption.emit("")
        if self._realtime is not None:
            try:
                self._realtime.stop()
            except Exception:
                pass

    def _handle_release(self) -> None:
        """Hotkey released: cancel previous worker, spawn new pipeline."""
        import time
        # GPT-Realtime mode — capture the screen, hand it to the
        # realtime session, which commits the spoken audio + requests a
        # response (model speaks back + emits point_at). Coordinate refinement
        # happens in _realtime_on_coordinate. Normal pipeline is skipped.
        if self._realtime is not None:
            _log("RELEASE handler START (realtime mode)")
            self.sig_hide_waveform.emit()
            try:
                import io as _io
                import base64 as _b64
                # Hide overlay so the model never sees our own blue cursor.
                # T2-1: same guard as the normal pipeline — a speech-to-speech turn is
                # no less capable of shipping a password manager to a cloud provider.
                captures = self._capture_screens_guarded()
                # Cursor-screen capture is first (capture_all_screens sorts it).
                self._realtime_capture = captures[0] if captures else None
                if self._realtime_capture is not None:
                    buf = _io.BytesIO()
                    self._realtime_capture.image.save(buf, format="JPEG", quality=85)
                    b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
                    self._realtime.respond(screenshot_jpeg_b64=b64)
                    # Show THINKING spinner until audio starts (on_audio_start hides it).
                    _, _, press_cursor = self._read_press_state()  # T0-7
                    cx, cy = press_cursor or get_cursor_position()
                    mon = monitor_containing(cx, cy, list_monitors())
                    if mon is not None:
                        self.sig_show_spinner.emit(cx, cy, mon)
            except Exception as exc:
                _log(f"REALTIME: release failed — {exc}")
            return
        _log(f"RELEASE handler START (Qt main thread)")
        # LISTENING → THINKING transition: hide waveform, show spinner at the
        # current cursor position. Cursor polygon stays hidden while spinner
        # runs; buddy reappears when pipeline hides spinner + fires bezier.
        self.sig_hide_waveform.emit()

        # Snapshot release-time cursor synchronously. Taken here (not at
        # worker-start) so mouse motion during STT can't flip the
        # reuse-vs-recapture decision mid-flight. Reused for the spinner
        # dispatch below to avoid a redundant Win32 GetCursorPos call.
        release_cursor: tuple[int, int] | None = None
        try:
            cursor_x, cursor_y = get_cursor_position()
            release_cursor = (cursor_x, cursor_y)
            mon = monitor_containing(cursor_x, cursor_y, list_monitors())
            if mon is not None:
                self.sig_show_spinner.emit(cursor_x, cursor_y, mon)
        except Exception as exc:
            _log(f"WARN: show_spinner dispatch failed — {exc}")
        if release_cursor is None:
            _, _, press_cursor = self._read_press_state()  # T0-7
            release_cursor = press_cursor or (0, 0)

        if self._worker_thread and self._worker_thread.is_alive():
            _log("  cancelling previous worker + stopping TTS")
            self._cancel_event.set()
            self._tts.stop()
            # Same 200ms grace as press — prevents aborted TTS tail from
            # contaminating the new PTT's transcript.
            self._stt.set_tts_grace_until(time.time() + 0.200)

        self._cancel_event = threading.Event()

        # Size-1 queue: capture worker pushes once, pipeline worker gets once.
        release_capture_queue: queue.Queue = queue.Queue(maxsize=1)

        # Launch capture worker BEFORE pipeline worker so it starts doing its
        # reuse-decision + potential mss.grab in parallel with stt.stop_recording.
        capture_worker_thread = threading.Thread(
            target=self._release_capture_worker,
            args=(release_cursor, self._current_app, release_capture_queue),
            daemon=True,
            name="nimbus-release-capture",
        )
        capture_worker_thread.start()

        self._worker_thread = threading.Thread(
            target=self._pipeline_worker,
            args=(
                self._current_app,
                self._current_title,
                self._cancel_event,
                release_capture_queue,
            ),
            daemon=True,
            name="nimbus-pipeline",
        )
        self._worker_thread.start()

    # --- Pipeline worker (runs on worker thread) ---

    def _pipeline_worker(
        self,
        app_name: str,
        window_title: str,
        cancel: threading.Event,
        capture_queue: "queue.Queue",
    ) -> None:
        """Sequential pipeline: STT → capture → recall → stream → TTS → overlay.

        ``capture_queue`` is populated in parallel by
        :meth:`_release_capture_worker` (launched in ``_handle_release``
        BEFORE this thread). This thread blocks on ``stt.stop_recording()``,
        then reads the capture result from the queue. Wall-clock becomes
        ``max(STT, capture)`` instead of ``STT + capture``.
        """
        dbg = DebugSession.start(app_name, window_title)
        # log the ACTUAL providers this interaction used. The old
        # hardcoded log labels lied about which
        # provider ran; this line lets you open any interaction.log and see
        # exactly what was used (STT, LLM, TTS).
        dbg.log(
            f"PROVIDERS: STT={type(self._stt).__name__} | "
            f"LLM={getattr(self._ai, 'model_id', None) or type(self._ai).__name__} | "
            f"TTS={type(self._tts).__name__}"
        )
        try:
            if cancel.is_set():
                return

            dbg.log("STT: calling stop_recording()...")
            transcript = self._stt.stop_recording()
            dbg.log(f"STT: {self._stt._chunk_count} audio chunks captured")
            dbg.log(f"STT: latest partial: {self._stt._latest_partial!r}")
            dbg.log(f"STT: final transcript ({len(transcript)} chars): {transcript!r}")
            _log(f"Transcript: {transcript!r}")
            if not transcript.strip():
                dbg.log("NO SPEECH DETECTED — skipping interaction")
                _log("No speech detected, skipping.")
                self.sig_chat_state.emit("idle")
                return

            # SHELL_AND_CHAT.md §4. The question goes up as soon as it is known rather than
            # with the answer, so the panel shows what Nimbus heard while it is still
            # thinking -- which is also when a misheard transcript is still worth catching.
            self._emit_chat_message("user", transcript)
            self.sig_chat_state.emit("thinking")

            # T3-3: journal commands ("quiz me", "what should i review") are answered from
            # the local database with no API call and no screenshot. Checked here, before
            # capture, because taking a screenshot for a question about the user's own
            # journal would be wasted work and an unnecessary privacy exposure.
            journal_reply = self._handle_journal_intent(transcript, app_name)
            if journal_reply is not None:
                dbg.log(f"JOURNAL: answered locally - {journal_reply!r}")
                self.sig_caption.emit(transcript)  # T4-5
                self.sig_hide_spinner.emit()
                self._emit_chat_message("nimbus", journal_reply)
                self.sig_chat_state.emit("speaking")
                if not cancel.is_set():
                    self._tts.speak(journal_reply)
                self.sig_chat_state.emit("idle")
                # A journal answer is a real completed turn -- it just did not need the model.
                self._record_turn(app_name, transcript, journal_reply)
                return

            if cancel.is_set():
                return

            # Read capture result from the worker that's been running in
            # parallel with stt.stop_recording above. Timeout is 5s — far
            # above any realistic capture time (~300ms worst case) — so if
            # the worker errored silently we fail loudly instead of hanging.
            # Fallback on timeout or error: use press-time captures if
            # available, else abort pipeline.
            try:
                captures, memory_context, capture_reason = capture_queue.get(
                    timeout=5.0
                )
            except queue.Empty:
                dbg.log("CAPTURE: worker timeout after 5s — falling back to press-time")
                # T0-7: atomic snapshot so captures and memory always match.
                captures, memory_context, _ = self._read_press_state()
                capture_reason = "worker timeout — press-time fallback"

            if captures is None:
                fallback_captures, fallback_memory, _ = self._read_press_state()  # T0-7
                if fallback_captures is not None:
                    dbg.log(
                        f"CAPTURE: worker failed ({capture_reason}) — "
                        f"using press-time fallback"
                    )
                    captures = fallback_captures
                    memory_context = fallback_memory
                else:
                    dbg.log(
                        f"CAPTURE: worker failed and no press-time fallback "
                        f"({capture_reason}) — aborting pipeline"
                    )
                    _log("ERROR: No screenshots available for Nimbus, aborting.")
                    return

            dbg.log(f"CAPTURE: {capture_reason}")

            dbg.log(f"CAPTURE: {len(captures)} screen(s)")
            for i, c in enumerate(captures):
                dbg.log(f"  screen[{i}]: {c.target_width}x{c.target_height}, "
                        f"scale=({c.scale_x:.2f}, {c.scale_y:.2f}), "
                        f"monitor={c.monitor}, cursor={c.is_cursor_screen}")
                dbg.save_screenshot(c.image, f"screenshot_{i}.jpg")
            dbg.log(f"MEMORY: recalled {len(memory_context)} chars for {app_name}")

            if cancel.is_set():
                return

            user_text = transcript
            if memory_context:
                user_text = (
                    f"[context from past sessions — use silently, don't summarize or reference it:]\n"
                    f"{memory_context}\n\n"
                    f"{transcript}"
                )

            # Curated KB recall (user-uploaded per-app docs). Empty tuple
            # if no .md file exists for this app — Nimbus proceeds with
            # vision + memory only ("Nimbus already knows that software"
            # path). When present, ask_stream injects as a 2nd
            # cache_control system block (Anthropic) or concats into
            # system string (Gemini).
            #
            # Wrapped in try/except because KB files are user-controlled
            # and could be malformed (bad encoding, permission errors,
            # symlink loops, etc.). Failure here must NOT crash the
            # pipeline — Nimbus can still answer with vision + memory.
            try:
                # T3-2: pass the transcript so an over-budget knowledge base is ranked by
                # relevance rather than blindly tail-truncated. Previously a 200k-char
                # manual meant Nimbus read the last 60k and silently answered questions
                # about the discarded part from nothing.
                kb_content, kb_app_name = kb.recall(app_name, query=transcript)
            except Exception as exc:
                dbg.log(
                    f"KB: read failed ({type(exc).__name__}: {exc}), "
                    f"falling back to no-KB path"
                )
                kb_content, kb_app_name = "", ""
            if kb_content:
                dbg.log(
                    f"KB: injected {len(kb_content)} chars from "
                    f"{kb_app_name}.md"
                )
            else:
                dbg.log(f"KB: no file for {app_name}, skipping")

            images = [(c.image, c.label) for c in captures]
            # T2-1: the Privacy Guard returns an empty capture list rather than aborting,
            # so the user still gets an answer -- just a blind one. Everything geometric is
            # gated on this being non-None, because pointing at a screen Nimbus was not
            # allowed to look at is meaningless.
            cursor_capture = captures[0] if captures else None
            voice_only = cursor_capture is None
            if voice_only:
                dbg.log("CAPTURE: none available (privacy guard) - voice-only turn")
                # Tell the model plainly, or it answers as though it can see and describes
                # a screen it was never shown.
                # Mutating user_text is enough: _ask_kwargs is assembled from it further
                # down. Setting _ask_kwargs here would raise NameError -- it does not
                # exist yet.
                user_text = (
                    f"{user_text}\n\n(you cannot see the screen for this question - it was "
                    f"withheld for privacy. answer from the conversation and what you know, "
                    f"and say you cannot see the screen if it matters.)"
                )

            if cancel.is_set():
                return

            dbg.log("LLM: streaming started...")
            stream_started_at = time.monotonic()
            _log("Asking Nimbus...")

            # Arm one-shot first-audible-word log. Fires on the first
            # successful sounddevice.play(samples) in the TTS playback
            # worker — closes the gap between "MODEL: streaming started"
            # (when we open the HTTP connection) and the actual moment
            # the user hears something. Per-interaction (slot clears
            # after firing once); next interaction re-arms.
            self._tts.arm_first_chunk_callback(
                lambda: dbg.log("TTS: first audible chunk played")
            )

            # Sentence-level TTS streaming. Flush complete
            # sentences from the buffer as each .!? boundary arrives, so TTS
            # starts on sentence 1 (~1200ms into Nimbus stream) instead of
            # after the full response (~3700ms). Saves ~2s perceived latency.
            #
            # Tag-safety: stop flushing the moment '[' appears in the buffer
            # (start of [POINT:x,y:label] tag). On stream close, use
            # result.spoken_text (tag-stripped) to compute + flush the tail.
            sentence_buffer = ""
            tag_started = False
            already_flushed_chars = 0

            # draw-on-screen teaching mode. Uses the module-
            # level cached config.ANNOTATION_MODE (resolved ONCE at import) — NOT
            # a fresh resolve_setting() — so there is ZERO per-interaction keyring
            # read/write on the hot path (resolve_setting writes to keyring on
            # every call when the value is in env). Trade-off: toggling needs an
            # app restart, which is fine (set once in .env). When on, swap in the
            # annotation system prompt so the model emits shape tags. Default
            # off = unchanged behavior (the [POINT] cursor prompt).
            annotation_mode = ANNOTATION_MODE == "on"
            # T1-2: providers that return geometry on a separate channel need none
            # of the tag machinery. Coordinates never enter the spoken text, so the
            # bracket guard below is skipped — it currently halts TTS flushing on
            # ANY bracket, including legitimate prose like "the array index [0]".
            structured_geometry = self._ai.supports_structured_geometry()
            dbg.log(f"LLM: structured_geometry={structured_geometry}")
            _ask_kwargs = dict(
                images=images,
                transcript=user_text,
                history=self._history,
                kb_content=kb_content,
                kb_app_name=kb_app_name,
            )
            if annotation_mode:
                _ask_kwargs["system_prompt"] = _NIMBUS_ANNOTATION_SYSTEM_PROMPT
            # T2-5 Code Mode: append a per-app addendum when the foreground app is one we
            # have specific guidance for. APPENDED, never substituted -- the base prompt
            # carries the persona, the write-for-the-ear contract and the pointing rules.
            #
            # Only set system_prompt when there IS an addendum, so a turn in an unrecognised
            # app passes exactly the kwargs it did before and behaviour is untouched.
            app_addendum = addendum_for_app(self._current_app)
            if app_addendum:
                base_prompt = (
                    _NIMBUS_ANNOTATION_SYSTEM_PROMPT if annotation_mode
                    else _NIMBUS_SYSTEM_PROMPT
                )
                _ask_kwargs["system_prompt"] = base_prompt + app_addendum
                dbg.log(f"PROMPT: code-mode addendum for {self._current_app}")
            if structured_geometry:
                # Declares the draw_box tool alongside point_at. Only passed to
                # providers that accept it, so other clients keep their exact
                # existing signature.
                _ask_kwargs["annotation_mode"] = annotation_mode

            # Time to the first token, measured separately from time to the last one.
            #
            # "Sometimes it takes too long" was not diagnosable from the old log, which
            # recorded only when the stream opened and when it finished. Those two numbers
            # cannot tell a slow model apart from a long answer, and they hide the number
            # that actually decides how the app feels: how long the user waits in silence
            # before anything is spoken. Recorded per turn so a slow one can be attributed
            # rather than guessed at.
            first_delta_at: float | None = None

            with self._ai.ask_stream(**_ask_kwargs) as stream:
                plan = getattr(self._ai, "last_geometry_plan", None)
                if plan:
                    dbg.log(
                        f"LLM: query_class={plan.get('query_class')} "
                        f"geometry_requested={plan.get('requested')} "
                        f"forced={plan.get('forced')}"
                    )
                for delta in stream.text_deltas():
                    if cancel.is_set():
                        return
                    if first_delta_at is None:
                        first_delta_at = time.monotonic()
                        dbg.log("LLM: first token")
                    sentence_buffer += delta
                    if not structured_geometry and "[" in sentence_buffer:
                        tag_started = True
                    if not tag_started:
                        sentences, sentence_buffer = flush_sentences(sentence_buffer)
                        for s in sentences:
                            if cancel.is_set():
                                return
                            self._tts.speak_sentence(s)
                            # +1 for the separator space matched by [.!?]\s
                            already_flushed_chars += len(s) + 1

                result = stream.final_result()

            if cancel.is_set():
                return

            dbg.log(f"LLM: done ({len(result.spoken_text)} chars)")
            # Where the wait actually went. A turn that felt slow is either slow to start,
            # which is the model, or slow to finish, which is a long answer streaming at a
            # normal rate. Those are different problems and the old log conflated them.
            if first_delta_at is not None:
                dbg.log(
                    f"LLM: {first_delta_at - stream_started_at:.1f}s to first token, "
                    f"{time.monotonic() - first_delta_at:.1f}s streaming the rest"
                )
            else:
                dbg.log("LLM: no text was streamed at all")
            dbg.log(f"LLM: spoken_text: {result.spoken_text!r}")
            dbg.log(f"LLM: coordinate={result.coordinate}, label={result.element_label!r}, screen={result.screen_number}")
            geometry_note = getattr(stream, "geometry_diagnostics", "")
            if geometry_note:
                dbg.log(f"GEOMETRY: {geometry_note}")
            # T0-3: malformed point tags are stripped from spoken_text so they
            # can never be read aloud. Log the raw text so a model drifting
            # off-format stays diagnosable instead of failing silently.
            if getattr(result, "malformed_tags", ()):
                dbg.log(
                    f"LLM: stripped {len(result.malformed_tags)} malformed point "
                    f"tag(s), not spoken: {list(result.malformed_tags)!r}"
                )
            # T1-5: grounding citations go to the debug log and the memory record only.
            # They are deliberately kept out of spoken_text — reading URLs aloud would
            # violate the write-for-the-ear contract the system prompt sets.
            citations = list(getattr(self._ai, "last_citations", ()) or ())
            search_queries = list(getattr(self._ai, "last_search_queries", ()) or ())
            if citations:
                dbg.log(f"GROUNDING: {len(citations)} citation(s)")
                for citation in citations:
                    dbg.log(f"  - {citation.get('title', '')} :: {citation.get('uri', '')}")
            if search_queries:
                # Logged separately: a strong persona prompt can suppress citation
                # metadata while search still runs, so queries-without-citations is a
                # normal state, not a failure.
                dbg.log(f"GROUNDING: searched for {search_queries!r}")

            # annotation mode: the shape tags ([ARROW]/[CIRCLE]/...) are
            # still in result.spoken_text — ai.py's parser only strips [POINT].
            # Parse + strip them here so TTS never reads coordinates aloud, and
            # map their screenshot coords to physical for the overlay.
            spoken_text = result.spoken_text
            phys_annotations: list = []
            if voice_only and annotation_mode:
                # T2-1: shapes are positioned against a screenshot that does not exist.
                # Tags must still be stripped from the spoken text, which the
                # parse_annotations call below the elif does -- so fall through to it
                # rather than skipping, then discard the shapes.
                spoken_text, _ = parse_annotations(result.spoken_text)
                dbg.log("ANNOTATIONS: skipped - no screenshot was taken")
            elif annotation_mode and structured_geometry:
                # T1-2: geometry came from the tool channel already in Space C, so
                # there is nothing to strip from the spoken text and no regex to
                # run. Straight to the physical-coordinate transform.
                try:
                    shapes = stream.geometry() if hasattr(stream, "geometry") else []
                    if shapes:
                        phys_annotations = _annotations_to_physical(
                            shapes, cursor_capture
                        )
                    dbg.log(
                        f"ANNOTATIONS: {len(shapes)} structured shapes -> "
                        f"{len(phys_annotations)} physical"
                    )
                except Exception as exc:  # never break the pipeline
                    dbg.log(f"ANNOTATIONS: structured transform skipped - {exc}")
            elif annotation_mode and result.spoken_text:
                # Strip FIRST, outside the try — parse_annotations is pure regex
                # and cannot raise, so spoken_text is ALWAYS tag-stripped before
                # the tail flush (coords never reach TTS even if the coordinate
                # transform below fails). Only the physical transform is guarded.
                spoken_text, _anns = parse_annotations(result.spoken_text)
                if _anns:
                    try:
                        _anns = _refine_annotations(
                            self._ai, _anns, cursor_capture, transcript, dbg
                        )
                        phys_annotations = _annotations_to_physical(
                            _anns, cursor_capture
                        )
                        dbg.log(
                            f"ANNOTATIONS: {len(_anns)} shapes parsed -> "
                            f"{len(phys_annotations)} physical"
                        )
                    except Exception as exc:  # never break the pipeline
                        dbg.log(f"ANNOTATIONS: transform skipped — {exc}")

            # Flush the tail (everything in spoken_text that hasn't yet been
            # sent to TTS). Uses the tag-stripped spoken_text — avoids ever
            # speaking the [POINT:x,y:label] OR shape tags aloud.
            if spoken_text:
                tail = spoken_text[already_flushed_chars:].strip()
                if tail:
                    dbg.log(f"TTS: flushing tail ({len(tail)} chars)")
                    self._tts.speak_sentence(tail)

            if cancel.is_set():
                return

            _log(f"Response: {result.spoken_text[:80]}...")

            # Grid-locator fallback for Ollama / weak vision models.
            # If Nimbus returned no [POINT:x,y] tag AND we're using Ollama AND
            # the query was directional, run grid-locator on the cursor
            # screenshot to derive coordinates. Returns physical virtual-desktop
            # coords (same convention as unscale_model_coords output), or None
            # if the locator can't find a target.
            #
            # CANCEL GUARD: skip the locator entirely if cancel fired between
            # stream.final_result() and here (e.g. ESC during sentence
            # streaming). Without this, locator's 2 Ollama calls would run for
            # 5-10s on a cancelled worker and emit pointer + memory side
            # effects for an interaction the user already aborted.
            if cancel.is_set():
                return
            if annotation_mode:
                # Annotation mode draws SHAPES, not a grid-located cursor point.
                # Skip the grid-locator entirely — it would otherwise fire for
                # weak-vision providers (OpenAI/Ollama) on directional queries
                # since annotation responses carry no [POINT] tag, adding 2 extra
                # LLM calls + emitting an unrelated cursor point that competes
                # with the shapes. Keeps annotation behavior identical across
                # providers.
                locator_phys_xy = None
            elif voice_only:
                # T2-1: nothing to locate against. The grid locator would dereference a
                # capture that was never taken.
                locator_phys_xy = None
            else:
                locator_phys_xy = _maybe_locate_via_grid(
                    ai_client=self._ai,
                    result=result,
                    cursor_capture=cursor_capture,
                    query=transcript,
                    dbg=dbg,
                )
            # POST-LOCATOR cancel guard: if locator just ran (took seconds on
            # Ollama), the user may have hit ESC or pressed Ctrl+Alt+Space
            # again. Stop before emitting any pointer / memory side effects
            # — those would race the new pipeline and write history for an
            # interaction that no longer matters.
            if cancel.is_set():
                return

            resolved_coordinate = result.coordinate
            if voice_only and resolved_coordinate is not None:
                # T2-1: a model given no image can still emit a coordinate. Placing a
                # pointer from it would be pure invention, so it is dropped.
                dbg.log("GROUNDING: discarding coordinate - no screenshot was taken")
                resolved_coordinate = None
            if result.coordinate and not voice_only:
                x_model, y_model = result.coordinate
                screen_num = result.screen_number

                # Save screenshot with red marker at Nimbus's coordinate
                dbg.save_screenshot(
                    cursor_capture.image,
                    "screenshot_with_marker.jpg",
                    coordinate=(x_model, y_model),
                )

                target_capture = cursor_capture
                if screen_num is not None:
                    for c in captures:
                        if f"screen{screen_num}" in c.label.replace(" ", ""):
                            target_capture = c
                            break

                # Direct GPT-5.4 grounding remains the primary result. For a
                # directional target, verify it in a compact native-resolution
                # crop before placing the cursor. A failed/uncertain verifier
                # never replaces the original coordinate.
                # T1-3: three refinement modes.
                #   crop    - Nimbus crops a native-resolution window and re-asks
                #             (provider-agnostic, the long-standing default)
                #   agentic - the model inspected the image itself; skip our pass, or we
                #             would pay for refinement twice
                #   off     - no verification at all
                refinement_mode = resolve_setting("GROUNDING_REFINEMENT", default="crop")
                agentic_available = bool(
                    getattr(self._ai, "supports_agentic_refinement", lambda: False)()
                )
                if refinement_mode == "agentic" and not agentic_available:
                    # Silent fallback: a provider that cannot self-refine must still get
                    # verified rather than silently losing accuracy.
                    dbg.log(
                        "REFINE: agentic requested but unsupported by this provider "
                        "- falling back to crop"
                    )
                    refinement_mode = "crop"
                elif refinement_mode == "agentic":
                    dbg.log("REFINE: agentic (model self-inspected; skipping crop pass)")
                elif refinement_mode == "off":
                    dbg.log("REFINE: disabled by setting")

                should_refine = (
                    refinement_mode == "crop"
                    and (_looks_directional(transcript)
                         or _references_cursor_area(transcript))
                )
                if should_refine:
                    refined = _refine_model_coordinate(
                        ai_client=self._ai,
                        capture=target_capture,
                        model_x=x_model,
                        model_y=y_model,
                        target=result.element_label or transcript,
                        query=transcript,
                        dbg=dbg,
                    )
                    if refined is not None:
                        x_model, y_model = refined
                        resolved_coordinate = refined
                        dbg.log(f"COORDS: verified model=({x_model},{y_model})")

                phys_x, phys_y = unscale_model_coords(
                    model_x=x_model,
                    model_y=y_model,
                    scale_x=target_capture.scale_x,
                    scale_y=target_capture.scale_y,
                    monitor_left=target_capture.monitor["left"],
                    monitor_top=target_capture.monitor["top"],
                    target_w=target_capture.target_width,
                    target_h=target_capture.target_height,
                )
                dbg.log(f"COORDS: model=({x_model},{y_model}) -> physical=({phys_x},{phys_y})")
                dbg.log(f"COORDS: scale=({target_capture.scale_x:.2f},{target_capture.scale_y:.2f}), "
                        f"monitor_offset=({target_capture.monitor['left']},{target_capture.monitor['top']})")
                # THINKING → FLYING: hide spinner BEFORE the point_at signal
                # so the overlay paints cleanly (no flicker of spinner +
                # cursor at the same time during the transition).
                self.sig_hide_spinner.emit()
                self.sig_point_at.emit(phys_x, phys_y, target_capture.monitor)
            elif locator_phys_xy is not None:
                # Grid-locator fallback (Ollama path): coords already in PHYSICAL
                # virtual-desktop space — skip unscale_model_coords, emit directly.
                phys_x, phys_y = locator_phys_xy
                dbg.log(f"COORDS: grid-locator -> physical=({phys_x},{phys_y})")
                self.sig_hide_spinner.emit()
                self.sig_point_at.emit(phys_x, phys_y, cursor_capture.monitor)
            else:
                dbg.log("COORDS: no coordinate returned (text-only response)")
                # Text-only path: spinner still needs to go away so the buddy
                # returns to follow-cursor mode during TTS playback.
                self.sig_hide_spinner.emit()

            # draw the teaching annotations (additive to the cursor).
            # Independent of the [POINT]/locator branches above — shapes show
            # during SPEAKING and auto-clear after 30s. Emit on EVERY annotation-
            # mode turn (even with zero shapes) so a no-shape answer clears any
            # stale circles/arrows from the previous turn immediately, instead of
            # leaving them on screen (misleading) until the 30s timer fires.
            # Final cancel guard: if the user re-pressed (which cancels this
            # worker), do NOT repaint — the press already cleared the overlay.
            if cancel.is_set():
                return
            if annotation_mode:
                self.sig_show_annotations.emit(
                    phys_annotations, cursor_capture.monitor
                )

            pointer_targets = []
            if resolved_coordinate:
                pointer_targets.append(resolved_coordinate)
            elif locator_phys_xy is not None:
                # Memory recording: store the grid-locator coords (physical
                # virtual-desktop space) so future recall can reference them
                # the same way Nimbus coords are referenced.
                pointer_targets.append(locator_phys_xy)

            # Use the tag-stripped `spoken_text` (== result.spoken_text in
            # normal mode; shape-tags removed in annotation mode) so memory +
            # history never store [CIRCLE]/[ARROW] control tags — otherwise
            # recall() would re-inject coordinates into future prompts and
            # stale tags could resurface on non-annotation turns.
            self.sig_record_memory.emit(
                app_name,
                window_title,
                transcript,
                spoken_text,
                pointer_targets,
            )

            # T3-3: queue this exchange for spaced review. Written AFTER the interaction
            # has already succeeded, and every failure is swallowed -- a journal write must
            # never be able to spoil a turn the user already got value from.
            self._record_journal_entry(
                app_name, transcript, spoken_text, result, dbg)

            user_blocks: list[dict] = [{"type": "text", "text": transcript}]
            # T2-4: attach the cursor-screen capture so a follow-up like "what about that
            # button you pointed at?" reaches a model that can still see the screen.
            # Off unless HISTORY_IMAGE_COUNT > 0, so the default path is unchanged.
            if HISTORY_IMAGE_COUNT > 0 and cursor_capture is not None:
                try:
                    user_blocks.append(
                        _history_image_from_capture(cursor_capture)
                    )
                except Exception as exc:  # never fail a completed turn over history
                    dbg.log(f"HISTORY: image attach skipped - {exc}")
            self._history.append({"role": "user", "content": user_blocks})
            self._history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": spoken_text}],
            })
            if len(self._history) > _MAX_HISTORY_EXCHANGES * 2:
                self._history = self._history[-(
                    _MAX_HISTORY_EXCHANGES * 2
                ):]
            # Evict old images AFTER the exchange trim, so the 10-exchange text window is
            # untouched and only the far smaller image budget is enforced here. Text is
            # cheap and useful for a long time; screenshots are expensive and go stale.
            _evict_old_history_images(self._history, HISTORY_IMAGE_COUNT)

            # SHELL_AND_CHAT.md §4/§3. The answer reaches the panel and Home's Recent table.
            # Both after the turn has already succeeded, so neither can spoil it.
            #
            # The coordinate handed over is `result.coordinate` -- Space C, the model's own
            # declared-resolution space, which is also the space the stored screenshot is in.
            # That is what lets "point at that again" later re-run the exact same Space C ->
            # physical conversion instead of replaying a physical coordinate that was only
            # ever valid for one monitor arrangement.
            self._emit_chat_message(
                "nimbus", spoken_text,
                coordinate=result.coordinate,
                image=cursor_capture.image if cursor_capture is not None else None,
                privacy_skipped=not captures,
            )
            self._record_turn(
                app_name, transcript, spoken_text,
                target=result.element_label or "")

            dbg.log("DONE — interaction complete")
            _play_feedback_tone_async("done")

        except Exception as exc:
            if not cancel.is_set():
                self.sig_show_toast.emit("Nimbus couldn't complete that request. Please try again.", "error")
                _play_feedback_tone_async("error")
                dbg.log(f"ERROR: {type(exc).__name__}: {exc}")
                _log(f"ERROR: Pipeline failed — {type(exc).__name__}: {exc}")
                # The panel says so too. A conversation that simply stops after a question is
                # worse than one that admits the turn failed.
                self._emit_chat_message(
                    "system", "That request could not be completed.", error=str(exc))
        finally:
            # Always hide spinner on pipeline exit (success, error, cancel).
            # Prevents a stuck-spinning arc if anything above raises before
            # the normal hide_spinner emit fires.
            self.sig_hide_spinner.emit()
            self.sig_chat_state.emit("idle")
            dbg.close()

    # --- Signal slot handlers (run on Qt main thread) ---

    def _on_hide_overlay(self) -> None:
        if self._overlay:
            self._overlay.hide_for_capture()
        # SHELL_AND_CHAT.md §4 `S-7` fallback. On Windows before 19041 there is no
        # WDA_EXCLUDEFROMCAPTURE, so the HUD has to be hidden the old way or the model sees
        # its own previous answer rendered as UI and may point at it (Invariant 1). Both calls
        # are no-ops when exclusion is active, so they are made unconditionally rather than
        # behind a version check that could drift from the one the HUD actually made.
        if self._hud is not None:
            try:
                self._hud.hide_for_capture()
            except Exception as exc:
                _log(f"CHAT HUD: hide-for-capture skipped - {type(exc).__name__}: {exc}")

    def _on_show_overlay(self) -> None:
        if self._overlay:
            self._overlay.show_after_capture()
        if self._hud is not None:
            try:
                self._hud.show_after_capture()
            except Exception as exc:
                _log(f"CHAT HUD: show-after-capture skipped - {type(exc).__name__}: {exc}")

    def _on_point_at(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.point_at(physical_x, physical_y, monitor)

    def _on_show_toast(self, message: str, severity: str) -> None:
        """Main-thread bridge for non-blocking in-overlay error feedback."""
        if self._overlay and hasattr(self._overlay, "show_toast"):
            self._overlay.show_toast(message, severity)

    def _on_record_memory(
        self,
        app_name: str,
        window_title: str,
        question: str,
        response: str,
        pointer_targets: list,
    ) -> None:
        try:
            self._memory.record(
                app_name=app_name,
                window_title=window_title,
                user_question=question,
                model_response=response,
                pointer_targets=pointer_targets,
            )
        except Exception as exc:
            _log(f"ERROR: Memory record failed — {exc}")

    # LISTENING-state slot handlers (run on Qt main thread)

    def _on_show_waveform(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_waveform(physical_x, physical_y, monitor)

    def _on_hide_waveform(self) -> None:
        if self._overlay:
            self._overlay.hide_waveform()

    def _on_audio_level(self, level: float) -> None:
        if self._overlay:
            self._overlay.set_audio_level(level)

    # THINKING-state slot handlers (Qt main thread)

    def _on_show_spinner(self, physical_x: int, physical_y: int, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_spinner(physical_x, physical_y, monitor)

    def _on_hide_spinner(self) -> None:
        if self._overlay:
            self._overlay.hide_spinner()

    def _on_show_annotations(self, annotations: list, monitor: dict) -> None:
        if self._overlay:
            self._overlay.show_annotations(annotations, monitor)

    def _on_clear_annotations(self) -> None:
        if self._overlay:
            self._overlay.clear_all_annotations()

    # --- Session-history export (Qt main thread) ----------------------------

    def _export_session_history(self, export_dir: Path | None = None) -> Path:
        """Write the live conversation and current app's recent memory to Markdown.

        ``MemoryStore.recall`` remains the only way this feature reads
        persistent memory. The conversation itself is intentionally sourced
        from the current in-process ``_history`` list, which is reset when
        Nimbus restarts. ``export_dir`` is a test hook; production writes to
        the user's Documents folder.
        """
        exported_at = datetime.now()
        # Use the normal bounded recall API rather than reaching into the
        # markdown file directly. This keeps the memory module authoritative
        # for naming, truncation, and future storage changes.
        memory_context = self._memory.recall(self._current_app)

        lines = [
            "# Nimbus session history",
            "",
            f"Exported: {exported_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Current app: {self._current_app}",
            f"Window: {self._current_title or '(none)'}",
            "",
            "## Conversation",
            "",
        ]
        if self._history:
            for message in self._history:
                role = str(message.get("role", "assistant")).title()
                text = _history_message_text(message) or "(no text content)"
                lines.extend((f"### {role}", "", text, ""))
        else:
            lines.extend(("(No messages in this Nimbus session yet.)", ""))

        lines.extend((
            f"## Recent per-app memory ({self._current_app})",
            "",
            memory_context or "(No saved memory for this app yet.)",
            "",
        ))
        filename = f"nimbus-session-{exported_at.strftime('%Y%m%d-%H%M%S-%f')}.md"
        contents = "\n".join(lines)

        def write_to(destination: Path) -> Path:
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / filename
            path.write_text(contents, encoding="utf-8")
            return path

        destination = Path(export_dir) if export_dir is not None else SESSION_EXPORT_DIR
        try:
            return write_to(destination)
        except OSError as documents_error:
            # Explicit destinations are test/caller-controlled and should
            # surface their failure. The user-facing default gets a durable
            # fallback when Windows security software or a managed profile
            # denies writes to Documents.
            if export_dir is not None:
                raise
            fallback_path = write_to(SESSION_EXPORT_FALLBACK_DIR)
            _log(
                "WARN: Documents export unavailable "
                f"({type(documents_error).__name__}); saved to {fallback_path}"
            )
            return fallback_path

    def _on_export_session_history(self) -> None:
        """Export slot, invoked through ``sig_export_session_history`` only."""
        try:
            path = self._export_session_history()
            _log(f"Session history exported: {path}")
        except Exception as exc:
            _log(f"ERROR: Session-history export failed — {type(exc).__name__}: {exc}")

    # --- Release update check: removed -------------------------------------
    #
    # There was a `check_for_updates_async` here that hit the GitHub Releases API on a background
    # thread at startup and, on a newer tag, opened a modal `QMessageBox` offering to download it.
    # **Removed at the user's request**, and it deserved to go on its own merits:
    #
    # * it fired on first launch, so the very first thing a new user saw was a dialog about a
    #   different version of the thing they had just installed;
    # * "Open" sent them to a browser to download an installer by hand, which is a notification
    #   dressed up as an update mechanism -- it cannot actually update anything;
    # * it was an unannounced outbound network call from an app whose Settings screen says nothing
    #   leaves the machine. The claim is about *user data* and stays true, but a silent call home
    #   is a bad look for a tool making that promise.
    #
    # `updates.py` is kept, along with its tests: it is a pure "compare two version strings and
    # query a release feed" module with no UI, and it is what a real updater would be built on.
    # Nothing calls it now, which the drift guard in `tests/test_updates.py` asserts, so this
    # comment cannot quietly stop being true.


_T0 = __import__("time").time()


# --- single-instance mutex --------------------------------------
#
# Without this, double-clicking the installed shortcut spawns multiple
# Nimbus.exe processes. Each installs its own pynput.Listener (suppress=False
# is observe-only — multiple listeners coexist), so one Ctrl+Alt+Space press
# fires N parallel STT->Nimbus->TTS pipelines. User hears N overlapping
# voices answering one question.
#
# Pattern: Win32 named mutex acquired before QApplication construction.
# Whoever wins the kernel-level CreateMutexW race holds the mutex for their
# process lifetime; second instance sees ERROR_ALREADY_EXISTS and exits.
# Same pattern Spotify, Slack, Discord, Raycast all use.

_MUTEX_NAME = "Local\\NimbusWindows-SingleInstance-v1"
"""Per-logon-session namespace (Local\\) — admin and non-admin in the same
session see the same mutex (correct), but different Windows users on the
same machine each get their own Nimbus (also correct). Global\\ would
block a second user on a shared RDP host — wrong for this app."""

_ERROR_ALREADY_EXISTS = 183  # winerror.h ERROR_ALREADY_EXISTS

_SHOW_EVENT_NAME = "Local\\NimbusWindows-ShowWindow-v1"
"""Named auto-reset event that means "somebody launched Nimbus again -- show yourself".

## Why this exists

The mutex above stops a second process, which is right, but it left the launch itself doing
nothing useful: the user double-clicks the shortcut, gets a dialog explaining that Nimbus is
already running, and is told to go and find a tray icon. Every other tray application in the
world raises its window instead, because clicking a shortcut *is* the request to see the app.

## Why an event and not a socket or a window message

It is two ctypes calls and no new dependency. ``QLocalServer`` would work but drags in QtNetwork
and a socket file that needs cleaning up after a crash; ``FindWindow`` + ``PostMessage`` needs a
stable window title, and ours is not a promise we want to make. A named kernel event is released
by Windows when the process dies, which is the same property that makes the mutex reliable.

``Local\\`` for the same reason as the mutex: per logon session, so two users on one machine do
not reach into each other's Nimbus."""

_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102


def _signal_existing_instance(kernel32=None) -> bool:
    """Ask the running Nimbus to show its window. ``True`` if the request was delivered.

    ``False`` means there was nothing listening -- an older build with no event, or a first
    instance that failed to create one. The caller then falls back to explaining itself, because
    exiting silently after a double-click would look like a crash.

    ``kernel32`` is a DI hook for tests, matching ``_acquire_single_instance_mutex``.
    """
    if kernel32 is None:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, _SHOW_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    except Exception:
        return False
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass


def _watch_for_show_requests(on_request, kernel32=None, should_stop=None) -> bool:
    """Create the show-window event and call ``on_request`` whenever a later launch sets it.

    Returns ``True`` if the watcher started. A failure here is not worth reporting: it costs the
    convenience of raising the window on a second launch and nothing else, and the tray icon is
    still there.

    ``on_request`` is called from this thread, so it must be a signal emit and not a widget call.

    The wait uses a timeout rather than ``INFINITE`` purely so ``should_stop`` can be honoured in
    tests; a daemon thread blocked forever would be fine in production but untestable.
    """
    if kernel32 is None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    # bManualReset=False: auto-reset, so each launch is one request and the wait re-arms itself.
    # bInitialState=False, or we would show the window once at startup for nobody.
    handle = kernel32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)
    if not handle:
        _log("SHELL: could not create the show-window event; a second launch will explain itself")
        return False

    def worker() -> None:
        while should_stop is None or not should_stop():
            try:
                result = kernel32.WaitForSingleObject(handle, 500)
            except Exception as exc:
                _log(f"SHELL: show-window watcher stopped - {type(exc).__name__}: {exc}")
                return
            if result == _WAIT_OBJECT_0:
                try:
                    on_request()
                except Exception as exc:
                    _log(f"SHELL: show-window request failed - {type(exc).__name__}: {exc}")
            elif result != _WAIT_TIMEOUT:
                _log(f"SHELL: show-window watcher stopped (wait returned {result})")
                return

    threading.Thread(target=worker, daemon=True, name="nimbus-show-window").start()
    return True


def _acquire_single_instance_mutex(kernel32=None):
    """Try to acquire the named mutex. Returns the HANDLE (truthy int) if
    we are the first instance, ``None`` if another Nimbus already owns it,
    or the string ``"fail-open"`` on rare CreateMutexW genuine failure (in
    which case caller should proceed with startup — better to risk a
    duplicate than block the user with a broken installer).

    The ``kernel32`` parameter is a DI hook for tests (pass a MagicMock).
    Production passes ``None`` and the function looks up the real
    ``ctypes.windll.kernel32`` itself, applying the explicit ``restype`` /
    ``argtypes`` signatures that prevent x64 HANDLE truncation (without
    them, ctypes defaults to ``c_int`` = 32-bit, which silently corrupts
    64-bit handles on x64 Windows).

    The returned handle MUST be retained for the process lifetime (a
    module-global reference is sufficient). The Windows kernel auto-
    releases the mutex when the process terminates — including on crash
    or Task Manager kill — so no explicit cleanup is needed at shutdown.
    """
    if kernel32 is None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    # bInitialOwner=False — for single-instance detection we need the
    # kernel object's *existence* as a flag, not ownership/synchronization
    # semantics. Setting True would make first instance pointlessly own a
    # mutex it never releases.
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    # Note: ctypes maps c_void_p NULL to Python None (NOT integer 0). Test
    # mocks use return_value=0 for convenience; both are falsy so `not handle`
    # handles both representations safely.
    if not handle:
        # Genuine CreateMutexW failure (rare). Fail open — don't block startup.
        return "fail-open"
    # GetLastError MUST be the next Win32 call after CreateMutexW; any
    # intervening kernel32 call could clobber the thread-local last-error.
    # The `if not handle` branch above is pure Python — safe.
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        # Another Nimbus owns the mutex. Close OUR handle to the same
        # kernel object (the original mutex is still held by the first
        # instance) so we don't leak.
        kernel32.CloseHandle(handle)
        return None
    return handle


# --- interaction audio cues (lazy-built + async playback) ----------

_CHIME_SAMPLE_RATE = 44100
_FEEDBACK_TONES: dict[str, object] = {}
"""Cache of generated tone buffers, keyed by cue kind. Built on first play so
startup pays nothing and the numpy/sounddevice cold path is amortised."""


def _feedback_tone_spec(kind: str) -> tuple[tuple[float, ...], float]:
    """Distinct, short cues for listening, success, and recoverable errors."""
    specs = {
        "listening": ((880.0,), 0.060),
        "done": ((659.0, 880.0), 0.110),
        "error": ((330.0, 247.0), 0.140),
    }
    return specs.get(kind, specs["listening"])


def _play_feedback_tone_async(kind: str) -> None:
    """Play a generated, non-blocking interaction cue without external assets.

    ``sounddevice.play()`` returns immediately, so the press handler is never
    blocked. Every failure is swallowed **deliberately**: audio cues are UX-only,
    so a missing or busy output device must never break push-to-talk. An unknown
    ``kind`` falls back to the listening cue rather than raising.
    """
    try:
        import numpy as _np
        import sounddevice as _sd
        samples = _FEEDBACK_TONES.get(kind)
        if samples is None:
            freqs, duration_s = _feedback_tone_spec(kind)
            frames_per_note = int(_CHIME_SAMPLE_RATE * duration_s / len(freqs))
            pieces = []
            for freq in freqs:
                t = _np.linspace(0.0, duration_s / len(freqs), frames_per_note, endpoint=False)
                envelope = _np.exp(-t * 30.0)
                pieces.append(_np.sin(2.0 * _np.pi * freq * t) * envelope * 0.22)
            samples = _np.concatenate(pieces).astype(_np.float32)
            _FEEDBACK_TONES[kind] = samples
        _sd.play(samples, _CHIME_SAMPLE_RATE)
    except Exception:
        pass


def _log(msg: str) -> None:
    """Print a log line with millisecond-precision elapsed time."""
    import time
    elapsed = (time.time() - _T0) * 1000
    ts = time.strftime("%H:%M:%S")
    print(f"[nimbus {ts} +{elapsed:.0f}ms] {msg}", flush=True)


def _run_startup_licence_gate(qt_app) -> bool:
    """Ask for a licence if this machine has none. ``False`` means Nimbus must not run.

    ## Why this is a function

    It was eleven lines inline in ``__main__``, and inline meant untestable, and untestable meant
    nobody noticed that it referenced ``theme`` before ``theme`` was imported. The gate's caller
    catches ``Exception`` so that a licence bug never locks out a legitimate user -- correct policy,
    and it turned a one-word ``NameError`` into "the licence gate silently does not exist". Every
    build ever shipped started unlicensed and never showed the activation screen.

    Two lessons are encoded here rather than written in a comment somewhere:

    * the gate is a function, so ``tests/test_app.py`` can execute the real body and a missing name
      fails a test instead of disappearing into an ``except``;
    * every import it needs is **local to it**, so it cannot depend on the order of unrelated lines
      in ``__main__`` again.

    Returns ``True`` when Nimbus may run, which includes the common case of an already-licensed
    machine -- ``run_activation_flow`` then shows nothing and makes no network call.
    """
    import theme

    import licensing
    from activation_dialog import run_activation_flow

    # The stylesheet goes on before the dialog, not after: the gate is the first thing a new user
    # sees, and unstyled Qt is a bad first impression of a designed application.
    qt_app.setStyleSheet(theme.build_qss())

    if not run_activation_flow(licensing):
        return False

    state = licensing.current_state()
    _log(f"LICENCE: {state.kind} - {state.detail}")
    return True


def _record_licence_gate_failure(exc: BaseException) -> None:
    """Write a skipped licence gate somewhere a windowed build can actually be asked about.

    This exists because the ``NameError`` above hid for the life of the product. ``_log`` prints to
    stdout; the shipped EXE is built ``console=False``, so its stdout goes nowhere and the single
    line reporting that the gate had been skipped was unreadable on every machine that had the bug.

    A file in the data directory costs nothing, is not shown to the user, and turns "the gate never
    appears and we do not know why" into one question we can ask someone to answer.
    """
    try:
        import time
        from pathlib import Path

        path = Path(os.path.expanduser("~")) / ".nimbus" / "licence-gate-error.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"{type(exc).__name__}: {exc}\n"
            )
    except Exception:
        # A diagnostic that cannot be written must never be the reason Nimbus fails to start.
        pass


# --- TTS provider resolution --------------------------------------


def _resolve_tts_credentials() -> tuple[str, str | None]:
    """Resolve (TTS_PROVIDER, api_key_for_that_provider) at startup.

    Reads TTS_PROVIDER via config.resolve_setting (env→keyring→default)
    then resolves the right API key via config.resolve_api_key based on
    the selected provider. Returned to __main__ which dispatches via
    tts.create_tts_client(provider, api_key).
    """
    provider = resolve_setting("TTS_PROVIDER", default="cartesia")
    if provider == "kokoro":
        return provider, ""  # local offline TTS, no API key
    if provider == "elevenlabs":
        api_key = resolve_api_key("ELEVENLABS_API_KEY")
    else:
        api_key = resolve_api_key("CARTESIA_API_KEY")
    return provider, api_key


def _resolve_stt_credentials() -> tuple[str, str]:
    """Resolve (STT_PROVIDER, api_key) at startup. Mirrors
    _resolve_tts_credentials. faster-whisper is local offline (no key).
    Resolved fresh (env→keyring) so a Settings change is honored on restart.
    """
    provider = resolve_setting("STT_PROVIDER", default="assemblyai")
    if provider in ("faster-whisper", "local"):
        return "faster-whisper", ""
    return "assemblyai", resolve_api_key("ASSEMBLYAI_API_KEY") or ""


def _openrouter_key() -> str:
    """Any OpenRouter (sk-or-) key the user pasted in ANY LLM slot.

    One OpenRouter key works for every model namespace (anthropic/, openai/,
    google/), so a power user pastes it once and it is reused across all LLM
    providers (cache + reuse). Direct provider keys (sk-ant-, sk-..., Google
    AIza) are NOT reused here — they only authenticate their own provider, so
    each is matched by its own slot. create_ai_client then routes an sk-or-
    key to OpenRouter and a direct key to that provider's native endpoint.
    """
    for k in (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY):
        if (k or "").startswith("sk-or-"):
            return k
    return ""


def _resolve_llm_credentials() -> tuple[str, str]:
    """Resolve (effective_model_id, api_key) at startup based on LLM_PROVIDER.

    Reads LLM_PROVIDER via config.resolve_setting (env→keyring→default), with
    the default supplied by config.DEFAULT_LLM_PROVIDER so every call site
    agrees (T0-2).
    - LLM_PROVIDER='ollama'    → returns ("ollama/<OLLAMA_MODEL_VISION>", ""),
                                  api_key empty because local Ollama is
                                  unauthenticated. create_ai_client factory
                                  routes `ollama/*` prefix to OllamaClient.
    - LLM_PROVIDER='anthropic' → returns ("anthropic/<ANTHROPIC_MODEL>",
                                  ANTHROPIC_API_KEY). Factory routes the
                                  'anthropic/' prefix to AnthropicClient, which
                                  adapts the slug per endpoint (T0-1).
    - any other value          → falls back to the anthropic branch
                                  (forward-compat). Note this is the fallback
                                  for an *unrecognised* value; the fallback for
                                  an *unset* value is DEFAULT_LLM_PROVIDER.

    Without this helper the Settings dropdown was cosmetic: LLM_PROVIDER='ollama' got persisted to
    keyring but app.py only ever read MODEL_ID, so the user's choice was
    silently ignored and AnthropicClient was always constructed with whatever
    MODEL_ID env var defaulted to.

    Note: MODEL_ID env var takes precedence over LLM_PROVIDER ONLY when
    MODEL_ID already routes to a non-Anthropic prefix (the factory dispatches
    on MODEL_ID prefix first). For the GUI-flow (user clicks Ollama in the
    dropdown), LLM_PROVIDER='ollama' is sufficient — they never need to know
    about MODEL_ID.
    """
    provider = resolve_setting("LLM_PROVIDER", default=DEFAULT_LLM_PROVIDER)
    if provider in ("openai", "openai-realtime"):
        # OpenAI native GPT-4o vision. 'openai/' prefix routes
        # create_ai_client → OpenAIVisionClient. Pointing accuracy is refined
        # via the grid-locator (GPT-4o is weaker at raw pixel coords than
        # Nimbus). For 'openai-realtime', the GPT-Realtime speech-to-speech
        # session runs as a parallel pipeline (see _setup_realtime); the main
        # ai_client built here is a valid GPT-4o client used by the realtime
        # path's grid-locator refinement, harmless otherwise.
        # Direct OpenAI key (sk-...) → api.openai.com; an OpenRouter key →
        # OpenRouter (create_ai_client routes by prefix). If the OpenAI slot is
        # empty, reuse a cached OpenRouter key so one sk-or- key serves OpenAI too.
        return f"openai/{OPENAI_MODEL_VISION}", OPENAI_API_KEY or _openrouter_key()
    if provider == "gemini-native":
        # T1-1: explicit native path. Uses the bare model name (no google/ prefix)
        # because the native SDK rejects a namespaced slug. Requires a direct
        # Google key; create_ai_client raises an actionable error otherwise.
        from config import DEFAULT_GEMINI_NATIVE_MODEL
        native_model = resolve_setting(
            "GEMINI_NATIVE_MODEL", default=DEFAULT_GEMINI_NATIVE_MODEL
        )
        return f"gemini/{native_model}", GEMINI_API_KEY or ""
    if provider == "gemini":
        # Gemini routes via OpenRouter — the SAME endpoint Nimbus uses. Reuse
        # the user's OpenRouter (sk-or-) key from the Anthropic slot if no
        # separate GEMINI_API_KEY is set, so there's no extra key to enter
        # (minimal-UX). Pointing is refined by the grid-locator.
        # Own slot first (a direct Google AI Studio key, or a per-Gemini
        # OpenRouter key), else reuse any cached OpenRouter key so one sk-or-
        # key serves Gemini too. create_ai_client routes by key prefix.
        return GEMINI_MODEL_VISION, GEMINI_API_KEY or _openrouter_key()
    if provider == "ollama":
        # log detected Ollama version + warn
        # about model/version mismatches at startup. Stderr only — the
        # Settings dialog catches this case interactively. This is
        # belt-and-suspenders for users who set OLLAMA_MODEL_VISION via
        # env var and never touch the Settings UI.
        try:
            from ollama_health import (
                check_model_compatibility,
                detect_ollama_version,
            )
            version = detect_ollama_version(OLLAMA_HOST)
            if version is None:
                print(
                    f"[ollama] could not reach {OLLAMA_HOST}/api/version "
                    "— is `ollama serve` running?",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[ollama] detected version {version}, "
                    f"using model {OLLAMA_MODEL_VISION}",
                    file=sys.stderr,
                )
                warning = check_model_compatibility(OLLAMA_MODEL_VISION, version)
                if warning:
                    print(f"[ollama] WARNING: {warning}", file=sys.stderr)
        except Exception as exc:
            # Don't fail startup over a logging helper.
            print(f"[ollama] version-check skipped: {exc}", file=sys.stderr)

        # Construct an ollama/ prefixed model id so create_ai_client routes
        # correctly. api_key is empty (Ollama is unauthenticated local).
        return f"ollama/{OLLAMA_MODEL_VISION}", ""
    # anthropic (default): the Settings model dropdown persists
    # ANTHROPIC_MODEL — honor it. An explicitly-set MODEL_ID env var still
    # takes precedence (advanced override).
    ant_key = ANTHROPIC_API_KEY or _openrouter_key()
    if os.getenv("MODEL_ID"):
        return MODEL_ID, ant_key
    # T0-1: the default was "model-sonnet-4-6", a placeholder left behind by an
    # earlier find-and-replace, so every Anthropic request failed. The canonical
    # form stored here is the NATIVE dash-versioned id; AnthropicClient converts
    # it to OpenRouter's dot-versioned slug when routing there.
    ant_model = resolve_setting("ANTHROPIC_MODEL", default=DEFAULT_ANTHROPIC_MODEL)
    return f"anthropic/{ant_model}", ant_key


_SETTINGS_ALSO_IN_SETTINGS_DIALOG = (
    "LLM_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER",
    "GEMINI_NATIVE_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL_VISION",
    "OLLAMA_MODEL_VISION", "GEMINI_MODEL_VISION",
    "CODE_EXECUTION", "SEARCH_GROUNDING", "AGENTIC_VISION", "GEMINI_LIVE",
    "GEMINI_LIVE_MODEL", "GROUNDING_REFINEMENT", "KB_CACHE", "ANNOTATION_MODE",
)
"""Settings a user can change in the Settings dialog AND pin in ``.env``."""


def _log_env_pinned_settings() -> None:
    """Warn when ``.env`` is overriding a setting the Settings dialog also controls.

    ``resolve_setting`` resolves env -> keyring -> default, so a value in ``.env`` wins
    over anything chosen in the dialog. Worse, it *writes the env value back into the
    keyring*, so the dialog's stored choice is actively overwritten on every launch.

    From the user's side this looks like the dialog being broken: pick a model, restart,
    and it has silently reverted. That exact confusion cost a debugging session, so the
    precedence is now stated at startup rather than left to be inferred. The precedence
    itself is deliberate and unchanged -- ``.env`` pinning is genuinely useful for
    development -- it just needs to be visible.
    """
    pinned = [n for n in _SETTINGS_ALSO_IN_SETTINGS_DIALOG if os.getenv(n)]
    if not pinned:
        return
    _log(
        f"NOTE: {len(pinned)} setting(s) pinned in .env and will OVERRIDE the Settings "
        f"dialog: {', '.join(pinned)}"
    )
    _log("      Remove them from .env if you want to change them from Settings.")


def _should_connect_stt(realtime) -> bool:
    """Whether to open the AssemblyAI STT mic at startup.

    FALSE in realtime mode: GPT-Realtime owns the 24 kHz mic (realtime.py
    start_turn opens its own RawInputStream). Opening the 16 kHz AssemblyAI
    STT mic too is the 'two-mic bug' — both grab the input device and the
    realtime path produces no audio. So in realtime mode we skip STT entirely.
    """
    return realtime is None


# --- SHELL_AND_CHAT.md integration (§9.1's single integration pass) -----------
#
# Module-level rather than closures inside ``__main__`` so the wiring is testable without
# launching the app. That matters more here than anywhere else in this file: this is the code
# that decides whether the window, the tray and the hotkey agree about push-to-talk, and
# "it looked right when I ran it" is not a check.


def build_chat_hud(nimbus) -> object | None:
    """Construct the chat HUD, connect it to ``nimbus``, and return it (or ``None``).

    Returns ``None`` when ``CHAT_HUD`` is off, or when anything at all goes wrong. The HUD is
    a view of a conversation that happens with or without it, so a failure here must cost the
    user their chat panel and nothing else (Invariant 10).

    Sessions are pruned once at startup rather than on a timer: the retention promise is
    "older than N days is gone", and a process that may run for weeks would otherwise honour
    it only at the moment it happened to start.

    **Built even when ``CHAT_HUD`` is off**, hidden and not auto-revealing. Gating construction on
    the setting is what made Home's switch answer "not until you restart", which is not a switch.
    """
    try:
        import sessions
        from chat_hud import ChatHud

        store = sessions.SessionStore()
        try:
            removed = store.prune()
            if removed:
                _log(f"CHAT HUD: pruned {removed} session(s) past the retention window")
        except Exception as exc:
            # Retention is housekeeping. Failing it must not stop the panel from opening.
            _log(f"CHAT HUD: prune skipped - {type(exc).__name__}: {exc}")

        hud = ChatHud(store=store)
        # The setting decides whether the panel appears on its own, not whether it exists. Off, it
        # sits hidden with a live transcript, and Home's switch or Ctrl+Alt+H brings it up with
        # everything that was said while it was away already in it.
        hud.set_auto_reveal(CHAT_HUD_ENABLED)
        nimbus._sessions = store
        nimbus._hud = hud
        if not CHAT_HUD_ENABLED:
            _log("CHAT HUD: built but hidden (CHAT_HUD=off) - the Home switch shows it live")

        # Inbound, through the HUD's own signals rather than its methods. They are the
        # documented entry point and they are already connected internally to append /
        # stream_delta / set_state, so going via them keeps one thread-safety contract instead
        # of two paths that could diverge.
        nimbus.sig_chat_message.connect(hud.sig_message)
        nimbus.sig_chat_delta.connect(hud.sig_delta)
        nimbus.sig_chat_state.connect(hud.sig_state)

        # Outbound. Each one is a thing only NimbusApp can do.
        hud.sig_replay.connect(nimbus._tts.speak)
        hud.sig_repoint.connect(nimbus.repoint_at)
        hud.sig_retry.connect(nimbus.retry_transcript)
        hud.sig_new_session.connect(lambda: nimbus.start_new_chat())
        hud.sig_open_session.connect(nimbus.open_chat_session)

        nimbus.start_new_chat()
        # Deliberately **not** logging the capture-exclusion state here. Measured during the
        # integration smoke test: exclusion is applied when the window is first shown, so at
        # build time ``needs_hide_for_capture()`` is always True and a log line based on it
        # claimed "capture exclusion unavailable" on every launch, including machines where it
        # works. The HUD reports the truth itself once it has a window handle, and
        # ``_on_hide_overlay`` calls the fallback unconditionally because it is a no-op when
        # exclusion is live.
        return hud
    except Exception as exc:
        _log(f"CHAT HUD: not started - {type(exc).__name__}: {exc}")
        nimbus._hud = None
        return None


def _licence_state_provider():
    """A callable the Account page can poll for licence state, or ``None`` if unavailable.

    Returns ``None`` rather than a callable that raises when ``licensing`` cannot be imported, so the
    page falls back to its honest "activation is not set up" text instead of showing an error for
    something the user did not do.
    """
    try:
        import licensing
    except Exception:
        return None
    return licensing.current_state


def build_main_window(nimbus) -> object | None:
    """Construct the application shell, wire it to ``nimbus``, and return it (or ``None``).

    Every data source reaches the window as a callable and every action leaves as a signal, so
    this function is the whole seam: there is no ``import app`` anywhere in ``shell/``.

    A failure returns ``None`` and the app keeps running as a tray utility. That is why
    ``__main__`` gives the tray a Settings fallback in that case -- without a window, Settings
    would otherwise be unreachable.
    """
    try:
        from shell.window import MainWindow

        window = MainWindow(
            listening_provider=lambda: nimbus.is_listening,
            hotkey_provider=lambda: resolve_setting("HOTKEY", HOTKEY),
            usage_provider=nimbus.questions_this_week,
            privacy_provider=nimbus.screenshots_skipped_this_week,
            recent_provider=nimbus.recent_turns,
            chat_visible_provider=lambda: nimbus.is_chat_visible,
            # Only when the journal is on. Handing over a queue the user disabled would build
            # the table they opted out of, and the page's empty state is the honest answer.
            review_queue_provider=(lambda: nimbus._journal) if JOURNAL_ENABLED else None,
            licence_provider=_licence_state_provider(),
        )

        # `S-3`: the window asks, NimbusApp writes, and sig_listening_changed tells every view.
        window.sig_set_listening.connect(nimbus.set_listening)
        nimbus.sig_listening_changed.connect(window.set_listening)

        # The chat panel switch, on the same pattern. `sig_chat_visible_changed` matters more than
        # it looks: Ctrl+Alt+H and the 45s auto-hide both move the panel without the window
        # knowing, so the switch is refreshed from `is_chat_visible` on every page change too.
        window.sig_set_chat_visible.connect(nimbus.set_chat_visible)
        nimbus.sig_chat_visible_changed.connect(window.set_chat_visible)

        # §5's two account actions. Both are real now that `licensing` exists; they were disabled
        # placeholders while it did not.
        window.sig_deactivate_device.connect(nimbus.deactivate_device)
        window.sig_sign_out.connect(nimbus.sign_out_licence)

        window.sig_quiz_me.connect(nimbus.start_review)
        window.sig_export_history.connect(nimbus.sig_export_session_history.emit)
        window.sig_open_memory_folder.connect(nimbus.open_memory_folder)

        # Coerced to ``str`` deliberately. ``model_id`` is a plain string on every client
        # today, but it is provider-supplied, and a ``None`` or a non-string reaching
        # ``QLabel.setText`` raises a TypeError that would take the whole window down -- a
        # cosmetic label is not worth that. Caught by the integration tests, which pass a mock.
        model_name = getattr(nimbus._ai, "model_id", None)
        window.set_provider(
            str(resolve_setting("LLM_PROVIDER", DEFAULT_LLM_PROVIDER)),
            str(model_name) if model_name else type(nimbus._ai).__name__,
        )
        window.set_privacy_guard(
            resolve_setting("PRIVACY_GUARD", "on").strip().lower() == "on")
        window.set_local_mode(_is_fully_local())
        nimbus._window = window
        return window
    except Exception as exc:
        _log(f"SHELL: window not built - {type(exc).__name__}: {exc}")
        nimbus._window = None
        return None


def _is_fully_local() -> bool:
    """Whether nothing in the pipeline leaves the machine.

    All three providers must be local, not just the model. A local LLM with cloud STT still
    sends the user's voice off the machine, and a sidebar dot claiming "local only" on that
    basis would be worse than no dot at all.
    """
    return (
        resolve_setting("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower() == "ollama"
        and resolve_setting("STT_PROVIDER", "assemblyai").strip().lower() == "faster-whisper"
        and resolve_setting("TTS_PROVIDER", "cartesia").strip().lower() == "kokoro"
    )


def _run_selftest() -> None:
    """Import every runtime module without starting Qt, audio, or network I/O.

    Used by the frozen build check: a successful run proves PyInstaller
    included Nimbus's Python modules, native extensions, and their dependent
    DLLs without requiring a tray, microphone, API key, or display session.
    """
    import importlib

    # The distributed app is a windowed (console=False) executable so normal
    # tray launches never flash a console. For this explicit CLI-only check,
    # attach to the invoking PowerShell/cmd console and recreate stdout so
    # `Nimbus.exe --selftest` visibly reports its result.
    if getattr(sys, "frozen", False):
        try:
            ctypes.windll.kernel32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except OSError:
            pass

    runtime_modules = (
        "ai", "stt", "tts", "overlay", "memory", "kb", "capture",
        "hotkey", "realtime", "settings_dialog", "onboarding", "tray", "config",
        "updates", "version",
        # T1-1/T1-4/T1-6a: native Gemini path plus its two satellites. Listed so a
        # frozen build fails the selftest here rather than at a user's first Gemini
        # interaction. gemini_cache and gemini_live are only imported lazily at the
        # point of use, so without this the selftest would never touch them.
        "gemini_native", "gemini_cache", "gemini_live",
        "annotations", "locator", "debug_log", "ollama_health",
        # T2-1 / T2-5 / T3-3. All three are imported lazily at the point of use, so they
        # are invisible to PyInstaller's static graph without this.
        "privacy", "prompts", "review", "theme", "brand",
        # SHELL_AND_CHAT.md §3/§4. The HUD is built only when CHAT_HUD is on and the shell
        # only when the window is first needed, so both are lazy in practice. Each shell
        # page is named because shell/__init__.py resolves MainWindow through __getattr__,
        # which no static import graph can follow.
        "chat_hud", "sessions",
        # SHELL_AND_CHAT.md §5. The gate runs before QApplication, so a frozen build that shipped
        # without these would fail at the licence check -- the very first thing a new user hits.
        "licensing", "activation_dialog",
        "shell", "shell.window", "shell.nav", "shell.titlebar", "shell.widgets",
        "shell.pages", "shell.pages.home", "shell.pages.knowledge",
        "shell.pages.journal", "shell.pages.settings", "shell.pages.account",
    )
    failures: list[str] = []
    for module_name in runtime_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            # Collected rather than raised on the first miss. A frozen build is usually missing
            # *several* related modules -- a whole package's worth -- and stopping at the first
            # means as many rebuild cycles as there are gaps.
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    result = "SELFTEST OK" if not failures else "SELFTEST FAILED\n" + "\n".join(failures)
    print(result, flush=True)

    # Also to a file, when asked.
    #
    # A frozen build's stdout is not capturable: the console attach above reopens it on `CONOUT$`,
    # which bypasses pipes and redirects entirely. So `Nimbus.exe --selftest | ...` prints to the
    # terminal and gives the caller nothing -- verified while building this. The exit code is
    # still the contract; this is for a build script that wants to *report* what failed.
    log_path = os.getenv("NIMBUS_SELFTEST_LOG")
    if log_path:
        try:
            Path(log_path).write_text(result + "\n", encoding="utf-8")
        except OSError:
            pass  # a diagnostic that cannot be written must not change the exit code

    if failures:
        sys.exit(1)


# --- Manual entry point -------------------------------------------------------

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _run_selftest()
        sys.exit(0)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    # Single-instance check — MUST run before QApplication construction
    # so a duplicate-launch exits fast without spinning up Qt / SDKs.
    # The handle is assigned to a __main__-module binding to keep it
    # alive for the process lifetime; Windows auto-releases on exit.
    _mutex_handle = _acquire_single_instance_mutex()
    if _mutex_handle is None:
        # Another Nimbus is already running. First try to *do what the user asked*: bring that
        # instance's window up and leave quietly. Launching a shortcut is a request to see the
        # application, and the old behaviour answered it with a dialog about where to find a tray
        # icon -- technically informative, practically a dead end, and the reason "it does not pop
        # up, I have to go and click the icon" was a fair description of the product.
        if _signal_existing_instance():
            sys.exit(0)

        # Nothing listening: an older build, or the first instance could not create the event.
        # Explain, rather than exiting silently, which after a double-click looks like a crash.
        # Win32 messagebox (no Qt dependency). MB_ICONINFORMATION = 0x40.
        # Copy fixed twice over. It used to say "look for the blue cursor icon" -- the icon has
        # been the orange Nimbus mark since `tools/make_icons.py` regenerated it, so the message
        # was describing a version that no longer exists and sent people hunting for the wrong
        # thing. It also named a "Settings and Quit menu" the tray no longer has: `S-5` trimmed it
        # to Show Nimbus / Pause / Quit, and Settings moved into the window.
        #
        # It now also says which build is talking, because this dialog is exactly what a user sees
        # when an older Nimbus is sitting in the tray and they launch a newer one: the new process
        # refuses to start, and every test they then run is against the old build. Naming the
        # version turns a confusing dead end into a diagnosis.
        # `APP_VERSION`, not `__version__`. This imported a name `version.py` has never defined, so
        # the `except` always fired and the line that exists to name the build always said
        # "unknown" -- a diagnostic that silently stopped diagnosing.
        try:
            from version import APP_VERSION as _running_version
        except Exception:
            _running_version = "unknown"
        ctypes.windll.user32.MessageBoxW(
            None,
            "Nimbus is already running.\n\n"
            "Look for the orange Nimbus icon in your system tray, at the bottom-right of "
            "your screen \u2014 click it to open the window, or right-click it to pause or "
            "quit.\n\n"
            "If you were expecting a newer version: the copy already running is the one that "
            "stays. Quit it from the tray first, then start this one again.\n\n"
            f"This copy is Nimbus {_running_version}.",
            "Nimbus already running",
            0x40,
        )
        sys.exit(0)
    # _mutex_handle == "fail-open" or a real handle: proceed with startup.

    print("=" * 70)
    print("Nimbus — push-to-talk AI buddy")
    print("=" * 70)

    # Qt must own process DPI awareness. QApplication sets Windows'
    # Per-Monitor-V2 context during construction; setting the legacy shcore
    # DPI mode first makes Qt log E_ACCESSDENIED and prevents V2.
    qt_app = QApplication(sys.argv)

    # --- the licence gate (SHELL_AND_CHAT.md §5 `S-10`) --------------------
    #
    # Here, and no later. §5 is explicit: **before the hotkey listener installs and before the mic
    # opens.** An unlicensed instance should consume no devices and register no global hooks, which
    # is both correct behaviour and the reason this cannot live inside `NimbusApp.__init__`.
    #
    # After `QApplication` only because the gate is a Qt dialog and needs one. It costs a licensed
    # user nothing: `run_activation_flow` returns immediately, with no dialog and no network call,
    # when the cached licence is valid.
    #
    # A failure to *evaluate* the licence is deliberately not a lockout. If `licensing` raises --
    # a corrupt keyring, a missing dependency in a bad build -- Nimbus starts. The alternative is a
    # paying tester locked out by our bug, and §0.1 is clear that this is deterrence, not
    # enforcement: the failure mode should favour the honest user every time.
    #
    # The work is in `_run_startup_licence_gate` rather than inline **because inline could not be
    # tested**, and that is not a stylistic point: this block spent its whole life raising
    # `NameError: name 'theme' is not defined` -- `theme` is imported 200 lines further down -- so the
    # blanket `except` below swallowed it and the gate never ran, in any build ever shipped. A
    # windowed EXE has no stdout, so the one line that said so went nowhere. See the function.
    try:
        if not _run_startup_licence_gate(qt_app):
            _log("LICENCE: activation declined - exiting before any device is claimed.")
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        _log(f"LICENCE: gate skipped - {type(exc).__name__}: {exc}")
        _record_licence_gate_failure(exc)

    # App-level icon — used by Qt for any window that doesn't set its
    # own (overlay, future dialogs). Belt-and-suspenders alongside
    # nimbus.spec's `icon=` (which embeds the icon as a Windows EXE
    # resource for taskbar/Alt-Tab/etc). Path resolved via __file__
    # so it works in both dev and bundled EXE.
    import brand as _brand
    _app_icon = _brand.window_icon()
    if not _app_icon.isNull():
        qt_app.setWindowIcon(_app_icon)
    # Tray-only mode: closing the overlay (or any internal window)
    # must NOT exit the app — only the Quit menu item should.
    qt_app.setQuitOnLastWindowClosed(False)

    # First-launch / missing-keys flow: show modal until all 3 keys
    # are saved. Modal blocks the QApplication.exec() loop so this
    # is synchronous from main()'s perspective.
    # T3-2: seed the knowledge-base guide. config._resolve_kb_dir creates the folder but
    # nothing explained it, so a new user saw an empty directory and never discovered that a
    # file must be named to match the .exe. Writes only when absent, never overwrites the
    # user's own edits, and a failure here is ignored -- it is help text, not functionality.
    try:
        kb.ensure_guide()
    except Exception as exc:
        _log(f"KB: guide not written ({type(exc).__name__}: {exc})")

    from settings_dialog import SettingsDialog, required_keys_present
    if not required_keys_present():
        print("First-launch setup — showing API key dialog...")
        dlg = SettingsDialog()
        if dlg.exec() != dlg.DialogCode.Accepted:
            print("Setup cancelled by user. Exiting.")
            sys.exit(1)
        # Sanity check — Save was clicked AND all 3 keys are now resolvable.
        if not required_keys_present():
            print(
                "ERROR: Setup completed but at least one API key still "
                "missing. Aborting."
            )
            sys.exit(1)

    # A fuller first-run explanation follows setup and is independent from the
    # short tray balloon. It is deliberately non-destructive: dismissing it
    # merely postpones the reminder until the next launch.
    from config import mark_welcome_seen, welcome_seen
    if not welcome_seen():
        from onboarding import WelcomeDialog
        welcome = WelcomeDialog(HOTKEY)
        if welcome.exec() == welcome.DialogCode.Accepted:
            mark_welcome_seen()

    # Resolve keys AFTER the modal has run — module-level constants
    # were captured at import time and may not reflect newly-saved
    # values. config.resolve_api_key() always reads fresh.
    api_anthropic = resolve_api_key("ANTHROPIC_API_KEY")
    api_assemblyai = resolve_api_key("ASSEMBLYAI_API_KEY")

    # resolve effective LLM model + api key based on LLM_PROVIDER
    # setting (Settings dialog dropdown). Reads keyring fresh so any change
    # the user just made in the modal is honored. See _resolve_llm_credentials
    # docstring for the Anthropic vs Ollama dispatch logic.
    _llm_model_id, _llm_api_key = _resolve_llm_credentials()

    # Local STT (faster-whisper) is opt-in via STT_PROVIDER; default AssemblyAI.
    # Resolved fresh so a Settings change is honored without a code edit.
    _stt_provider, _stt_key = _resolve_stt_credentials()

    # dispatch TTS subclass based on TTS_PROVIDER setting.
    # Cartesia (default) and ElevenLabs (opt-in) are both supported;
    # user picks via Settings dialog dropdown which writes to keyring
    # under "TTS_PROVIDER" + the provider's key under e.g. "ELEVENLABS_API_KEY".
    tts_provider, tts_api_key = _resolve_tts_credentials()
    # Local TTS (Kokoro) is keyless — skip the credential guard for it.
    if tts_provider != "kokoro" and not tts_api_key:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Nimbus needs an API key for {tts_provider.title()} TTS.\n\n"
            "Right-click the tray icon → Settings... to set it.",
            f"{tts_provider.title()} key missing",
            0x40,
        )
        sys.exit(1)
    from tts import create_tts_client
    try:
        tts_instance = create_tts_client(provider=tts_provider, api_key=tts_api_key)
    except ValueError as exc:
        # Stale provider_id in keyring (e.g. user downgraded after a future
        # version added a new provider that no longer exists). Show a
        # friendly MessageBox instead of dumping a traceback into the
        # bundled-EXE void.
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Nimbus's TTS configuration is invalid: {exc}\n\n"
            "Right-click the tray icon → Settings... to choose a "
            "supported provider.",
            "TTS provider not supported",
            0x40,
        )
        sys.exit(1)

    # Pre-load a local TTS model (Kokoro) in the background so the first spoken
    # reply isn't blocked by a cold ~330MB model load. No-op for cloud TTS.
    import threading as _warmup_threading
    _warmup_threading.Thread(
        target=tts_instance.warmup, daemon=True, name="tts-warmup"
    ).start()

    nimbus = NimbusApp(
        # route LLM_PROVIDER to the right model/client. When user
        # selected "Ollama (local)" in Settings, _llm_model_id is
        # 'ollama/<vision-model>' and _llm_api_key is empty — create_ai_client
        # dispatches to OllamaClient and Anthropic key is ignored.
        ai_client=create_ai_client(
            model_id=_llm_model_id,
            api_key=_llm_api_key,
            ollama_host=OLLAMA_HOST,
        ),
        stt_client=create_stt_client(_stt_provider, _stt_key),
        tts_client=tts_instance,
    )

    # Two-mic fix: in realtime mode, GPT-Realtime owns the 24kHz mic, so we
    # must NOT also open the 16kHz AssemblyAI STT mic (both grabbing the input
    # device = no audio). Skip STT entirely when the realtime session is active.
    if _should_connect_stt(nimbus._realtime):
        _log("Pre-opening mic + WebSocket (one-time startup cost)...")
        try:
            nimbus._stt.connect()
            # T4-5: this previously printed to stdout, which a windowed build does not
            # have -- the callback worked and its output went nowhere. Now it drives the
            # on-screen caption. Emitted as a signal because it fires on the AssemblyAI
            # WebSocket thread, never the Qt main thread.
            nimbus._stt.on_partial_transcript(nimbus.sig_caption.emit)
        except Exception as exc:
            # a provider that fails to load at startup (a local model
            # with a missing bundled dep, or a stale keyring setting) must NOT
            # brick the app with a traceback. If we exit here the user can't
            # reach Settings to fix it. Show a friendly message and keep going
            # so the tray + Settings open and they can switch providers.
            import traceback
            print(f"\nERROR: STT failed to start: {exc}")
            traceback.print_exc()
            # Two different failures, and the old dialog gave one answer to both.
            #
            # A provider that will not load is fixed by choosing another provider. A
            # microphone that will not open is not, because every provider records from the
            # same device -- so telling that user to switch to the cloud recogniser sent them
            # to a setting that could not possibly help. Reported, and it had happened: a
            # default input device of "Line In" with nothing plugged into it produced
            # PortAudio -9985, and the advice on screen was to change recogniser.
            #
            # `MicrophoneUnavailable` already explains itself, including which devices were
            # tried, so it is shown as-is rather than wrapped in advice that contradicts it.
            from stt import MicrophoneUnavailable

            if isinstance(exc, MicrophoneUnavailable):
                title, body = "Nimbus cannot reach a microphone", str(exc)
            else:
                title = "Speech-to-text failed to load"
                body = (
                    "Nimbus's speech-to-text provider failed to start:\n\n"
                    f"{exc}\n\n"
                    "Right-click the Nimbus tray icon and open Settings to switch "
                    "to a different provider (for example AssemblyAI cloud), then "
                    "restart Nimbus."
                )
            try:
                ctypes.windll.user32.MessageBoxW(None, body, title, 0x10)  # MB_ICONERROR
            except Exception:
                pass
            # Do NOT sys.exit — let the app open so the user can recover via Settings.
    else:
        _log("REALTIME: skipping AssemblyAI STT mic (GPT-Realtime owns the mic)")

    nimbus.start()
    # §5's silent 7-day check. After start(), so a licence check can never delay the hotkey being
    # ready -- and it no-ops entirely when the last check was recent.
    nimbus.revalidate_licence_async()

    # System tray icon — the ONLY clean exit path now that the overlay
    # has WS_EX_TOOLWINDOW (no taskbar entry) and there's no console
    # for Ctrl+C. Right-click tray → Quit triggers a clean shutdown.
    from tray import NimbusTray

    def _quit_via_tray() -> None:
        _log("Quit requested — shutting down...")
        nimbus.stop()
        qt_app.quit()

    def _show_settings() -> None:
        dlg = SettingsDialog()
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        if dlg._local_data_cleared:
            _log("Nimbus local data cleared; closing for a clean restart.")
            nimbus.stop()
            qt_app.quit()
            return
        _log("Settings saved.")
        # providers/models are built ONCE at startup, so a Settings
        # change can't affect the already-running instance. An earlier attempt
        # to auto-relaunch popped a stray terminal and sometimes failed, so we
        # do the reliable thing: close cleanly and tell the user to reopen.
        # One manual click, but it always works.
        ctypes.windll.user32.MessageBoxW(
            None,
            "Settings saved.\n\nNimbus will now close so your change can take "
            "effect. Reopen it from the Start Menu (or your desktop shortcut) "
            "to continue.",
            "Nimbus - reopen to apply",
            0x40,  # MB_ICONINFORMATION (OK only)
        )
        nimbus.stop()
        qt_app.quit()

    # --- SHELL_AND_CHAT.md §3/§4: the design system, the panel, the window ---
    #
    # The stylesheet goes on the QApplication rather than on each window, so the Settings
    # dialog, the tray menu and every QMessageBox inherit the palette too. A shell that looked
    # designed while its dialogs looked like stock Qt would be worse than neither.
    import theme
    qt_app.setStyleSheet(theme.build_qss())

    hud = build_chat_hud(nimbus)
    window = build_main_window(nimbus)

    def _show_window() -> None:
        """Tray left-click, Show Nimbus, and a second launch. Raises an open window, never no-ops."""
        if window is None:
            _show_settings()  # no window: Settings is the only thing worth offering
            return
        window.show()
        window.raise_()
        window.activateWindow()
        window.refresh()

    # A second launch of Nimbus lands here, marshalled off the Win32 wait thread by Qt.
    nimbus.sig_show_window.connect(_show_window)
    _watch_for_show_requests(nimbus.sig_show_window.emit)

    def _regate_licence() -> None:
        """Re-run the licence gate inside a running process (§5 `S-10`).

        Reached from Sign out and Deactivate. The window goes away first: leaving the Account page
        visible behind a modal that is asking for a licence key reads as two screens disagreeing
        about whether you are signed in.

        Declining quits, because the alternative is a process that has no licence, has been told so,
        and is still holding the hotkey and the microphone.

        A *failure* to evaluate the gate does not quit -- the same rule as startup. If our own code
        raises, the honest user keeps working; that is §0.1's deterrence-not-enforcement, and it is
        the reason this whole block is wrapped.

        Deferred to the next event-loop turn rather than run inline. The signal arrives while Qt is
        still inside the Account page's button handler, and opening a modal there means a nested
        event loop underneath a widget that is mid-click -- the classic way to end up deleting a
        button that is still on the stack. ``singleShot(0, ...)`` lets the click finish first.
        """
        QTimer.singleShot(0, _run_licence_gate)

    def _run_licence_gate() -> None:
        try:
            import licensing
            from activation_dialog import run_activation_flow

            if window is not None:
                window.hide()
            if run_activation_flow(licensing):
                _log(f"LICENCE: re-gated - {licensing.current_state().detail}")
                _show_window()
                return
        except Exception as exc:
            _log(f"LICENCE: re-gate skipped - {type(exc).__name__}: {exc}")
            return
        _log("LICENCE: gate declined after sign out - shutting down.")
        _quit_via_tray()

    nimbus.sig_licence_gate_required.connect(_regate_licence)

    def _set_ptt_paused(paused: bool) -> None:
        # The tray asks; NimbusApp writes and then tells every view (`S-3`). The tray
        # deliberately does not apply this itself, so its checkmark cannot get ahead of the
        # state it is supposed to be reporting.
        nimbus.set_listening(not paused)
        if paused:
            nimbus.sig_show_toast.emit(
                "Push-to-talk paused. Uncheck it in the tray menu to resume.", "info")

    # Tray construction can raise RuntimeError if the user's Windows
    # has no system tray available (rare — kiosk mode, custom shells,
    # certain VMs). Show a QMessageBox + exit cleanly rather than
    # leaving an invisible app running with no quit path.
    try:
        tray = NimbusTray(
            on_quit=_quit_via_tray,
            on_show_window=_show_window,
            on_pause_changed=_set_ptt_paused,
            # `S-5` trimmed Settings out of the tray because the window hosts it. If the window
            # could not be built, it goes back in -- an unreachable Settings screen would leave
            # a user with a bad API key no way to fix it.
            on_settings=_show_settings if window is None else None,
        )
    except RuntimeError as exc:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Nimbus -- Tray Error", str(exc)
        )
        nimbus.stop()
        sys.exit(1)

    # The tray's Pause checkmark is a view of hotkey.enabled, like the window's toggle. One
    # signal updates both, and neither keeps a copy.
    nimbus.sig_listening_changed.connect(lambda on: tray.set_paused(not on))

    if window is not None:
        window.sig_quit.connect(_quit_via_tray)
        # Closing hides (Invariant 5), which is correct and invisible. Without a word, a user
        # who closed the window reasonably concludes Nimbus has quit -- and is then startled
        # when the hotkey answers. Said once, not on every close, or it becomes noise.
        _hidden_notice_shown = {"done": False}

        def _on_hidden_to_tray() -> None:
            if _hidden_notice_shown["done"]:
                return
            _hidden_notice_shown["done"] = True
            tray.notify(
                "Nimbus is still running",
                f"Push-to-talk still works. Hold {HOTKEY} to ask something, or click the "
                "tray icon to reopen this window.",
            )

        window.sig_hidden_to_tray.connect(_on_hidden_to_tray)

        def _on_shell_local_data_cleared() -> None:
            _log("Nimbus local data cleared from the shell; closing for a clean restart.")
            nimbus.stop()
            qt_app.quit()

        window.sig_local_data_cleared.connect(_on_shell_local_data_cleared)

        # Before the question is asked, not after. A stored "off" from the era when that was the
        # default would otherwise beat the new default forever -- see
        # `config.migrate_shell_startup_default`.
        try:
            from config import migrate_shell_startup_default
            if migrate_shell_startup_default():
                _log("SHELL: retired an inherited SHELL_ON_STARTUP=off; the window opens now")
        except Exception as exc:
            _log(f"SHELL: startup-default migration skipped - {type(exc).__name__}: {exc}")

        from shell.window import should_open_on_startup
        if should_open_on_startup():
            _show_window()
        else:
            _log("SHELL: window built, starting to tray (SHELL_ON_STARTUP=off)")

    def _shutdown(*_args):
        _log("Shutting down...")
        nimbus.stop()
        qt_app.quit()

    signal.signal(signal.SIGINT, _shutdown)

    # T0-4: name the resolved provider alongside the model, and name the STT and
    # TTS providers too. Previously only the LLM model was logged, so a
    # provider/model mismatch surfaced as an opaque API failure several seconds
    # into the first interaction rather than as a visible line at startup.
    _log(
        f"LLM: provider={resolve_setting('LLM_PROVIDER', DEFAULT_LLM_PROVIDER)} "
        f"model={_llm_model_id}"
    )
    _log(f"STT: {type(nimbus._stt).__name__} | TTS: {type(nimbus._tts).__name__}")
    _log_env_pinned_settings()
    _log(f"Listening for {HOTKEY}... (Ctrl+C to quit)")

    sys.exit(qt_app.exec())
