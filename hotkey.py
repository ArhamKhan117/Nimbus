"""Nimbus global push-to-talk hotkey (Ctrl+Alt+Space).

Installs a low-level Windows keyboard hook via pynput in OBSERVE-ONLY mode
(`suppress=False`) and fires on_press / on_release callbacks when the
Ctrl+Alt+Space combo becomes active / breaks.

Why Ctrl+Alt+Space and not Alt+Space, Ctrl+Shift+Space, or Fn+Space:

- **Alt+Space alone** is reserved by Windows for the window menu (Move / Size /
  Minimize / Maximize / Close). Microsoft also reassigned it to Copilot in
  Windows 11. Every launcher that ships with Alt+Space (Raycast,
  Flow Launcher, PowerToys Run, Launchy) requires users to manually disable
  these via `Settings > Hotkeys`. Making it work cleanly requires Win32
  `RegisterHotKey` + `GetAsyncKeyState` polling for release detection (8-12h
  of fragile ctypes code). That upgrade is deferred as a future drop-in
  subclass of `PushToTalkHotkey`.
- **Ctrl+Shift+Space** was an earlier pivot target,
  but empirical testing revealed it conflicts with Microsoft Excel + Google
  Sheets "Select entire worksheet" binding. Because this listener uses
  `suppress=False` (observe-only), the spreadsheet underneath receives the
  keypress AND wipes the user's selection every time they invoke Nimbus,
  which is unacceptable when working in a spreadsheet.
- **Fn+Space** was researched and rejected: the Fn key is handled by the
  laptop's Embedded Controller BELOW the OS layer. Windows never receives an
  Fn keypress event. pynput's `WH_KEYBOARD_LL` hook does not see it. On many
  laptops, Fn+Space produces a hardware action (brightness / backlight /
  airplane mode) instead of a Space event. AutoHotkey community confirms:
  *"the Fn key does not (as a general rule) generate any scan code that can
  be used by AHK, as the key is intercepted and interpreted directly by the
  PC's BIOS."* Non-portable even where it happens to work.
- **Ctrl+Alt+Space** (chosen): 10-minute pivot from Ctrl+Shift+Space, zero
  known code-level conflicts (Excel, Sheets, Windows window menu, Copilot,
  VS Code all clear), reuses the existing pynput suppress=False model.
  Three-finger combo but all on the left side of the keyboard for one-handed
  ergonomics. VS Code binds Ctrl+Shift+Space to "Trigger Parameter Hints" —
  that was a minor conflict with the previous pivot but is NOT a conflict
  with Ctrl+Alt+Space.

  **Known setup requirement:** if another app already binds Ctrl+Alt+Space
  to a global quick-access shortcut, both apps will receive the keypress
  because our listener is observe-only. Disable that other binding
  (Keyboard Shortcuts > Ctrl+Alt+Space > None / reassign) — the same thing
  Raycast / Flow Launcher / PowerToys Run users do for the Alt+Space /
  Windows menu / Copilot conflicts. A future configurable-hotkey UI or a
  Win32 RegisterHotKey approach could claim the combo at the OS level.

`suppress=False` is DELIBERATE and load-bearing: pynput's suppress flag is
global all-or-nothing. Setting it to True would install a `WH_KEYBOARD_LL`
hook that blocks EVERY key event system-wide, not just our combo. That's the
exact bug that caused the earlier Alt+Space pivot away from suppress=True.
Ctrl+Alt+Space has no default Windows OS behavior that needs suppressing, so
observe-only works cleanly.

Our suppress=False approach observes keypresses but does NOT consume them, so
the focused app still receives every keystroke — "observe but don't consume".

Callbacks run on the pynput listener thread, NOT the Qt main thread.
caller (this module's __main__) just prints. app.py will wire them to
pyqtSignal.emit which is thread-safe by design — Qt marshals across threads.

File order (so `py -3.13 -m hotkey` works):
    1. Module docstring
    2. Imports
    3. HotkeyState enum
    4. PushToTalkHotkey class
    5. __main__ block LAST
"""
from __future__ import annotations

import threading
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from pynput import keyboard


# --- State enum --------------------------------------------------------------

class HotkeyState(Enum):
    """Two-state push-to-talk state machine.

    IDLE:      waiting for the user to hold all 3 keys of Ctrl+Alt+Space.
               No recording active.
    RECORDING: Ctrl AND Alt AND Space are ALL currently held. Audio capture
               is live. On any of the 3 being released, transition back to
               IDLE and fire on_release.
    """

    IDLE = "idle"
    RECORDING = "recording"


@dataclass(frozen=True)
class HotkeyCombo:
    """Validated, normalized push-to-talk chord."""
    modifiers: frozenset[str]
    trigger: str

    @property
    def display(self) -> str:
        ordered = [m for m in ("ctrl", "alt", "shift") if m in self.modifiers]
        return "+".join([*ordered, self.trigger])


_MODIFIER_ALIASES = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift"}
_SPECIAL_TRIGGERS = {"space", "enter", "tab"}


def shortcut_vk(character: str) -> int | None:
    """The Windows virtual key code for a letter or digit, or ``None``.

    Windows numbers ``VK_A``..``VK_Z`` and ``VK_0``..``VK_9`` as the ASCII codes of their
    *uppercase* forms, so this is ``ord(c.upper())`` with a range check rather than a table.
    Measured: ``ord('H') == 72`` and ``ord('N') == 78``, which is what the hook reports for
    Ctrl+Alt+H and Ctrl+Alt+N.

    **Why the shortcuts match on this and not on ``key.char``.** They used to match on the
    character, and it never fired once on real hardware. pynput's Windows backend builds every
    ``KeyCode`` through ``KeyTranslator.__call__``, which looks the character up in a layout
    table keyed by ``(shift, ctrl, alt)`` -- so with Ctrl held, ``H`` translates to the *control
    character* ``\\x08``, not ``'h'``. The tests passed because they synthesised
    ``KeyCode.from_char('h')``, which is not what Windows delivers; that constructor does not
    even populate ``vk`` (measured: ``None``). The virtual key code is the one field that is the
    same regardless of which modifiers happen to be down.
    """
    text = str(character or "")
    if len(text) != 1:
        return None
    upper = text.upper()
    if upper.isascii() and (upper.isdigit() or "A" <= upper <= "Z"):
        return ord(upper)
    return None


def control_character_vk(character: str) -> int | None:
    """The virtual key code implied by an ASCII control character, or ``None``.

    ``Ctrl+H`` arrives as ``\\x08`` and ``Ctrl+N`` as ``\\x0e``; ASCII control codes are the
    letter's position in the alphabet, so ``ord(c) + 64`` recovers ``'H'`` / ``'N'``. This is the
    fallback for the case where a ``KeyCode`` carries a character but no ``vk`` -- pynput's
    non-Windows backends, and any caller that built one with ``KeyCode.from_char``.
    """
    text = str(character or "")
    if len(text) != 1:
        return None
    code = ord(text)
    if 1 <= code <= 26:
        return code + 64
    return None


def parse_hotkey(value: str) -> HotkeyCombo:
    """Validate a human-readable chord such as ``ctrl+alt+space``.

    Requiring a modifier prevents a Settings typo from turning ordinary typing
    into push-to-talk. The final trigger may be Space, Enter, Tab, one letter
    or number, or an F1–F12 key.
    """
    tokens = [part.strip().lower() for part in value.split("+") if part.strip()]
    if len(tokens) < 2:
        raise ValueError("Use a modifier plus a key, for example Ctrl+Alt+Space.")
    modifiers = frozenset(_MODIFIER_ALIASES[t] for t in tokens if t in _MODIFIER_ALIASES)
    triggers = [t for t in tokens if t not in _MODIFIER_ALIASES]
    if len(modifiers) != len([t for t in tokens if t in _MODIFIER_ALIASES]):
        raise ValueError("Do not repeat a modifier in the hotkey.")
    if not modifiers:
        raise ValueError("Add Ctrl, Alt, or Shift so normal typing stays safe.")
    if len(triggers) != 1:
        raise ValueError("Choose exactly one final key, for example Ctrl+Alt+Space.")
    trigger = triggers[0]
    if not (
        trigger in _SPECIAL_TRIGGERS
        or re.fullmatch(r"[a-z0-9]", trigger)
        or re.fullmatch(r"f(?:[1-9]|1[0-2])", trigger)
    ):
        raise ValueError("Use Space, Enter, Tab, A-Z, 0-9, or F1-F12 as the final key.")
    if modifiers == frozenset({"alt"}) and trigger == "space":
        raise ValueError("Alt+Space opens the Windows window menu; add Ctrl or choose another key.")
    if modifiers == frozenset({"ctrl", "shift"}) and trigger == "space":
        raise ValueError("Ctrl+Shift+Space conflicts with Excel and Google Sheets; choose another chord.")
    if modifiers == frozenset({"ctrl"}) and trigger == "space":
        raise ValueError("Ctrl+Space conflicts with VS Code IntelliSense; add Alt or choose another key.")
    return HotkeyCombo(modifiers, trigger)


# --- PushToTalkHotkey --------------------------------------------------------

class PushToTalkHotkey:
    """Global Ctrl+Alt+Space push-to-talk hotkey, non-suppressing.

    Tracks Ctrl, Alt, Space key-down state independently so any of the 6
    possible press orders transitions IDLE -> RECORDING when all 3 are held.
    Any release of any of the 3 while in RECORDING immediately fires
    on_release() and returns to IDLE, clearing all 3 flags. This matches
    real-world PTT UX: the moment the combo breaks, stop recording.

    Thread model: pynput installs a low-level Windows keyboard hook on its
    own thread and invokes our handlers from that thread. Callers' on_press /
    on_release run on the pynput listener thread. (__main__) just
    prints, which is thread-safe. app.py wires to pyqtSignal.emit
    which marshals across threads for free.

    A small threading.Lock guards the state flags because the listener thread
    fires handlers serially BUT start()/stop() can be called from the main
    thread concurrently with handler execution.

    suppress=False is DELIBERATE and load-bearing: pynput's suppress flag is
    global (all-or-nothing), and we only want to observe Ctrl+Alt+Space, not
    block every other key on the system. Ctrl+Alt+Space has no default
    Windows behavior, so observe-only works.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        hotkey: str = "ctrl+alt+space",
        listener_class=None,
        on_cancel: Optional[Callable[[], None]] = None,
        is_in_flight: Optional[Callable[[], bool]] = None,
        shortcuts: Optional[dict] = None,
    ) -> None:
        """Wire the hotkey to caller callbacks.

        Args:
            on_press:       fired once when Ctrl+Alt+Space combo becomes
                            active (all 3 keys held). Runs on pynput listener
                            thread.
            on_release:     fired once when the combo is broken by releasing
                            any of the 3 keys while RECORDING. Listener thread.
            listener_class: DI hook for tests -- factory for building the
                            keyboard listener. Defaults to pynput.keyboard.Listener
                            at construction time so tests can inject MagicMock.
            on_cancel:      T2-2. Fired on bare Esc, but ONLY when
                            ``is_in_flight()`` returns True. Optional, so existing
                            construction is unaffected. Listener thread.
            is_in_flight:   T2-2. Predicate the app supplies to report whether a
                            response is currently in progress.
            shortcuts:      SHELL_AND_CHAT.md §4 item 8. ``{"h": callback, "n": callback}``
                            -- extra chords sharing the PTT modifiers, fired on key-down.
                            Listener thread.

                            **Routed through this listener rather than a second one.**
                            Each ``pynput`` listener installs its own ``WH_KEYBOARD_LL``
                            hook, and that hook runs on *every keystroke system-wide*; a
                            second one would double that cost for two shortcuts. It is
                            also deliberately not ``parse_hotkey``, which validates chords
                            Nimbus intends to *own* -- these are secondary bindings that
                            piggyback on modifiers already held.

        **Why Esc is handled here rather than in a second listener.** Each
        ``pynput`` listener installs its own ``WH_KEYBOARD_LL`` hook, and that hook
        runs on *every keystroke system-wide*. Adding a second one would double that
        cost for every key the user types anywhere, to watch a single key. Extending
        the existing listener costs one comparison.

        **Why Esc does not go through ``parse_hotkey``.** That function deliberately
        rejects modifier-free chords, so a Settings typo cannot turn ordinary typing
        into push-to-talk. That guard is correct and stays. Cancel needs the opposite
        -- a bare, unmodified key -- so it takes a separate, narrower path and is not
        user-configurable.

        **Why the predicate matters.** Esc is one of the most-pressed keys on the
        keyboard. Firing unconditionally would mean Nimbus reacting to every Esc in
        every application. Gating on "is a response actually in flight" makes the key
        inert the rest of the time. ``hotkey.py`` stays stateless about the pipeline:
        it asks, the app answers.
        """
        self._combo = parse_hotkey(hotkey)
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._on_cancel_cb = on_cancel
        self._is_in_flight = is_in_flight
        # Lower-cased once here so the per-keystroke path does no string work.
        self._shortcuts = {
            str(character).lower(): callback
            for character, callback in (shortcuts or {}).items()
        }
        self._shortcut_vks = {
            vk: callback
            for character, callback in self._shortcuts.items()
            for vk in (shortcut_vk(character),) if vk is not None
        }
        self._listener_class = listener_class or keyboard.Listener

        self._lock = threading.Lock()
        self._ctrl_down: bool = False
        self._alt_down: bool = False
        self._space_down: bool = False
        self._down_modifiers: set[str] = set()
        self._trigger_down: bool = False
        self._state: HotkeyState = HotkeyState.IDLE
        self._enabled: bool = True

        self._listener = None  # set in start(), cleared in stop()

    @property
    def state(self) -> HotkeyState:
        """Current state machine position. Thread-safe read."""
        with self._lock:
            return self._state

    @property
    def enabled(self) -> bool:
        """Whether PTT callbacks are currently accepted (listener remains installed)."""
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Pause/resume PTT without tearing down the global keyboard hook.

        Pausing during a recording cleanly ends that recording once, then
        ignores all later key events until resumed.
        """
        fire_release = False
        with self._lock:
            enabled = bool(enabled)
            if self._enabled == enabled:
                return
            self._enabled = enabled
            if not enabled and self._state == HotkeyState.RECORDING:
                fire_release = True
            self._state = HotkeyState.IDLE
            self._down_modifiers.clear()
            self._trigger_down = False
            self._sync_legacy_flags()
        if fire_release:
            self._on_release_cb()

    # --- public lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Install the low-level Windows keyboard hook with suppress=False.

        Idempotent: calling start() twice is a no-op after the first.
        The listener runs on its own thread; this returns immediately.

        suppress=False is DELIBERATE -- we observe key events but do NOT
        consume them. Ctrl+Alt+Space has no default Windows OS behavior
        (unlike Alt+Space which opens the title-bar menu / Copilot), so we
        don't need to block it. This preserves global typing. Changing to
        suppress=True would block ALL keys globally due to pynput's
        all-or-nothing suppress semantics -- the exact bug that forced the
        earlier Alt+Space pivot.
        """
        with self._lock:
            if self._listener is not None:
                return  # already started -- idempotent

            self._listener = self._listener_class(
                on_press=self._handle_press,
                on_release=self._handle_release,
                suppress=False,
            )
            self._listener.start()

    def stop(self) -> None:
        """Uninstall the hook and release the listener thread. Idempotent."""
        listener = None
        with self._lock:
            if self._listener is None:
                return  # already stopped -- idempotent
            listener = self._listener
            self._listener = None
        # Call stop() outside the lock so pynput can join its own thread
        # without deadlocking on a handler that's mid-flight waiting for us.
        try:
            listener.stop()
        except Exception:
            # Best-effort: if pynput's teardown raises (e.g. already stopped
            # internally), don't bubble it up -- stop() is idempotent.
            pass

    # --- key-event handlers (invoked by pynput on listener thread) ----------

    def _is_ctrl(self, key) -> bool:
        """Treat Ctrl, Ctrl_L, and Ctrl_R all as the ctrl modifier.

        pynput fires Key.ctrl_l on left-Ctrl press and Key.ctrl on some
        systems. Lumping them together avoids split-brain state where
        Ctrl_L pressed and Ctrl_R released would leave _ctrl_down stuck True.
        The RECORDING-release path clears all 3 flags defensively.
        """
        return key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)

    def _is_alt(self, key) -> bool:
        """Treat Alt, Alt_L, Alt_R, and Alt_Gr all as the alt modifier.

        pynput fires Key.alt_l on left-Alt press. Right-Alt on international
        keyboards is often Alt_Gr (AltGr) which is a separate pynput key
        from Alt_R. We lump all four together so the combo works
        regardless of which Alt is pressed. Note: if the user is on an
        international keyboard layout that uses AltGr for composing
        characters, holding Ctrl+AltGr+Space will still trigger Nimbus,
        which is acceptable -- AltGr without a composing key is typically
        unused.
        """
        return key in (
            keyboard.Key.alt,
            keyboard.Key.alt_l,
            keyboard.Key.alt_r,
            keyboard.Key.alt_gr,
        )

    def _is_space(self, key) -> bool:
        """Space is a single constant in pynput (no left/right variants)."""
        return key == keyboard.Key.space

    def _modifier_for_key(self, key) -> str | None:
        if self._is_ctrl(key):
            return "ctrl"
        if self._is_alt(key):
            return "alt"
        if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            return "shift"
        return None

    def _is_trigger(self, key) -> bool:
        trigger = self._combo.trigger
        if trigger == "space":
            return self._is_space(key)
        if trigger == "enter":
            return key in (keyboard.Key.enter, getattr(keyboard.Key, "return", keyboard.Key.enter))
        if trigger == "tab":
            return key == keyboard.Key.tab
        if trigger.startswith("f"):
            return key == getattr(keyboard.Key, trigger)
        return getattr(key, "char", "").lower() == trigger

    def _sync_legacy_flags(self) -> None:
        """Keep default-combo diagnostic fields stable for existing callers."""
        self._ctrl_down = "ctrl" in self._down_modifiers
        self._alt_down = "alt" in self._down_modifiers
        self._space_down = self._trigger_down if self._combo.trigger == "space" else False

    def _handle_cancel_key(self, key) -> bool:
        """Fire ``on_cancel`` on bare Esc while a response is in flight (T2-2).

        Returns True when the key was consumed as a cancel, so ``_handle_press``
        returns immediately and Esc can never touch the PTT state machine. Esc is not a
        modifier or a trigger, so it would be ignored anyway -- returning early makes
        that independence explicit rather than incidental.

        Deliberately does NOT take ``self._lock``: the only shared state read is
        ``_enabled``, and the callback may do real work (stopping TTS, clearing the
        overlay). Holding the lock across it would block every other key event in the
        system on Nimbus's cancel path.

        Every failure is swallowed. This runs on the pynput listener thread, where an
        escaping exception kills the listener and the hotkey stops working for the rest
        of the session -- a far worse outcome than a cancel that did not happen.
        """
        if key != keyboard.Key.esc:
            return False
        if self._on_cancel_cb is None or not self._enabled:
            return False
        try:
            if self._is_in_flight is not None and not self._is_in_flight():
                return False
            self._on_cancel_cb()
        except Exception:
            return True
        return True

    def _handle_shortcut_key(self, key) -> bool:
        """Fire a secondary shortcut sharing the PTT modifiers (§4 item 8).

        ``Ctrl+Alt+H`` and ``Ctrl+Alt+N`` in practice: the same modifiers the user already
        holds for push-to-talk, with a letter instead of the trigger. Reusing the modifier set
        rather than declaring its own means a user who remapped the hotkey to ``Ctrl+Shift+Space``
        gets ``Ctrl+Shift+H`` for free, which is what they would expect.

        Returns True when consumed, so ``_handle_press`` returns before the PTT state machine
        sees the key. A letter is neither a modifier nor the trigger so it would be ignored
        anyway; returning early makes that independence explicit rather than incidental.

        Same three disciplines as ``_handle_cancel_key``, for the same reasons: no lock around
        the callback (it may open a window), gated on ``_enabled`` so a paused Nimbus is fully
        paused, and every exception swallowed because an escape here kills the listener thread
        and with it the hotkey for the rest of the session.

        **Matches on the virtual key code, not the character** -- see ``shortcut_vk``. Matching
        on ``key.char`` was the reason neither shortcut ever fired: with Ctrl held, Windows
        translates ``H`` to ``\\x08``.
        """
        if not self._shortcut_vks:
            return False
        callback = self._callback_for(key)
        if callback is None:
            return False
        with self._lock:
            if not self._enabled:
                return False
            # The modifiers must already be held. `issubset` rather than equality so a user
            # holding an extra modifier is not silently ignored.
            if not self._combo.modifiers.issubset(self._down_modifiers):
                return False
        try:
            callback()
        except Exception:
            return True
        return True

    def _callback_for(self, key):
        """The shortcut bound to ``key``, by virtual key code, with a character fallback.

        Three shapes have to resolve to the same binding, because three different producers
        exist: the Windows hook (``vk`` set, ``char`` a control character), a ``KeyCode`` built
        from a plain character (``vk`` empty), and a control character with no ``vk``.
        """
        vk = getattr(key, "vk", None)
        if isinstance(vk, int):
            callback = self._shortcut_vks.get(vk)
            if callback is not None:
                return callback
        character = getattr(key, "char", None)
        if not character:
            return None
        derived = control_character_vk(character) or shortcut_vk(character)
        if derived is None:
            return None
        return self._shortcut_vks.get(derived)

    def _handle_press(self, key) -> Optional[bool]:
        """Low-level key-down handler called by pynput on its listener thread.

        Sets the appropriate _down flag. If all 3 flags are True AND state is
        IDLE, transitions to RECORDING and fires on_press() exactly once.
        Order-independent: any of the 6 possible key-down sequences works.
        """
        if self._handle_cancel_key(key):
            return None
        if self._handle_shortcut_key(key):
            return None
        fire_press = False
        with self._lock:
            if not self._enabled:
                return None
            modifier = self._modifier_for_key(key)
            if modifier is not None:
                self._down_modifiers.add(modifier)
            elif self._is_trigger(key):
                self._trigger_down = True
            self._sync_legacy_flags()
            # Non-hotkey keys: ignored, no state change, no flag touched.

            # Check if the combo is now complete.
            if (self._state == HotkeyState.IDLE
                    and self._combo.modifiers.issubset(self._down_modifiers)
                    and self._trigger_down):
                self._state = HotkeyState.RECORDING
                fire_press = True

        if fire_press:
            # Fire callbacks OUTSIDE the lock so a slow on_press doesn't
            # block concurrent state reads from other threads.
            self._on_press_cb()
        return None  # pynput convention: None = propagate (we're suppress=False anyway)

    def _handle_release(self, key) -> Optional[bool]:
        """Low-level key-up handler called by pynput on its listener thread.

        If RECORDING AND the released key is ctrl/alt/space: fire on_release
        once, clear all 3 flags, return to IDLE. Otherwise just clear the
        flag for this specific released key (if it's one of the 3).
        """
        fire_release = False
        with self._lock:
            if not self._enabled:
                return None
            modifier = self._modifier_for_key(key)
            is_hotkey_key = (
                (modifier is not None and modifier in self._combo.modifiers)
                or self._is_trigger(key)
            )

            if self._state == HotkeyState.RECORDING and is_hotkey_key:
                # Any of the 3 released while RECORDING: end the recording.
                fire_release = True
                self._state = HotkeyState.IDLE
                self._down_modifiers.clear()
                self._trigger_down = False
            else:
                if modifier is not None:
                    self._down_modifiers.discard(modifier)
                elif self._is_trigger(key):
                    self._trigger_down = False
            self._sync_legacy_flags()

        if fire_release:
            self._on_release_cb()
        return None


# --- Manual verification entry point ----------------------------------------

if __name__ == "__main__":
    # Run: py -3.13 -m hotkey
    # Hold Ctrl+Alt+Space to trigger PRESSED, release any of the 3 for RELEASED.
    # CRITICAL: verify typing in other apps still works (suppress=False).
    # CRITICAL: verify Excel does NOT "Select entire worksheet" on the combo
    # (that was the Ctrl+Shift+Space conflict this pivot fixes).
    import time

    print("=" * 70)
    print("Nimbus -- hotkey.py manual verification (Ctrl+Alt+Space)")
    print("=" * 70)
    print("\nInstructions:")
    print("  1. Hold Ctrl+Alt+Space -- you should see >>> PRESSED within 50ms")
    print("  2. Release any of the 3 keys -- you should see >>> RELEASED within 50ms")
    print("  3. Open another window (Notepad) and type 'hello world' normally --")
    print("     typing MUST work (suppress=False: observe but never consume keys)")
    print("  4. Open Excel or Google Sheets, hold Ctrl+Alt+Space, verify NO")
    print("     'Select entire worksheet' side effect. This is the whole reason")
    print("     we pivoted from Ctrl+Shift+Space -- that combo selects all cells.")
    print("  5. Verify the Windows window menu does NOT pop (Alt+Space alone")
    print("     would open it, but Ctrl+Alt+Space should not).")
    print("  6. Ctrl+C in this terminal to quit")
    print()

    hk = PushToTalkHotkey(
        on_press=lambda: print("  >>> PRESSED (recording started)"),
        on_release=lambda: print("  >>> RELEASED (recording stopped)"),
    )
    hk.start()
    print("Listener started. Waiting for Ctrl+Alt+Space...\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        hk.stop()
        print("\nListener stopped. Exiting.")
