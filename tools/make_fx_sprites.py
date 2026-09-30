#!/usr/bin/env python3
"""Renders the screen-effect sprites in app/fx/ (run once; the PNGs are committed).

Drawn at 2x so they stay sharp on HiDPI screens: glowing sparks per colour, a sparkle star,
glossy balloons with strings, and a shaded heart.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(__file__), "..", "app", "fx")
PALETTE = {"red": (255, 92, 92), "orange": (255, 170, 60), "yellow": (255, 225, 80), "green": (92, 224, 122),
           "blue": (77, 181, 255), "purple": (166, 107, 255), "pink": (255, 107, 208), "gold": (255, 211, 90),
           "white": (255, 250, 235)}


def glow(color, size=96):
    """A spark: a hot white core fading through the colour to nothing."""
    im = Image.new("RGBA", (size, size))
    px = im.load()
    c = size / 2
    for y in range(size):
        for x in range(size):
            d = math.hypot(x + 0.5 - c, y + 0.5 - c) / c
            if d >= 1:
                continue
            a = (1 - d) ** 2.2
            core = max(0.0, 1 - d / 0.22)
            r, g, b = (int(color[i] + (255 - color[i]) * core) for i in range(3))
            px[x, y] = (r, g, b, int(255 * min(1, a * 1.15)))
    return im


def sparkle(color, size=128):
    """A four-pointed sparkle with a soft halo."""
    im = Image.new("RGBA", (size, size))
    halo = glow(color, size).filter(ImageFilter.GaussianBlur(size / 16))
    halo.putalpha(halo.getchannel("A").point(lambda v: v // 2))
    im.alpha_composite(halo)
    d = ImageDraw.Draw(im)
    c = size / 2
    for ang in (0, 90):
        pts = []
        for k in range(4):
            t = math.radians(ang + k * 90)
            L = size * (0.48 if k % 2 == 0 else 0.48)
            pts.append((c + math.cos(t) * L, c + math.sin(t) * L))
        # thin diamond per axis
        a = math.radians(ang)
        w = size * 0.07
        d.polygon([(c + math.cos(a) * c * 0.96, c + math.sin(a) * c * 0.96),
                   (c + math.cos(a + math.pi / 2) * w, c + math.sin(a + math.pi / 2) * w),
                   (c - math.cos(a) * c * 0.96, c - math.sin(a) * c * 0.96),
                   (c - math.cos(a + math.pi / 2) * w, c - math.sin(a + math.pi / 2) * w)],
                  fill=tuple(int(v + (255 - v) * 0.6) for v in color) + (255,))
    core = glow((255, 255, 255), int(size * 0.35))
    im.alpha_composite(core, (int(c - core.width / 2), int(c - core.height / 2)))
    return im


def balloon(color, w=180, h=340):
    """A glossy balloon: shaded body, highlight, knot and a curling string."""
    im = Image.new("RGBA", (w, h))
    body_h = int(w * 1.18)
    body = Image.new("RGBA", (w, body_h))
    px = body.load()
    cx, cy, rx, ry = w / 2, body_h * 0.47, w * 0.46, body_h * 0.46
    lx, ly = cx - rx * 0.35, cy - ry * 0.4          # light source
    for y in range(body_h):
        for x in range(w):
            # a slightly pear-shaped ellipse (narrower towards the knot)
            yy = (y - cy) / ry
            k = 1 - 0.12 * max(0, yy)
            if ((x - cx) / (rx * k)) ** 2 + yy ** 2 > 1:
                continue
            dl = math.hypot(x - lx, y - ly) / (rx * 1.6)
            shade = max(0.55, 1.15 - dl * 0.75)
            r, g, b = (min(255, int(color[i] * shade)) for i in range(3))
            px[x, y] = (r, g, b, 245)
    body = body.filter(ImageFilter.GaussianBlur(0.8))
    # specular highlight
    hl = Image.new("RGBA", (w, body_h))
    ImageDraw.Draw(hl).ellipse([cx - rx * 0.62, cy - ry * 0.78, cx - rx * 0.18, cy - ry * 0.2], fill=(255, 255, 255, 150))
    body.alpha_composite(hl.filter(ImageFilter.GaussianBlur(w / 22)))
    im.alpha_composite(body)
    d = ImageDraw.Draw(im)
    kx, ky = cx, cy + ry * 0.9
    d.polygon([(kx - w * 0.06, ky + w * 0.07), (kx + w * 0.06, ky + w * 0.07), (kx, ky - w * 0.02)],
              fill=tuple(int(v * 0.8) for v in color) + (255,))
    # string: a gentle S curve
    pts = [(kx + math.sin(t / 18) * w * 0.07, ky + w * 0.07 + t) for t in range(0, h - int(ky + w * 0.07))]
    d.line(pts, fill=(220, 220, 220, 200), width=3)
    return im


def heart(color, size=512, shadow=True):
    """A shaded heart with a soft glow."""
    s = size
    mask = Image.new("L", (s, s))
    m = ImageDraw.Draw(mask)
    pts = []
    for i in range(720):
        t = math.pi * 2 * i / 720
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((s / 2 + x * s / 38, s * 0.47 - y * s / 38))
    m.polygon(pts, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    im = Image.new("RGBA", (s, s))
    if shadow:
        g = Image.new("RGBA", (s, s), color + (0,))
        g.putalpha(mask.filter(ImageFilter.GaussianBlur(s / 12)).point(lambda v: int(v * 0.28)))
        im.alpha_composite(g)
    body = Image.new("RGBA", (s, s))
    px = body.load()
    lx, ly = s * 0.36, s * 0.3
    for y in range(s):
        for x in range(s):
            dl = math.hypot(x - lx, y - ly) / (s * 0.75)
            shade = max(0.6, 1.2 - dl)
            px[x, y] = tuple(min(255, int(color[i] * shade)) for i in range(3)) + (255,)
    body.putalpha(mask)
    im.alpha_composite(body)
    hl = Image.new("RGBA", (s, s))
    ImageDraw.Draw(hl).ellipse([s * 0.24, s * 0.2, s * 0.42, s * 0.36], fill=(255, 255, 255, 120))
    hl = hl.filter(ImageFilter.GaussianBlur(s / 40))
    hl.putalpha(Image.composite(hl.getchannel("A"), Image.new("L", (s, s)), mask))
    im.alpha_composite(hl)
    return im


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, col in PALETTE.items():
        glow(col).save(f"{OUT}/glow-{name}.png")
    sparkle(PALETTE["gold"]).save(f"{OUT}/sparkle-gold.png")
    sparkle(PALETTE["white"]).save(f"{OUT}/sparkle-white.png")
    for name in ("red", "orange", "yellow", "green", "blue", "purple", "pink"):
        balloon(PALETTE[name]).save(f"{OUT}/balloon-{name}.png")
    heart((255, 45, 85)).save(f"{OUT}/heart.png")
    heart((255, 90, 120), 96, shadow=False).save(f"{OUT}/heart-small.png")
    print("sprites written to", os.path.abspath(OUT))
