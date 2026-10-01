#!/usr/bin/env python3
"""Builds preview.png, the 1600x900 marketplace card, in the style of the other Pear / Omapager
cards: icon tile, two-line name, tagline and feature line on the left, the window on the right.

The text block sits high on purpose: the marketplace's mini-preview tile crops the bottom 10-20%
of the card, so nothing that matters goes below ~75% of the height.

Run from the repo root:  python3 tools/make_preview.py  (needs docs/window.png and ImageMagick)
"""
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1600, 900
GREEN = (185, 210, 90)
WHITE = (236, 238, 234)
DIM = (150, 156, 150)
FAINT = (108, 114, 108)
FONT = "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-{}.ttf"


def font(size, weight="Regular"):
    return ImageFont.truetype(FONT.format(weight), size)


def background():
    bg = Image.new("RGB", (W, H))
    px = bg.load()
    for y in range(H):
        for x in range(W):
            t = (x / W * 0.55 + y / H * 0.45)
            # a warm green-black, lighter toward the top left - the same family as the other cards
            r = int(30 - 18 * t); g = int(34 - 20 * t); b = int(24 - 14 * t)
            px[x, y] = (max(r, 8), max(g, 9), max(b, 8))
    return bg


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img, (0, 0), mask)
    return out


def main():
    card = background().convert("RGBA")

    # --- the window, right side, a little above centre (the bottom of the card gets cropped)
    win = Image.open("docs/window.png").convert("RGB")
    ww = 850
    wh = round(win.height * ww / win.width)
    win = rounded(win.resize((ww, wh), Image.LANCZOS), 22)
    wx, wy = W - ww - 70, 92
    shadow = Image.new("RGBA", (ww + 120, wh + 120), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([60, 70, ww + 60, wh + 70], 26, fill=(0, 0, 0, 150))
    card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)), (wx - 60, wy - 60))
    card.alpha_composite(win, (wx, wy))
    ImageDraw.Draw(card).rounded_rectangle([wx, wy, wx + ww - 1, wy + wh - 1], 22, outline=(255, 255, 255, 46), width=2)

    # --- icon tile
    tmp = tempfile.mkdtemp()
    subprocess.run(["magick", "-background", "none", "app/icon.svg", "-resize", "104x104", f"{tmp}/icon.png"], check=True)
    icon = Image.open(f"{tmp}/icon.png").convert("RGBA")
    card.alpha_composite(icon, (92, 70))

    d = ImageDraw.Draw(card)
    x = 98
    d.text((x - 4, 192), "Pear", font=font(100, "Bold"), fill=WHITE)
    d.text((x - 4, 292), "Messages", font=font(100, "Bold"), fill=GREEN)
    y = 432
    for line in ("Every bubble, blue", "or green, in a native", "Omarchy window."):
        d.text((x, y), line, font=font(36), fill=DIM)
        y += 47
    y += 14
    for line in ("photos · reactions", "effects · bluetooth"):
        d.text((x, y), line, font=font(27), fill=FAINT)
        y += 36
    card.convert("RGB").save("preview.png", optimize=True)
    print("preview.png", card.size, "text ends at y =", y, f"({y / H:.0%} of the height)")


if __name__ == "__main__":
    main()
