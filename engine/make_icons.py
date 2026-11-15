#!/usr/bin/env python3
"""PWA 아이콘 생성 (PIL). 실행: python3 engine/make_icons.py"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "icons")
os.makedirs(OUT, exist_ok=True)

BG_TOP = (12, 17, 23)
BG_BOTTOM = (18, 30, 40)
GREEN = (61, 220, 151)
AMBER = (255, 200, 87)


def vertical_gradient(size, top, bottom):
    img = Image.new("RGB", (1, size), top)
    px = img.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        px[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return img.resize((size, size))


def draw_mark(img, size, pad_ratio=0.0):
    d = ImageDraw.Draw(img, "RGBA")
    pad = int(size * pad_ratio)
    inner = size - pad * 2

    def X(v):
        return pad + int(inner * v)

    # 상승 캔들 3개 (몸통 + 위아래 꼬리)
    candles = [
        (0.18, 0.58, 0.72, 0.52, 0.78, GREEN),
        (0.42, 0.42, 0.58, 0.36, 0.64, GREEN),
        (0.66, 0.26, 0.42, 0.20, 0.48, GREEN),
    ]
    w = int(inner * 0.14)
    for cx, top, bottom, wick_top, wick_bottom, color in candles:
        x = X(cx)
        # 꼬리
        d.line([(x, X(wick_top)), (x, X(wick_bottom))], fill=color, width=max(2, int(inner * 0.022)))
        # 몸통
        d.rounded_rectangle(
            [x - w // 2, X(top), x + w // 2, X(bottom)],
            radius=max(2, w // 4), fill=color,
        )
    # 신호등 점(호박색)
    r = int(inner * 0.055)
    d.ellipse([X(0.85) - r, X(0.16) - r, X(0.85) + r, X(0.16) + r], fill=AMBER)


def make(size: int, name: str, maskable: bool = False):
    img = vertical_gradient(size, BG_TOP, BG_BOTTOM).convert("RGBA")
    if size >= 192:
        draw_mark(img, size, 0.16 if maskable else 0.08)
    else:
        draw_mark(img, size, 0.06)
    if not maskable:
        # 둥근 모서리 (maskable 은 꽉 찬 사각형 유지)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        img = out
    path = os.path.join(OUT, name)
    img.save(path)
    print("saved", path, img.size)


if __name__ == "__main__":
    make(192, "icon-192.png")
    make(512, "icon-512.png")
    make(512, "icon-maskable-512.png", maskable=True)
    make(180, "apple-touch-icon.png")
