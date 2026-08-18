from __future__ import annotations

from pathlib import Path

from PIL import Image


class ImageFileOutput:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._opened = False

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._opened = True

    def present(self, frame: Image.Image) -> None:
        if not self._opened:
            raise RuntimeError("Output is not open")
        if frame.size != (128, 64):
            raise ValueError(f"Expected 128x64 frame, got {frame.size}")
        frame.save(self.path, format="PNG")

    def close(self) -> None:
        self._opened = False
