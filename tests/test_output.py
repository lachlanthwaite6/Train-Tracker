from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from train_tracker.outputs.base import OutputContext
from train_tracker.outputs.image_file import ImageFileOutput


def test_image_output_lifecycle(tmp_path: Path) -> None:
    target = tmp_path / "frames" / "frame.png"
    output = ImageFileOutput(target)
    with pytest.raises(RuntimeError):
        output.present(Image.new("RGB", (128, 64)))
    with OutputContext(output) as context:
        context.present(Image.new("RGB", (128, 64), "red"))
    assert target.exists()
    assert Image.open(target).size == (128, 64)
    with pytest.raises(RuntimeError):
        output.present(Image.new("RGB", (128, 64)))
