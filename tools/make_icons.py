"""Generate the Windows icon files from the brand artwork.

    python -m tools.make_icons          regenerate, reporting what changed
    python -m tools.make_icons --check  fail if the .ico is stale (for CI)

## Why this is a build tool and not a runtime path

``assets/nimbus_tray.ico`` is the *executable's* icon. Windows reads it out of the PE header for
the taskbar, Alt-Tab, the Start Menu, desktop shortcuts and Explorer -- long before any Python
runs -- so it has to be a real multi-resolution ``.ico`` file on disk, embedded by
``nimbus.spec``'s ``icon=``. ``brand.py`` cannot help: by the time it could, Windows has already
drawn the icon.

## Why every size is explicit

An ``.ico`` holding one large bitmap looks fine at 256px and turns to mush at 16px, because
Explorer downsamples it with no idea which details matter. Shipping each size means Windows picks
the one drawn for the slot it is filling. 16 and 24 are the taskbar and tray; 32 and 48 are
Explorer's list and tile views; 64, 128 and 256 are large icons and the Alt-Tab overlay.

The source is trimmed to its artwork first and padded back to a square, for the reason
``brand.py`` documents: the PNG is a 1536x1024 canvas with the mark floating in the middle, and
scaling it directly gives a mark a third of the size in a box of transparent padding -- which at
16px is a handful of pixels.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

SOURCE = ASSETS / "Nimbus tranparent .png"
"""The orange mark. The same artwork the window title bar and every window icon use, so the
desktop icon cannot drift from the in-app one."""

TRAY_ICO = ASSETS / "nimbus_tray.ico"
"""Kept at its historical name. It is referenced by ``nimbus.spec``, ``installer/nimbus.iss`` and
``tray.py``; renaming it would be a three-file change for no gain, and the *contents* are what
were wrong -- it held the old blue artwork."""

INSTALLER_PNG = ASSETS / "nimbus_installer.png"
"""164x314 for Inno Setup's ``WizardImageFile``. Its own file because the installer wants a tall
banner rather than a square icon."""

INSTALLER_SMALL_PNG = ASSETS / "nimbus_installer_small.png"
"""138x140 for Inno Setup's ``WizardSmallImageFile`` -- the header icon on every wizard page
after the first.

Drawn from the **cursor**, not the full mark. At this size the mark's lockup loses its detail and
reads as a blob, whereas the pointer is a single silhouette and survives being small. Same
reasoning as the chat panel's header using the pointer: where there is no room for the whole
identity, the pointer is the part that still says Nimbus."""

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

PADDING_RATIO = 0.06
"""Breathing room inside the square, as a fraction of the longest edge.

Windows draws icons hard against their bounding box in some surfaces (the taskbar) and inset in
others. A little padding of our own means the mark never looks clipped, and 6% is small enough
that it still fills its slot at 16px."""


CURSOR = ASSETS / "cursor.png"
"""The pointer, for the surfaces too small for the full mark."""


def squared_source(size: int, source: Path | None = None):
    """The trimmed artwork, centred on a transparent square of ``size``.

    Square because an ``.ico`` frame is square. Centring a 557x469 mark rather than stretching it
    is the difference between a logo and a squashed logo.
    """
    from PIL import Image

    image = Image.open(source or SOURCE).convert("RGBA")
    bbox = image.split()[3].getbbox()
    if bbox is not None:
        image = image.crop(bbox)

    inner = max(1, int(size * (1 - PADDING_RATIO * 2)))
    scale = min(inner / image.width, inner / image.height)
    scaled = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(
        scaled,
        ((size - scaled.width) // 2, (size - scaled.height) // 2),
        scaled,
    )
    return canvas


def build_ico(destination: Path = TRAY_ICO) -> Path:
    """Write the multi-resolution icon.

    Each frame is rendered from the source at its own size rather than downsampled from one large
    frame: Lanczos from 1536px straight to 16px keeps far more of the shape than two hops do.
    """
    largest = squared_source(max(ICO_SIZES))
    frames = [squared_source(size) for size in ICO_SIZES]
    largest.save(
        destination,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=frames,
    )
    return destination


def build_installer_banner(destination: Path = INSTALLER_PNG,
                           size: tuple[int, int] = (164, 314)) -> Path:
    """The installer's side banner: the mark on the dark background, not on white.

    Inno Setup's default banner is a white-background stock image. A dark one carrying the mark
    is the first thing a user sees of the product, and it should not be the only place Nimbus
    looks like a generic installer.
    """
    from PIL import Image

    import theme

    width, height = size
    background = Image.new("RGBA", size, theme.parse_hex(theme.BG_BASE) + (255,))

    mark = squared_source(min(width, height) - 40)
    background.paste(
        mark,
        ((width - mark.width) // 2, (height - mark.height) // 3),
        mark,
    )
    background.convert("RGB").save(destination, format="PNG")
    return destination


def build_installer_small(destination: Path = INSTALLER_SMALL_PNG,
                          size: tuple[int, int] = (138, 140)) -> Path:
    """The wizard's small header icon: the pointer on the dark background.

    138x140 is Inno's recommended size for ``WizardStyle=modern`` at high DPI; it scales down to
    the classic 55x55 cleanly. The pointer rather than the mark, because at 55px the mark's
    lockup becomes a blob and the pointer is a single legible silhouette.
    """
    from PIL import Image

    import theme

    width, height = size
    background = Image.new("RGBA", size, theme.parse_hex(theme.BG_BASE) + (255,))
    pointer = squared_source(min(width, height) - 24, source=CURSOR)
    background.paste(
        pointer,
        ((width - pointer.width) // 2, (height - pointer.height) // 2),
        pointer,
    )
    background.convert("RGB").save(destination, format="PNG")
    return destination


def _digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else "absent"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv

    if not SOURCE.is_file():
        print(f"ERROR: source artwork missing: {SOURCE}")
        return 1

    before = _digest(TRAY_ICO)

    if check_only:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            candidate = build_ico(Path(temporary) / "candidate.ico")
            if _digest(candidate) != before:
                print("STALE: assets/nimbus_tray.ico does not match the source artwork.")
                print("       Run: python -m tools.make_icons")
                return 1
        print("OK: the icon matches the source artwork.")
        return 0

    build_ico()
    after = _digest(TRAY_ICO)
    print(f"assets/{TRAY_ICO.name}: {before} -> {after}")
    print(f"  sizes: {', '.join(str(size) for size in ICO_SIZES)}")

    banner = build_installer_banner()
    print(f"assets/{banner.name}: written ({banner.stat().st_size} bytes)")
    small = build_installer_small()
    print(f"assets/{small.name}: written ({small.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
