"""Per-app system-prompt addenda (T2-5, "Code Mode").

Nimbus already knows which application is in the foreground -- `app.py` calls
`get_foreground_app()` on every press and passes the name into memory recall. That signal
was doing nothing for the *prompt*, so a question asked in a code editor got the same
generic UI-helper framing as one asked in a spreadsheet.

This module closes that gap with the cheapest possible mechanism: a lookup table of short
addenda appended to the existing system prompt when the foreground app matches.

Two design rules, both load-bearing:

1. **Append, never replace.** The base prompt carries the persona, the write-for-the-ear
   contract, and the pointing rules. Substituting it would silently destroy all three --
   which is why `test_addendum_appended_not_replacing_base_prompt` exists.
2. **Keys go through `memory._sanitize_app_name`.** Reusing that function rather than
   writing a second normaliser means the keys match the folder names users already see in
   `~/.nimbus/memory/`, and the two cannot drift apart. `_sanitize_app_name` lowercases and
   strips, which also gives case-insensitive matching for free -- `Code.exe` and `code.exe`
   are the same key.

Extending this to spreadsheets, design tools or video editors is a dictionary entry.
"""
from __future__ import annotations

from memory import _sanitize_app_name


_CODE_ADDENDUM = (
    "\n\nthis is a code editor. when the user asks about what is on screen, talk about the "
    "code itself, not the editor chrome: name variables, functions and types as they are "
    "written. if you spot the cause of a bug, say what is wrong and why in one or two "
    "sentences before pointing at it. when you refer to a line, say what the line does "
    "rather than reading its number aloud. do not read long code fragments or punctuation "
    "out loud -- describe them, because everything you say is heard rather than read."
)

_BROWSER_ADDENDUM = (
    "\n\nthis is a web browser. distinguish the page content from the browser's own "
    "controls, and say which one you mean. if the user is filling in a form or reading "
    "documentation, help with that content rather than describing the browser. never read a "
    "url out character by character -- name the site instead, because everything you say is "
    "heard rather than read."
)

_TERMINAL_ADDENDUM = (
    "\n\nthis is a terminal. read error output carefully and say what actually failed "
    "rather than restating the whole message. when you suggest a command, say it slowly "
    "and plainly, and never read long paths character by character."
)


APP_PROMPT_ADDENDA: dict[str, str] = {
    # --- Code editors --------------------------------------------------------
    # Verified present on the development machine by enumerating installed software
    # and running processes rather than guessing: Kiro, Notepad++, Visual Studio 2019.
    "kiro.exe": _CODE_ADDENDUM,
    "notepad++.exe": _CODE_ADDENDUM,
    "devenv.exe": _CODE_ADDENDUM,
    # Standard basenames for the rest of the field. Unverified on this machine, but a
    # non-matching key is inert -- it costs one dict entry and never misfires.
    "code.exe": _CODE_ADDENDUM,
    "code - insiders.exe": _CODE_ADDENDUM,
    "cursor.exe": _CODE_ADDENDUM,
    "windsurf.exe": _CODE_ADDENDUM,
    "zed.exe": _CODE_ADDENDUM,
    "sublime_text.exe": _CODE_ADDENDUM,
    "idea64.exe": _CODE_ADDENDUM,
    "pycharm64.exe": _CODE_ADDENDUM,
    "webstorm64.exe": _CODE_ADDENDUM,
    "rider64.exe": _CODE_ADDENDUM,
    "clion64.exe": _CODE_ADDENDUM,
    "goland64.exe": _CODE_ADDENDUM,
    "rubymine64.exe": _CODE_ADDENDUM,
    "phpstorm64.exe": _CODE_ADDENDUM,
    "nvim.exe": _CODE_ADDENDUM,
    "gvim.exe": _CODE_ADDENDUM,
    "emacs.exe": _CODE_ADDENDUM,
    # --- Browsers ------------------------------------------------------------
    "chrome.exe": _BROWSER_ADDENDUM,
    "msedge.exe": _BROWSER_ADDENDUM,
    "firefox.exe": _BROWSER_ADDENDUM,
    "brave.exe": _BROWSER_ADDENDUM,
    "opera.exe": _BROWSER_ADDENDUM,
    "vivaldi.exe": _BROWSER_ADDENDUM,
    # --- Terminals -----------------------------------------------------------
    "windowsterminal.exe": _TERMINAL_ADDENDUM,
    "powershell.exe": _TERMINAL_ADDENDUM,
    "pwsh.exe": _TERMINAL_ADDENDUM,
    "cmd.exe": _TERMINAL_ADDENDUM,
    "alacritty.exe": _TERMINAL_ADDENDUM,
    "wezterm-gui.exe": _TERMINAL_ADDENDUM,
}
"""Foreground app -> extra system-prompt text. Keys are sanitised exe basenames."""


def addendum_for_app(app_name: str) -> str:
    """Return the prompt addendum for a foreground app, or `""` if none applies.

    Returns a plain empty string rather than `None` so callers can concatenate
    unconditionally -- the common case is "no addendum" and it should not need a branch.

    Tolerates the `"unknown"` sentinel and the empty string that `get_foreground_app()`
    returns on detection failure, because a foreground-detection hiccup must never break the
    prompt.
    """
    if not app_name:
        return ""
    try:
        key = _sanitize_app_name(app_name)
    except ValueError:
        return ""
    return APP_PROMPT_ADDENDA.get(key, "")


def apply_app_addendum(system_prompt: str, app_name: str) -> str:
    """Append the app-specific addendum to `system_prompt`.

    The single entry point callers should use: it makes the append-not-replace rule
    structural instead of a convention someone has to remember.
    """
    return system_prompt + addendum_for_app(app_name)
