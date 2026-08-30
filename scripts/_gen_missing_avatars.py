#!/usr/bin/env python3
"""
scripts/_gen_missing_avatars.py — 補上 6 個缺漏頭像（一次性工具）

工單「UI 靈魂本質面板（#2）」：/avatars/{rem,aoi,mai,ram,mahiru,anna}.png 404。
現有頭像（yua/ruka/akane/bryan）是「透明底 + 角色色圓形」的 128x128 RGBA PNG
（無 PIL 依賴，純 zlib + struct 手寫），本工具照同一風格生成缺漏的 6 個。

配色：跟 static/index.html 的 avatarColor() 完全一致（同 hash + 同 palette），
讓新頭像的圓形色塊與 UI 的 SVG fallback 配色無縫接續。

不碰 src/ 核心邏輯、不碰 frozen contract。生成後可直接 commit。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 128
RADIUS = 56  # 圓半徑（同現有頭像：四角透明、中央實色圓）

# 跟 static/index.html avatarColor() 的 palettes 完全一致（順序不可改）
_PALETTES = [
    (0xFF, 0x8B, 0xA7, 0xFF, 0x5F, 0x8F),  # ["#ff8ba7", "#ff5f8f"]
    (0xFF, 0xB3, 0x7D, 0xFF, 0x8A, 0x5C),  # ["#ffb37d", "#ff8a5c"]
    (0xB2, 0x8D, 0xFF, 0x8A, 0x5C, 0xF5),  # ["#b28dff", "#8a5cf5"]
    (0xFF, 0xD2, 0x7D, 0xFF, 0xA9, 0x4D),  # ["#ffd27d", "#ffa94d"]
    (0x7D, 0xD8, 0xC9, 0x4D, 0xB8, 0xA8),  # ["#7dd8c9", "#4db8a8"]
    (0xF7, 0x8F, 0xB3, 0xE0, 0x55, 0x7E),  # ["#f78fb3", "#e0557e"]
    (0xA8, 0xD8, 0xFF, 0x6F, 0xA9, 0xFF),  # ["#a8d8ff", "#6fa9ff"]
    (0xC9, 0xA7, 0xFF, 0x9A, 0x6F, 0xFF),  # ["#c9a7ff", "#9a6fff"]
    (0xFF, 0x9D, 0x8F, 0xF5, 0x6B, 0x5F),  # ["#ff9d8f", "#f56b5f"]
]


def avatar_color(agent_id: str) -> tuple:
    """跟 JS avatarColor(id) 同演算法（31-hash + abs % len(palettes)）。"""
    if agent_id == "bryan":
        return (0x4A, 0x9E, 0xFF, 0x25, 0x63, 0xEB)
    if agent_id == "group":
        return (0xFF, 0x8F, 0xAB, 0xF5, 0x60, 0x7F)
    h = 0
    for c in agent_id:
        h = ((h << 5) - h + ord(c)) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000  # 對齊 JS 的 |0（signed 32-bit）
    return _PALETTES[abs(h) % len(_PALETTES)]


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def make_circle_png(agent_id: str) -> bytes:
    """透明底 + 角色色圓形（對角漸層 light→deep，同 SVG fallback 風格）。"""
    r1, g1, b1, r2, g2, b2 = avatar_color(agent_id)
    cx = cy = SIZE / 2.0
    # scanlines: 每 row 前置 filter byte 0（None）
    rows = []
    for y in range(SIZE):
        row = bytearray()
        row.append(0)  # filter: None
        for x in range(SIZE):
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= RADIUS:
                t = (x + y) / (2.0 * (SIZE - 1))  # 左上 light → 右下 deep
                row += bytes((_lerp(r1, r2, t), _lerp(g1, g2, t), _lerp(b1, b2, t), 255))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    return png


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "static" / "avatars"
    out_dir.mkdir(parents=True, exist_ok=True)
    for agent_id in ["rem", "aoi", "mai", "ram", "mahiru", "anna", "miku"]:
        target = out_dir / f"{agent_id}.png"
        png = make_circle_png(agent_id)
        target.write_bytes(png)
        print(f"[OK] {target.name} ({len(png)} bytes) color={avatar_color(agent_id)}")
    # sanity：PNG signature + IHDR dims
    for agent_id in ["rem", "aoi", "mai", "ram", "mahiru", "anna", "miku"]:
        data = (out_dir / f"{agent_id}.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{agent_id} bad signature"
        w, h, bd, ct = struct.unpack(">IIBB", data[16:26])
        assert (w, h, bd, ct) == (SIZE, SIZE, 8, 6), f"{agent_id} bad IHDR {w}x{h}"
    print("sanity OK: 7 PNGs, 128x128 RGBA")


if __name__ == "__main__":
    main()
