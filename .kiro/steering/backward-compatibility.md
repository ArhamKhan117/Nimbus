# Backward compatibility

Nimbus has been installed and used on machines other than the development one. Testers have existing
keyring entries, memory files, knowledge-base folders, SQLite databases and installers already on disk,
some of them months old. **Breaking any of those is a defect, not a migration.**

The rule holds even though the audience is small, and arguably *more* strongly: a handful of testers
whose memory files were orphaned by an upgrade will not report it as a bug, they will simply stop using
it — so the loss is invisible.

## Hard rules

| Rule | What breaks if you ignore it |
|---|---|
| **Never rename an existing keyring slot.** Add a new one and read the old one as a fallback. | The user's API key silently disappears |
| **Never change the memory Markdown block shape.** `memory.py` preserves everything from the first `## ` heading to EOF. | Every existing interaction is orphaned |
| **Never change the knowledge-base filename convention.** `kb._sanitize_app_name` *delegates to* `memory._sanitize_app_name` rather than copying it. | Hand-authored `<app>.exe.md` files stop being found |
| **A new setting must have a default that reproduces current behaviour.** | An existing install, which has no keyring entry for it, changes behaviour on upgrade |
| **A new `AIClient` method needs a concrete default on the ABC.** | `OllamaClient` and every future provider break |
| **New SQLite tables are purely additive.** `CREATE TABLE IF NOT EXISTS`, never `ALTER` against a live user database. | A migration step someone can forget, run against real data |
| **The fully-local path keeps working.** | Offline users lose the app entirely |
| **The desktop licence contract is frozen.** `/trial`, `/activate`, `/refresh`, `/deactivate` with no `/api` prefix. | Every installer already in someone's hands stops being able to activate |

## The two that have already bitten

**`kb.py` carried its own copy of `_sanitize_app_name`** whose docstring claimed to mirror
`memory.py`'s "exactly". It did not: measured, **7 of 15 test inputs disagreed**. Memory strips
surrounding whitespace and replaces all nine Windows-reserved characters; the copy stripped nothing
and replaced three. The user-facing consequence was that the documented mental model broke — users are
told to read the canonical name out of `~/.nimbus/memory/` and name their file to match, and for an
app whose name needed stripping the two folders disagreed. The fix is delegation, and the delegation
*is* the guarantee: one function means the two can never diverge again.

**`SHELL_ON_STARTUP` was recorded as a choice nobody made.** The Settings dialog writes every checkbox
on Save, so a user who saved settings while the default was `off` had `off` stored explicitly. Changing
the default to `on` therefore did nothing for them. Fixed with `config.migrate_shell_startup_default`,
which deletes a stored `off` exactly once, guarded by `SHELL_STARTUP_REVISION`. That revision guard is
the pattern to copy: a migration that can run twice is a migration that will.

## Sanctioned exceptions to "a new setting must reproduce current behaviour"

Three, each argued rather than assumed:

- **`PRIVACY_GUARD` defaults ON.** The existing behaviour is the defect: every push-to-talk captured
  every monitor with no content awareness, so an open password manager could be sent to a cloud
  provider. Clearing local data restores the ON default, because a wipe must not silently weaken
  privacy.
- **`CAPTIONS` defaults ON.** The capability was already wired and merely printed to a console a
  windowed build does not have, so nobody was relying on the old behaviour.
- **`CHAT_HUD` defaults ON.** Nothing existed there before, so nobody can be relying on its absence.

`KB_CACHE` also defaults ON, but it is not an exception: it changes no observable behaviour and every
failure path falls back to the previous inline injection.

`CHAT_STORE_SCREENSHOTS` is the one in that group to get right, and it defaults **OFF**. Screen
contents on disk is a privacy commitment, not a preference, and must be an explicit opt-in rather than
something inherited from having enabled the panel.

## Licence and installer compatibility

- **The public key is baked into shipped binaries.** Generating a new keypair invalidates every
  licence already issued. If a pair exists, use it.
- **The service URL is baked in too**, which is why `licensing._post` follows redirects: a client that
  breaks on a 308 is a client a future DNS change can brick in the field, for users whose installer
  cannot be corrected.
- **Uninstall preserves user data.** `~/.nimbus/` and `~/Documents/Nimbus Wiki/` are the user's; only
  `_internal/__pycache__` is removed.
- **`releases/latest/download/Nimbus-Windows-Setup.exe` must keep resolving.** GitHub matches that
  path on the exact asset filename, so the release workflow publishes a stable name alongside the
  versioned one and reads the asset list back to confirm it landed.
