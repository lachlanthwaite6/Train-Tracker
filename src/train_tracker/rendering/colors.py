from __future__ import annotations

from typing import Final

RGB = tuple[int, int, int]

BLACK: Final[RGB] = (0, 0, 0)
WHITE: Final[RGB] = (235, 245, 255)
DIM_WHITE: Final[RGB] = (120, 135, 145)
CYAN: Final[RGB] = (0, 210, 255)
BLUE: Final[RGB] = (40, 95, 255)
GREEN: Final[RGB] = (40, 235, 105)
AMBER: Final[RGB] = (255, 175, 20)
RED: Final[RGB] = (255, 55, 45)
MAGENTA: Final[RGB] = (230, 70, 255)
GREY: Final[RGB] = (55, 65, 70)


def apply_brightness(color: RGB, brightness: int) -> RGB:
    factor = max(0, min(100, brightness)) / 100.0
    return tuple(round(channel * factor) for channel in color)  # type: ignore[return-value]
