from __future__ import annotations

from PIL import Image


class RGBMatrixEmulatorOutput:
    """Optional adapter for the third-party RGBMatrixEmulator package."""

    def __init__(self) -> None:
        self._matrix: object | None = None

    def open(self) -> None:
        try:
            from RGBMatrixEmulator import (  # type: ignore[import-not-found]
                RGBMatrix,
                RGBMatrixOptions,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Install RGBMatrixEmulator separately to use this optional output. "
                "The built-in GUI simulator requires no such dependency."
            ) from exc
        options = RGBMatrixOptions()
        options.rows = 64
        options.cols = 128
        self._matrix = RGBMatrix(options=options)

    def present(self, frame: Image.Image) -> None:
        if self._matrix is None:
            raise RuntimeError("RGBMatrixEmulator output is not open")
        self._matrix.SetImage(frame.convert("RGB"), 0, 0)  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._matrix is not None:
            self._matrix.Clear()  # type: ignore[attr-defined]
        self._matrix = None
