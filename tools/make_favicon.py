"""Render the website favicon from the brand mark, nudged down inside its box.

    python -m tools.make_favicon

Writes ``web/public/favicon.ico`` (16/32/48) and ``web/public/icon.png`` (180, for Apple touch).

## Why the mark needs shifting at all

A browser tab draws the favicon vertically centred in a small box, next to text whose optical centre is a
little lower than its geometric one — so a mark that is *mathematically* centred reads as sitting high.
Every carefully-made favicon compensates for this, and it cannot be done in CSS because a tab is not a
document.

``NUDGE`` is a fraction of the canvas, so the shift is proportional at 16px and at 180px rather than
correct at one size and wrong at the others.

The mark is also scaled to a fraction of the box: a favicon that touches its own edges looks larger than
its neighbours and slightly wrong, and it leaves the shift nowhere to go.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "nimbus_mark.png"
ICO = ROOT / "web" / "public" / "favicon.ico"
PNG = ROOT / "web" / "public" / "icon.png"

NUDGE = 0.06
"""Downward shift, as a fraction of the canvas height."""

FILL = 0.96
"""How much of the box the mark occupies before the shift.

Was 0.86, which made the icon visibly smaller than its neighbours in a tab strip — a real cost for a
margin nobody asked for. At 0.96 it is effectively full size.

The tradeoff, stated because it is unavoidable: **a full-size icon has almost nowhere to move.** The shift
is clamped so artwork is never cropped, so at 0.96 the nudge lands at one pixel at 16 and 32, two at 48,
and about four at 180. Wanting both "as big as possible" and "clearly lower" is wanting the same pixels
twice; this favours the size and takes the shift the box allows."""


def compose(size: int) -> Image.Image:
    mark = Image.open(SOURCE).convert("RGBA")

    # Trim transparent padding first, so `FILL` measures the artwork rather than the artwork plus
    # whatever margin the source file happens to carry.
    box = mark.getbbox()
    if box:
        mark = mark.crop(box)

    target = max(1, int(size * FILL))
    ratio = min(target / mark.width, target / mark.height)
    mark = mark.resize(
        (max(1, round(mark.width * ratio)), max(1, round(mark.height * ratio))),
        Image.LANCZOS,
    )

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    left = (size - mark.width) // 2
    top = (size - mark.height) // 2 + round(size * NUDGE)
    # Clamp, so a large nudge at a small size can never push artwork off the bottom edge.
    top = min(top, size - mark.height)
    canvas.paste(mark, (left, top), mark)
    return canvas


def main() -> int:
    if not SOURCE.is_file():
        print(f"Missing {SOURCE}")
        return 1

    sizes = (16, 32, 48)
    images = [compose(size) for size in sizes]
    # Pillow writes a multi-size .ico from one image plus `sizes`, but that rescales a single render;
    # composing each size separately keeps the nudge proportional and the small sizes sharp.
    images[-1].save(ICO, format="ICO", sizes=[(size, size) for size in sizes], append_images=images[:-1])

    compose(180).save(PNG, format="PNG", optimize=True)

    print(f"Wrote {ICO.relative_to(ROOT)}  {ICO.stat().st_size:,} bytes  sizes={sizes}")
    print(f"Wrote {PNG.relative_to(ROOT)}  {PNG.stat().st_size:,} bytes  180x180")
    print(f"Nudged down {NUDGE:.1%} of the canvas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
