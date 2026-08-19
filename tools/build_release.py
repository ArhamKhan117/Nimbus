"""Build the frozen app, verify it, and package the installer.

    python -m tools.build_release                freeze + verify
    python -m tools.build_release --clean        wipe dist/ first, then freeze + verify
    python -m tools.build_release --installer    freeze + verify + Setup.exe
    python -m tools.build_release --verify-only  verify an existing dist/ without rebuilding

## Why a script rather than a README paragraph

Producing a release is four steps that have to happen in order, and three of them fail silently:

* icons regenerate from artwork, and a stale `.ico` ships the wrong logo;
* PyInstaller succeeds while omitting a module, and the failure surfaces at a user's first click;
* the frozen `--selftest` **cannot be piped**. It reopens stdout on `CONOUT$` so the result goes
  to the terminal and a caller reading the pipe sees nothing -- verified the hard way. Only the
  exit code and `NIMBUS_SELFTEST_LOG` are trustworthy;
* Inno Setup silently produces a working installer with a missing wizard image.

So this checks each one and says which failed.

## The fourth silent failure, which was this script's own

``--verify`` used to be tested before anything else, so ``--clean --verify`` ran *only* the verify
step: PyInstaller never ran, and verify then passed against a ``dist/`` from an earlier session and
printed a clean bill of health. A whole round of manual testing went against a stale binary --
"the update dialog is still there" was true, because it was still the old build.

Two changes came out of that. The flag is now ``--verify-only`` and refuses to be combined with a
build flag rather than quietly winning, and ``step_verify`` compares the executable's timestamp
against every source file it was built from. A build older than its sources now fails the verify
step, which is the check that would have caught this immediately.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "Nimbus"
EXE = DIST / "Nimbus.exe"

BUNDLED_ASSETS = (
    "nimbus_tray.ico",
    "Nimbus tranparent .png",
    "cursor.png",
)
"""Assets ``brand.py`` and ``tray.py`` read at runtime by path.

PyInstaller only ships what ``nimbus.spec`` names, and a missing one degrades quietly -- the
logo becomes a null pixmap and the window simply has no icon. Checked explicitly because
"the build succeeded" does not cover it.
"""

ISCC_CANDIDATES = (
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) /
    "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
    "Inno Setup 6" / "ISCC.exe",
    # winget installs Inno Setup per-user by default, which lands here and nowhere near Program
    # Files. Missing this path is why the installer step reported "not installed" on a machine where
    # it had just been installed successfully.
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
)


def find_iscc() -> Path | None:
    """The Inno Setup compiler, from ``NIMBUS_ISCC``, then ``PATH``, then the usual install dirs."""
    override = os.environ.get("NIMBUS_ISCC", "")
    if override and Path(override).is_file():
        return Path(override)
    on_path = shutil.which("ISCC")
    if on_path:
        return Path(on_path)
    return next((path for path in ISCC_CANDIDATES if path.is_file()), None)


def run(command: list[str], **kwargs) -> int:
    print(f"\n$ {' '.join(str(part) for part in command)}", flush=True)
    return subprocess.call(command, cwd=ROOT, **kwargs)


def step_icons() -> bool:
    """Regenerate the icon and installer artwork from the brand PNGs."""
    print("\n=== 1/4  icons ===")
    from tools import make_icons

    return make_icons.main([]) == 0


def step_freeze(clean: bool) -> bool:
    print("\n=== 2/4  freeze ===")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "nimbus.spec"]
    if clean:
        command.insert(3, "--clean")
    return run(command) == 0


SOURCE_GLOBS = ("*.py", "shell/**/*.py", "nimbus.spec")
"""What the executable is built from. Compared against its timestamp by ``check_not_stale``.

Deliberately not ``**/*.py`` from the root: that would sweep in ``tests/``, ``tools/`` and
``.venv``, and editing a test does not make a build stale."""


def newest_source() -> tuple[Path | None, float]:
    """The most recently modified source file and its mtime."""
    newest: Path | None = None
    newest_at = 0.0
    for pattern in SOURCE_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            if path.name.startswith("_"):
                # Throwaway probes are named `_something.py` by convention here and are never
                # bundled, so one lying around must not report the build as stale.
                continue
            at = path.stat().st_mtime
            if at > newest_at:
                newest, newest_at = path, at
    return newest, newest_at


def check_not_stale() -> bool:
    """Fail when the executable predates the sources it was supposedly built from.

    This is the check that was missing. Verify used to prove the *bundle* was internally
    consistent -- assets present, every module importable -- which a months-old build also passes.
    It never asked whether the build was of the current code, so a skipped freeze step looked
    exactly like a successful one.
    """
    import time

    exe_at = EXE.stat().st_mtime
    source, source_at = newest_source()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exe_at))
    print(f"  built  {stamp}")
    if source is None:
        return True
    source_stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(source_at))
    relative = source.relative_to(ROOT)
    if source_at > exe_at:
        print(f"  stale  FAIL {relative} was modified {source_stamp}, after the build")
        print("         The executable does not contain your latest changes. Run:")
        print("           python -m tools.build_release --clean")
        return False
    print(f"  stale  OK   newest source is {relative} at {source_stamp}")
    return True


def check_licence_key() -> bool:
    """Report whether this build can verify a licence at all.

    A warning, not a failure: a dev build with no key is normal and useful. It is printed because the
    alternative way to discover it is a tester pasting a valid key and being told the licence is
    bad -- the message names the licence, not the build, so the report has to happen here.
    """
    if (ROOT / "licence_key.py").is_file():
        from tools import set_licence_key

        return set_licence_key.report() == 0
    print("  licence  WARN no licence_key.py -- activation will be refused in this build")
    print("           python -m tools.set_licence_key --public-key <key>")
    return False


def step_verify() -> bool:
    """Prove the frozen build is current, imports every runtime module, and carries its assets."""
    print("\n=== 3/4  verify the frozen build ===")
    if not EXE.is_file():
        print(f"FAIL: {EXE} does not exist")
        return False

    ok = check_not_stale()
    check_licence_key()
    assets = DIST / "_internal" / "assets"
    for name in BUNDLED_ASSETS:
        path = assets / name
        if path.is_file():
            print(f"  asset  OK   {name} ({path.stat().st_size:,} bytes)")
        else:
            print(f"  asset  FAIL {name} is missing from the bundle")
            ok = False

    # `--selftest` imports every module named in app._run_selftest. Its stdout is unreachable
    # from here (see the module docstring), so the log file is how the reason gets out.
    log = ROOT / "build" / "frozen-selftest.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.unlink(missing_ok=True)
    environment = {**os.environ, "NIMBUS_SELFTEST_LOG": str(log)}
    code = subprocess.call([str(EXE), "--selftest"], cwd=ROOT, env=environment)
    reported = log.read_text(encoding="utf-8").strip() if log.is_file() else "(no log written)"
    print(f"  selftest exit={code}: {reported}")
    if code != 0:
        ok = False
    return ok


def step_installer() -> bool:
    print("\n=== 4/4  installer ===")
    iscc = find_iscc()
    if iscc is None:
        print("SKIP: Inno Setup 6 is not installed.")
        print("      Get it from https://jrsoftware.org/isdl.php, then re-run with --installer.")
        return True  # not a build failure; the frozen app is still usable

    # `APP_VERSION`, which is what version.py actually exports. This step had never run on this
    # machine -- Inno Setup was not installed, so it returned "SKIP" every time and the wrong name
    # here was invisible until the compiler was available.
    from version import APP_VERSION

    if run([str(iscc), f"/DAppVersion={APP_VERSION}",
            str(ROOT / "installer" / "nimbus.iss")]) != 0:
        return False

    setup = ROOT / "installer" / "Output" / f"Nimbus-Windows-Setup-v{APP_VERSION}.exe"
    if not setup.is_file():
        print(f"FAIL: ISCC reported success but {setup.name} is not there")
        return False
    print(f"  setup  OK   {setup.name} ({setup.stat().st_size / 1_048_576:.0f} MB)")
    print(f"         {setup}")
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verify_only = "--verify-only" in argv or "--verify" in argv
    with_installer = "--installer" in argv
    clean = "--clean" in argv

    if verify_only and (clean or with_installer):
        # Refused rather than resolved. Silently letting one win is what shipped a stale build
        # past a passing verify: `--clean --verify` skipped the freeze entirely.
        print("ERROR: --verify-only cannot be combined with --clean or --installer.")
        print("       To rebuild and verify:  python -m tools.build_release --clean")
        print("       To verify what exists:  python -m tools.build_release --verify-only")
        return 2

    if verify_only:
        return 0 if step_verify() else 1

    if clean and DIST.exists():
        shutil.rmtree(DIST, ignore_errors=True)

    for label, step in (
        ("icons", step_icons),
        ("freeze", lambda: step_freeze(clean)),
        ("verify", step_verify),
    ):
        if not step():
            print(f"\nBUILD FAILED at: {label}")
            return 1

    if with_installer and not step_installer():
        print("\nBUILD FAILED at: installer")
        return 1

    print(f"\nBUILD OK -> {DIST}")
    if not with_installer:
        print("Pass --installer to also produce installer/Output/Setup.exe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
