# The frozen build: four failures that are all silent

Nimbus ships as a PyInstaller one-dir bundle wrapped by Inno Setup. Everything that goes wrong here
goes wrong quietly, which is why this file exists and why `tools/build_release.py` checks each step
rather than trusting the exit code of the one before it.

## 1. A lazily-imported module is invisible

**The rule: any module imported lazily must be named in BOTH `nimbus.spec` `hiddenimports` AND
`app._run_selftest`'s `runtime_modules`.**

PyInstaller builds a static import graph. A module imported inside a function, behind a default-off
toggle, or through a package-level `__getattr__` is not in that graph, so it is not bundled — and the
build succeeds. The failure surfaces at someone's first use of the feature, on their machine.

Two modules shipped through this exact gap: `gemini_cache` and `gemini_live`, both behind
experimental toggles. `shell/__init__.py` resolves `MainWindow` through `__getattr__`, which no static
graph can follow, so every shell module is named individually.

Both lists must be updated. `nimbus.spec` is what bundles the module; `runtime_modules` is what makes
`--selftest` notice it is missing. One without the other gives you either a broken build or a build
that cannot tell you it is broken.

## 2. An asset read by path is not a dependency

`brand.py` and `tray.py` load artwork with `Path`-relative lookups, so PyInstaller has no reason to
include it. Three files are named explicitly in `datas`: `Nimbus tranparent .png`, `cursor.png`,
`nimbus_tray.ico`. And `brand.trimmed_pixmap` **degrades to a null pixmap rather than raising** — which
is correct at runtime and exactly why a missing asset is invisible. `build_release` checks all three
by name.

## 3. `--selftest` output cannot be piped

The frozen executable is built `console=False`, so `_run_selftest` calls
`AttachConsole(ATTACH_PARENT_PROCESS)` and reopens `sys.stdout` on `CONOUT$`. That bypasses pipes and
redirection entirely: `Nimbus.exe --selftest | Select-String OK` prints to the terminal and gives the
caller nothing. **Only the exit code and `NIMBUS_SELFTEST_LOG` are trustworthy.**

`_run_selftest` also collects failures rather than raising on the first one. A frozen build is usually
missing several related modules — a whole package's worth — and stopping at the first means as many
rebuild cycles as there are gaps.

## 4. A stale build passes every other check

A full round of manual testing once ran against an old executable. `--clean --verify` tested
`--verify` first, so PyInstaller never ran and verify passed against the previous session's `dist/`.
Every "you did not fix this" was true.

Two fixes, both still in place:

- The flag is `--verify-only` and it **refuses** to be combined with `--clean` or `--installer`
  rather than quietly letting one win.
- `check_not_stale()` compares the executable's mtime against every source in `SOURCE_GLOBS`
  (`*.py`, `shell/**/*.py`, `nimbus.spec` — deliberately not `tests/` or `tools/`, and skipping
  `_`-prefixed probes).

For the stronger question — *is this specific string inside the binary?* — use
`tools/verify_bundle.py`. A byte search over `dist/` proves nothing, because PyInstaller
zlib-compresses every module: measured, a scan of 2,741 files found all seven markers absent from a
build that contained several. That tool reads the archive's own marshalled code objects, walks the
PYZ, and matches by **substring** (adjacent string literals are concatenated at compile time, so
exact matching reports every multi-line message as missing).

## The licence key

`licence_key.py` is **generated and git-ignored**, and holds the public half only. It exists as a
module rather than an environment variable because PyInstaller freezes code, not the shell that
invoked it: `NIMBUS_LICENCE_PUBLIC_KEY` set during a build is gone by the time someone
double-clicks the exe, and every activation then fails with "this build has no licence key
configured".

`nimbus.spec` names it in `hiddenimports` **only if the file exists**, because PyInstaller warns
loudly about an unresolvable hidden import and a dev build legitimately has no key.
`tools/set_licence_key.py` validates before writing: decodes the base64url, checks for exactly 32
bytes, loads it as an Ed25519 key, and round-trips a signature. A truncated paste otherwise surfaces
as a tester whose valid licence is rejected with a message blaming the *licence*.

## Bundle size

The first build was 1.1 GB. The `excludes` list drops it about 60% to ~440 MB, and the installer
compresses to 125–170 MB. `torch` (315 MB), `llvmlite` (102 MB), `pyarrow` (76 MB), `scipy` (53 MB)
and `pandas` (17 MB) are pulled in transitively and used by nothing at runtime. **`av` and
`onnxruntime` are deliberately no longer excluded** — faster-whisper needs PyAV, Kokoro needs ONNX
Runtime — and `av` needs special handling because `collect_all` misclassifies its `.pyd` as data and
its FFmpeg DLLs live in a sibling `av.libs` directory.

## Installer

Per-user, `PrivilegesRequired=lowest`, so there is no UAC prompt on a locked-down machine. Icons come
from one generated `.ico` so the setup icon, the taskbar icon and the in-app logo cannot drift apart.
`AppURL` points at the website rather than the repository, because those are the links a user clicks
from Windows' own settings and the website is what they came from. `AppUpdatesURL` is separate and
points at the releases page: it used to be `{AppURL}/releases`, and the site has no such route, so that
link was a 404 for every installed copy.
