from __future__ import annotations

from PIL import Image

from train_tracker.rendering import colors
from train_tracker.rendering.fonts import draw_text


def render_status(title: str, detail: str = "") -> Image.Image:
    image = Image.new("RGB", (128, 64), colors.BLACK)
    draw_text(image, (3, 15), title[:15], colors.CYAN, scale=2, max_width=122)
    draw_text(image, (3, 43), detail[:31], colors.DIM_WHITE, max_width=124)
    return image
