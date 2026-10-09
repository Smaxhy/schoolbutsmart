"""Generate the PWA icons (pure standard library, no Pillow needed).

Usage: python scripts/make_icons.py
"""

import math
import os
import struct
import zlib

BG = (31, 33, 37)
ACCENT = (122, 162, 247)
OUT = os.path.join(os.path.dirname(__file__), "..", "web", "icons")


def dist_to_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def coverage(x, y):
    """Fraction (0..1) of the unit square point (x, y) covered by the ring + check mark."""
    ring = abs(math.hypot(x - 0.5, y - 0.5) - 0.28) < 0.04
    check = min(
        dist_to_segment(x, y, 0.36, 0.52, 0.46, 0.62),
        dist_to_segment(x, y, 0.46, 0.62, 0.65, 0.40),
    ) < 0.04
    return ring or check


def render(size, samples=3):
    rows = []
    for j in range(size):
        row = bytearray([0])  # PNG filter type 0
        for i in range(size):
            hits = sum(
                coverage((i + (sx + 0.5) / samples) / size, (j + (sy + 0.5) / samples) / size)
                for sx in range(samples) for sy in range(samples)
            ) / (samples * samples)
            row += bytes(round(BG[c] + (ACCENT[c] - BG[c]) * hits) for c in range(3))
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, size):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(render(size), 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        write_png(os.path.join(OUT, name), size)
        print("wrote", name)
