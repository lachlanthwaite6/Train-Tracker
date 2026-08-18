from __future__ import annotations

from enum import StrEnum

from PIL import Image, ImageDraw

from train_tracker.rendering import colors
from train_tracker.rendering.fonts import draw_text


class DiagnosticPattern(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    WHITE = "white"
    GRADIENT = "gradient"
    CHECKERBOARD = "checkerboard"
    COORDINATES = "coordinates"
    MOVING_BORDER = "moving-border"
    ORIENTATION = "orientation"


def render_diagnostic(pattern: str | DiagnosticPattern, frame_number: int = 0) -> Image.Image:
    selected = DiagnosticPattern(pattern)
    solid = {
        DiagnosticPattern.RED: colors.RED,
        DiagnosticPattern.GREEN: colors.GREEN,
        DiagnosticPattern.BLUE: colors.BLUE,
        DiagnosticPattern.WHITE: colors.WHITE,
    }
    if selected in solid:
        return Image.new("RGB", (128, 64), solid[selected])
    image = Image.new("RGB", (128, 64), colors.BLACK)
    draw = ImageDraw.Draw(image)
    if selected == DiagnosticPattern.GRADIENT:
        for x in range(128):
            level = round(x / 127 * 255)
            draw.line((x, 0, x, 63), fill=(level, level, level))
    elif selected == DiagnosticPattern.CHECKERBOARD:
        for y in range(64):
            for x in range(128):
                if (x + y) % 2 == 0:
                    draw.point((x, y), fill=colors.WHITE)
    elif selected == DiagnosticPattern.COORDINATES:
        draw.rectangle((0, 0, 127, 63), outline=colors.WHITE)
        draw.line((64, 0, 64, 63), fill=colors.BLUE)
        draw.line((0, 32, 127, 32), fill=colors.BLUE)
        for x in range(0, 128, 16):
            draw_text(image, (x + 1, 2), str(x), colors.GREEN, max_width=14)
        for y in range(0, 64, 16):
            draw_text(image, (2, y + 8), str(y), colors.AMBER, max_width=11)
    elif selected == DiagnosticPattern.MOVING_BORDER:
        draw.rectangle((0, 0, 127, 63), outline=colors.GREY)
        perimeter = 2 * (128 + 64) - 4
        position = frame_number % perimeter
        if position < 128:
            point = (position, 0)
        elif position < 128 + 63:
            point = (127, position - 127)
        elif position < 128 + 63 + 127:
            point = (127 - (position - 190), 63)
        else:
            point = (0, 63 - (position - 317))
        draw.point(point, fill=colors.RED)
    else:
        draw.rectangle((0, 0, 127, 63), outline=colors.WHITE)
        draw.polygon(((2, 2), (20, 2), (2, 20)), fill=colors.RED)
        draw.polygon(((125, 2), (107, 2), (125, 20)), fill=colors.GREEN)
        draw.polygon(((2, 61), (20, 61), (2, 43)), fill=colors.BLUE)
        draw.polygon(((125, 61), (107, 61), (125, 43)), fill=colors.WHITE)
        draw_text(image, (39, 27), "TOP", colors.CYAN, scale=2)
    return image
