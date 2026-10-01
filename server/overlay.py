"""The reconnect notice: a line of text drawn into a frame of palette indices.

The text is drawn with a built-in 5x7 bitmap font straight into the index array,
so it needs no imaging library. Indices refer to the fixed 3-3-2 palette
(`media.default_palette`): 0 is black, 255 is white, 1 is a dark blue.
"""

from __future__ import annotations

from . import frames

NOTICE_TEXT = "RECONNECTING..."

BLACK = 0
WHITE = 255
DARK_BLUE = 1

_GLYPHS: dict[str, tuple[str, ...]] = {
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    ".": ("..", "..", "..", "..", "..", "##", "##"),
}
_GLYPH_ROWS = 7
_LETTER_GAP = 1
_BOX_PAD = 8


def _text_columns(text: str) -> int:
    return sum(len(_GLYPHS[c][0]) for c in text) + _LETTER_GAP * (len(text) - 1)


def reconnect_frame(base: bytes | None) -> bytes:
    """`base` with the notice drawn on a black box at its centre.

    With no `base` -- nothing has been shown yet -- the notice sits on a solid
    dark-blue frame.
    """
    if base is not None and len(base) != frames.FRAME_PIXELS:
        raise ValueError(f"frame is {len(base)} bytes, expected {frames.FRAME_PIXELS}")
    width, height = frames.WIDTH, frames.HEIGHT
    pixels = bytearray(base) if base is not None else bytearray([DARK_BLUE]) * frames.FRAME_PIXELS
    columns = _text_columns(NOTICE_TEXT)
    scale = max(1, (width - 4 * _BOX_PAD) // columns)
    text_w, text_h = columns * scale, _GLYPH_ROWS * scale
    left, top = (width - text_w) // 2, (height - text_h) // 2

    if base is not None:
        for y in range(max(0, top - _BOX_PAD), min(height, top + text_h + _BOX_PAD)):
            row = y * width
            for x in range(max(0, left - _BOX_PAD), min(width, left + text_w + _BOX_PAD)):
                pixels[row + x] = BLACK

    x0 = left
    for char in NOTICE_TEXT:
        glyph = _GLYPHS[char]
        for gy, line in enumerate(glyph):
            for gx, cell in enumerate(line):
                if cell != "#":
                    continue
                for dy in range(scale):
                    row = (top + gy * scale + dy) * width
                    for dx in range(scale):
                        pixels[row + x0 + gx * scale + dx] = WHITE
        x0 += (len(glyph[0]) + _LETTER_GAP) * scale
    return bytes(pixels)
