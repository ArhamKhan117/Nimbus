"""Privacy Guard: skip screen capture in sensitive contexts (T2-1).

Nimbus captures every monitor on every push-to-talk with no content awareness. On a cloud
provider that means a password manager, a banking page or an open `.env` file can be sent to
a third party because the user happened to ask a question with it on screen.

The Settings dialog says *"Nothing leaves your machine"*. That is true of **credentials** --
they live in Windows Credential Manager -- but a user will read it as being about **screen
contents**, and for cloud providers it is not. This module makes the existing claim honest.

**Kept separate from `capture.py` on purpose.** `capture.py` is about pixels and coordinate
spaces; this is policy. Mixing them would put a blocklist in the middle of the
highest-risk geometry code in the project.

`should_skip_capture` is a **pure function**: no I/O, no clock, no global state beyond the
configured lists. That makes the entire policy exhaustively testable without mocks, which
matters for a privacy feature -- a silently broken blocklist is worse than none, because the
user believes they are protected.

## Two decisions worth stating plainly

**This defaults ON**, which is the one deliberate exception to the rule that a new setting
must reproduce existing behaviour. The justification is that the existing behaviour is the
defect, not a preference. Noted in `README.md`.

**Detection failure fails OPEN.** `get_foreground_app()` returns `("unknown", "")` when the
Win32 calls fail, which happens transiently -- during window transitions, on UAC prompts,
against elevated processes. Failing *closed* would mean Nimbus silently stopping working
whenever foreground detection hiccups, and users would experience a privacy feature as
random breakage. Blocking is therefore based on positive identification only.
"""
from __future__ import annotations

import re


DEFAULT_BLOCKED_APPS: tuple[str, ...] = (
    # Verified installed on the development machine by enumerating the uninstall
    # registry and the install directory, rather than guessed: Kaspersky Password
    # Manager ships kpm.exe, kpm_viewer.exe and kpm_tray.exe.
    "kpm.exe",
    "kpm_viewer.exe",
    "kpm_tray.exe",
    # Standard basenames for the rest of the field. Unverified here, but a
    # non-matching entry is inert, and the cost of omitting a real password
    # manager is far higher than the cost of an unused list entry.
    "keepass.exe",
    "keepassxc.exe",
    "keepassxc-cli.exe",
    "bitwarden.exe",
    "1password.exe",
    "agilebits 1password.exe",
    "lastpass.exe",
    "dashlane.exe",
    "enpass.exe",
    "nordpass.exe",
    "protonpass.exe",
    "roboform.exe",
    "keeperpassword.exe",
    "sticky password.exe",
    "authy desktop.exe",
    "winauth.exe",
    # Credential and key tooling.
    "keymgr.dll",
    "rundll32.exe.keymgr",
    "puttygen.exe",
    "seahorse.exe",
)
"""Foreground executables that always suppress capture. Sanitised, lowercase."""

DEFAULT_BLOCKED_TITLE_PATTERNS: tuple[str, ...] = (
    # Authentication surfaces.
    r"\bsign[ -]?in\b",
    r"\blog[ -]?in\b",
    # NOT a bare \bpassword\b. That blocked any documentation page or blog post that
    # merely mentions passwords, which suppresses the screenshot on exactly the kind of
    # page Nimbus is most useful for. Require credential context, or the title starting
    # with the word (real password dialogs are often titled just "Password").
    r"^\s*password\b",
    r"\b(?:enter|new|current|confirm|master|forgot|reset|change|your|old)\s+password\b",
    r"\bpassword\s*(?:manager|vault|store|safe|generator)\b",
    r"\bpasskey\b",
    r"\bcredential",
    r"\b2fa\b",
    r"\bmfa\b",
    r"\bone[- ]time (?:code|password)\b",
    r"\bverification code\b",
    r"\bauthenticat",
    r"\brecovery (?:code|key|phrase)\b",
    r"\bseed phrase\b",
    r"\bprivate key\b",
    # Finance.
    r"\b(?:online |internet )?banking\b",
    r"\bbank of\b",
    r"\bcheckout\b",
    r"\bbilling\b",
    r"\bcard number\b",
    r"\bwallet\b",
    # Secret-bearing files. The literal dot is what keeps this tight: it matches the
    # ".env" extension in "config.env" or ".env.local", but not the word "environment"
    # in a docs page, because after "env" the \b fails against "i".
    r"\.env(?:\.\w+)?\b",
    r"\bid_rsa\b",
    r"\bid_ed25519\b",
    r"\bsecrets?\.(?:ya?ml|json|toml|ini)\b",
    r"\bcredentials\.(?:json|ya?ml|ini)\b",
    r"\.pem\b",
    r"\.pfx\b",
    r"\bkeystore\b",
    # Private browsing: the user has already signalled they do not want a record.
    r"\bincognito\b",
    r"\bprivate browsing\b",
    r"\bInPrivate\b",
)
"""Window-title regexes that suppress capture. Matched case-insensitively."""

_UNKNOWN_APP = "unknown"
"""Sentinel `get_foreground_app()` returns when Win32 detection fails."""


def _compile(patterns) -> list[re.Pattern]:
    """Compile title patterns, discarding any that are invalid.

    A malformed pattern is skipped rather than raised: these lists are user-editable from
    Settings, and one bad regex must not take down the capture path on every interaction.
    The remaining patterns keep working, so the guard degrades rather than failing.
    """
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def should_skip_capture(
    app_name: str,
    window_title: str,
    enabled: bool = True,
    blocked_apps=DEFAULT_BLOCKED_APPS,
    blocked_title_patterns=DEFAULT_BLOCKED_TITLE_PATTERNS,
) -> tuple[bool, str]:
    """Decide whether to suppress screen capture for the current foreground window.

    Returns:
        ``(skip, reason)``. ``reason`` is short, user-presentable text for the toast --
        never a regex, a path, or an exe name, because the toast is shown on screen and
        may itself be captured in a screenshot or screen recording.

    Pure function: same inputs always give the same answer, so the whole policy is
    testable without touching Win32 or the capture path.
    """
    if not enabled:
        return (False, "")

    name = (app_name or "").strip().lower()
    # Fail OPEN on detection failure. A transient Win32 hiccup must not look like a bug.
    if name and name != _UNKNOWN_APP:
        if name in {a.strip().lower() for a in blocked_apps}:
            return (True, "a password manager is open")

    title = window_title or ""
    if title.strip():
        for pattern in _compile(blocked_title_patterns):
            if pattern.search(title):
                return (True, "this window looks like it holds sensitive information")

    return (False, "")
