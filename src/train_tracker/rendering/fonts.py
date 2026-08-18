from __future__ import annotations

from PIL import Image

from train_tracker.rendering.colors import RGB

# Original compact 3x5 bitmap alphabet. Keeping glyphs in source makes rendering
# identical on macOS, Linux, CI and Raspberry Pi without relying on system fonts.
_RAW: dict[str, tuple[str, ...]] = {
    " ": ("000", "000", "000", "000", "000"),
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "111", "011"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "010", "100", "111"),
    "3": ("110", "001", "010", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "110", "101", "010"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("010", "101", "010", "101", "010"),
    "9": ("010", "101", "011", "001", "110"),
    ":": ("000", "010", "000", "010", "000"),
    ".": ("000", "000", "000", "000", "010"),
    ",": ("000", "000", "000", "010", "100"),
    "-": ("000", "000", "111", "000", "000"),
    "+": ("000", "010", "111", "010", "000"),
    "/": ("001", "001", "010", "100", "100"),
    "!": ("010", "010", "010", "000", "010"),
    "?": ("110", "001", "010", "000", "010"),
    "#": ("101", "111", "101", "111", "101"),
    "=": ("000", "111", "000", "111", "000"),
    "<": ("001", "010", "100", "010", "001"),
    ">": ("100", "010", "001", "010", "100"),
    "[": ("110", "100", "100", "100", "110"),
    "]": ("011", "001", "001", "001", "011"),
    "_": ("000", "000", "000", "000", "111"),
}

CHAR_WIDTH = 3
CHAR_HEIGHT = 5


def text_width(text: str, scale: int = 1) -> int:
    return max(0, len(text) * (CHAR_WIDTH + 1) * scale - scale)


def draw_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    color: RGB,
    *,
    scale: int = 1,
    max_width: int | None = None,
) -> int:
    pixels = image.load()
    if pixels is None:
        raise ValueError("Image does not expose writable pixels")
    x0, y0 = xy
    cursor = x0
    upper = text.upper()
    for char in upper:
        glyph = _RAW.get(char, _RAW["?"])
        glyph_end = cursor + CHAR_WIDTH * scale
        if max_width is not None and glyph_end > x0 + max_width:
            break
        for row, row_bits in enumerate(glyph):
            for column, bit in enumerate(row_bits):
                if bit != "1":
                    continue
                px = cursor + column * scale
                py = y0 + row * scale
                for dy in range(scale):
                    for dx in range(scale):
                        target_x, target_y = px + dx, py + dy
                        if 0 <= target_x < image.width and 0 <= target_y < image.height:
                            pixels[target_x, target_y] = color
        cursor += (CHAR_WIDTH + 1) * scale
    return cursor - x0
