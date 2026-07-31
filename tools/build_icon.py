"""Rebuild the app icon from the source artwork.

The original AppIcon.png was a 1024px canvas with an opaque near-white
background and the purple squircle filling only the middle ~60%, so macOS
drew a small icon inside a white tile. This crops to the artwork, makes
everything outside the rounded square transparent, and emits an .icns.

Usage:
    python tools/build_icon.py source.png
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANVAS = 1024
# Fraction of the canvas the artwork occupies. macOS app icons leave a
# little breathing room; 0.94 keeps it large without clipping in the Dock.
ARTWORK_SCALE = 0.94
CORNER_RATIO = 0.225  # Apple's squircle is close to this
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]
SATURATION_THRESHOLD = 40
# The source art sits on a light background with a drop shadow. Cropping to
# the coloured bounding box keeps a pale halo along the edges, so shave a
# little off each side before masking.
EDGE_TRIM = 0.02


def find_artwork_box(image):
    """Bounding box of the coloured artwork, ignoring background and shadow.

    The background and its drop shadow are near-grey (R≈G≈B); the squircle
    is strongly coloured, so the channel spread separates them cleanly.
    """
    pixels = np.asarray(image.convert("RGB"), dtype=np.int16)
    spread = pixels.max(axis=2) - pixels.min(axis=2)
    mask = Image.fromarray(
        np.where(spread > SATURATION_THRESHOLD, 255, 0).astype(np.uint8)
    )
    return mask.getbbox()


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)],
                           radius=radius, fill=255)
    return mask


def build(source_path):
    source = Image.open(source_path).convert("RGBA")
    box = find_artwork_box(source)
    if box is None:
        raise SystemExit("Could not find coloured artwork in the source image")
    print(f"Artwork bounding box: {box} (source {source.size})")

    # Square the crop on the smaller side, centred, so the shadow-inflated
    # axis is trimmed rather than letterboxed.
    left, top, right, bottom = box
    side = min(right - left, bottom - top)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    half = side // 2
    trim = int(side * EDGE_TRIM)
    square = source.crop((cx - half + trim, cy - half + trim,
                          cx + half - trim, cy + half - trim))
    print(f"Squared+trimmed crop: {square.size}")

    target = int(CANVAS * ARTWORK_SCALE)
    square = square.resize((target, target), Image.LANCZOS)
    square.putalpha(rounded_mask(target, int(target * CORNER_RATIO)))

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    offset = (CANVAS - target) // 2
    canvas.paste(square, (offset, offset), square)
    return canvas


def write_icns(canvas, iconset_dir, icns_path):
    iconset_dir.mkdir(parents=True, exist_ok=True)
    for size in ICNS_SIZES:
        canvas.resize((size, size), Image.LANCZOS).save(
            iconset_dir / f"icon_{size}x{size}.png"
        )
        double = size * 2
        if double <= CANVAS:
            canvas.resize((double, double), Image.LANCZOS).save(
                iconset_dir / f"icon_{size}x{size}@2x.png"
            )
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )
    print(f"Wrote {icns_path}")


def main():
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        PROJECT_ROOT / "assets" / "AppIcon-source.png"
    )
    canvas = build(source_path)

    assets = PROJECT_ROOT / "assets"
    assets.mkdir(exist_ok=True)
    png_path = assets / "AppIcon.png"
    canvas.save(png_path)
    print(f"Wrote {png_path}")

    write_icns(canvas, assets / "AppIcon.iconset", assets / "AppIcon.icns")


if __name__ == "__main__":
    main()
