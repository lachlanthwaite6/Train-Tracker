from __future__ import annotations

from PIL import Image

from train_tracker.rendering import colors
from train_tracker.rendering.fonts import draw_text
from train_tracker.rendering.layout import abbreviate


def render_alert(message: str, *, brightness: int = 100) -> Image.Image:
    image = Image.new("RGB", (128, 64), colors.BLACK)
    color = colors.apply_brightness(colors.AMBER, brightness)
    draw_text(image, (39, 5), "ALERT", color, scale=2)
    words = abbreviate(message, 62)
    draw_text(image, (2, 30), words[:31], color, max_width=124)
    draw_text(image, (2, 40), words[31:62], color, max_width=124)
    return image
