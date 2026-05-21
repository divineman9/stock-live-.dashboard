"""Generate PWA icon assets for the Stock Live Dashboard.

Produces three PNGs under ``static/icons/``:

* ``icon-192.png`` (192x192) - any-purpose icon used by manifest.json.
* ``icon-512.png`` (512x512) - any-purpose icon used by manifest.json.
* ``icon-maskable-512.png`` (512x512) - maskable icon. Visual content is
  constrained to the inner 80% safe area per the W3C maskable icon
  recommendation, so platforms that mask the icon to circles, squircles,
  or other shapes never clip the wordmark.

Theme color ``#0f172a`` matches ``manifest.json``'s ``theme_color`` /
``background_color`` and the dashboard's dark slate background.

Run from the repository root::

    python -m tools.make_icons

The script is deterministic; running it again overwrites the PNGs with
byte-identical (modulo PNG metadata timestamps) output, so the icons are
reproducible and can be committed alongside this script.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- Configuration -------------------------------------------------------

# Slate-900 from the dashboard palette; matches manifest.json theme_color.
BG_COLOR = (15, 23, 42)          # #0f172a
FG_COLOR = (255, 255, 255)       # white wordmark
ACCENT_COLOR = (16, 185, 129)    # #10b981 emerald-500, dashboard "positive"

# Maskable icon safe area: inner 80% (per W3C maskable icon guidance,
# https://www.w3.org/TR/appmanifest/#icon-masks).
MASKABLE_SAFE_FRACTION = 0.80

WORDMARK = "SD"

REPO_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = REPO_ROOT / "static" / "icons"


# --- Font selection ------------------------------------------------------

def _load_font(target_px: int) -> ImageFont.ImageFont:
    """Return a TrueType font sized so capital letters are roughly
    ``target_px`` tall. Falls back to PIL's default bitmap font if no
    TTF is available (e.g. on a stripped-down CI image)."""

    # Try a few common fonts likely to ship on Windows / macOS / Linux.
    candidates = [
        "arialbd.ttf",
        "Arial Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "LiberationSans-Bold.ttf",
        "seguibl.ttf",
        "arial.ttf",
        "DejaVuSans.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, target_px)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# --- Drawing primitives --------------------------------------------------

def _draw_centered_text(
    img: Image.Image,
    text: str,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
) -> None:
    """Draw ``text`` centered inside ``box`` (left, top, right, bottom)."""
    left, top, right, bottom = box
    box_w = right - left
    box_h = bottom - top

    # Pick a font size that fits the box. Start large, shrink until both
    # text width and height fit within ~95% of the box.
    target = int(box_h * 0.85)
    font = _load_font(target)
    draw = ImageDraw.Draw(img)

    while target > 8:
        font = _load_font(target)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_w <= box_w * 0.95 and text_h <= box_h * 0.95:
            break
        target -= 4

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    # bbox[0]/bbox[1] account for the font's internal offset.
    x = left + (box_w - text_w) // 2 - bbox[0]
    y = top + (box_h - text_h) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=color)


def _draw_accent_bar(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Draw a thin emerald accent bar under the wordmark, evoking a
    rising chart line."""
    left, top, right, bottom = box
    draw = ImageDraw.Draw(img)
    bar_h = max(2, (bottom - top) // 24)
    y0 = bottom - bar_h
    draw.rectangle((left, y0, right, y0 + bar_h), fill=ACCENT_COLOR)


# --- Icon builders -------------------------------------------------------

def _make_standard(size: int) -> Image.Image:
    """Build a non-maskable icon: wordmark fills most of the canvas."""
    img = Image.new("RGB", (size, size), BG_COLOR)
    # Use the full canvas with a small inset so the wordmark doesn't
    # touch the edges.
    inset = int(size * 0.12)
    box = (inset, inset, size - inset, size - inset)
    _draw_centered_text(img, WORDMARK, box, FG_COLOR)
    _draw_accent_bar(img, (inset, inset, size - inset, size - inset))
    return img


def _make_maskable(size: int) -> Image.Image:
    """Build a maskable icon: wordmark fits inside the inner 80% safe
    area so platform masks (circle, squircle, rounded square) never
    clip the visible content."""
    img = Image.new("RGB", (size, size), BG_COLOR)
    margin = int(size * (1 - MASKABLE_SAFE_FRACTION) / 2)
    box = (margin, margin, size - margin, size - margin)
    _draw_centered_text(img, WORDMARK, box, FG_COLOR)
    _draw_accent_bar(img, box)
    return img


# --- Entry point ---------------------------------------------------------

def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        (ICONS_DIR / "icon-192.png", _make_standard(192)),
        (ICONS_DIR / "icon-512.png", _make_standard(512)),
        (ICONS_DIR / "icon-maskable-512.png", _make_maskable(512)),
    ]

    for path, img in targets:
        img.save(path, format="PNG", optimize=True)
        size_kb = os.path.getsize(path) / 1024
        print(f"wrote {path.relative_to(REPO_ROOT)} ({img.size[0]}x{img.size[1]}, {size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
