"""Answer "is the code I just wrote actually inside dist/Nimbus/Nimbus.exe?"

    python -m tools.verify_bundle --marker "orange Nimbus icon"
    python -m tools.verify_bundle --marker focus_visible_only --absent "blue cursor icon"

## Why this exists

A round of manual testing was spent on a stale executable. Every "you did not fix this" was
correct -- the fixes were in the source and not in the binary, because ``build_release``'s
``--verify`` had skipped the freeze step and then passed the *old* build. ``build_release`` now
compares timestamps, which catches that case cheaply. This tool answers the stronger question
directly, by reading the build's own code objects.

## Two things that make the naive version of this useless

**A byte search over ``dist/`` finds nothing either way.** PyInstaller zlib-compresses every
bundled module, so neither the old strings nor the new ones appear as plain bytes. Measured: a
scan of 2,741 files found all seven markers absent, from a build that contained several of them.

**Exact string matching gives false negatives.** Python concatenates adjacent string literals at
compile time, so a message written across four source lines is one constant. Checking set
membership reported every multi-line message as missing. Matching is by substring.
"""
from __future__ import annotations

import argparse
import marshal
import pathlib
import sys
import time
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "Nimbus" / "Nimbus.exe"


def _strings(code: types.CodeType, seen: set[int] | None = None):
    """Every string constant, name and local reachable from ``code``, recursively.

    Names and locals are included as well as constants so an identifier like
    ``_install_hotkey_guard`` is findable -- that is often the cheapest marker for a change that
    added no user-visible text.
    """
    if seen is None:
        seen = set()
    if id(code) in seen:
        return
    seen.add(id(code))
    for const in code.co_consts:
        if isinstance(const, str):
            yield const
        elif isinstance(const, types.CodeType):
            yield from _strings(const, seen)
    yield from code.co_names
    yield from code.co_varnames


def _load(data) -> types.CodeType | None:
    """Marshalled code from an archive entry, with or without PyInstaller's 16-byte header."""
    if isinstance(data, types.CodeType):
        return data
    if isinstance(data, tuple):
        data = data[-1]
    if not isinstance(data, (bytes, bytearray)):
        return None
    for offset in (0, 16):
        try:
            loaded = marshal.loads(bytes(data)[offset:])
        except Exception:
            continue
        if isinstance(loaded, types.CodeType):
            return loaded
    return None


def harvest(exe: pathlib.Path = EXE) -> tuple[str, int]:
    """Every string in the frozen bundle, joined, plus how many modules it came from."""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    reader = CArchiveReader(str(exe))
    pool: set[str] = set()
    modules = 0

    for name in reader.toc:
        try:
            data = reader.extract(name)
        except Exception:
            continue
        if isinstance(data, tuple):
            data = data[-1]

        if str(name).endswith(".pyz"):
            # The PYZ holds every module except the entry point, so skipping it would check
            # roughly one file out of eight thousand.
            temp = ROOT / "build" / "_verify_bundle.pyz"
            temp.parent.mkdir(parents=True, exist_ok=True)
            temp.write_bytes(bytes(data))
            try:
                inner = ZlibArchiveReader(str(temp))
                for module in getattr(inner, "toc", {}):
                    try:
                        code = _load(inner.extract(module))
                    except Exception:
                        continue
                    if code is not None:
                        modules += 1
                        pool.update(_strings(code))
            finally:
                temp.unlink(missing_ok=True)
            continue

        code = _load(data)
        if code is not None:
            modules += 1
            pool.update(_strings(code))

    return "\n".join(pool), modules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--marker", action="append", default=[],
                        help="text that MUST be in the bundle (repeatable)")
    parser.add_argument("--absent", action="append", default=[],
                        help="text that must NOT be in the bundle (repeatable)")
    args = parser.parse_args(argv)

    if not EXE.is_file():
        print(f"FAIL: {EXE} does not exist. Run: python -m tools.build_release --clean")
        return 1
    if not args.marker and not args.absent:
        parser.error("give at least one --marker or --absent")

    stat = EXE.stat()
    print(f"  {EXE.name} {stat.st_size:,} bytes, built "
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))}")
    blob, modules = harvest()
    print(f"  read {modules:,} modules from the bundle\n")

    failures = 0
    for needle in args.marker:
        present = needle in blob
        failures += not present
        print(f"  {'present' if present else 'MISSING'}  {needle!r}")
    for needle in args.absent:
        present = needle in blob
        failures += present
        print(f"  {'STILL THERE' if present else 'gone   '}  {needle!r}")

    print("\n  OK" if not failures else f"\n  {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
