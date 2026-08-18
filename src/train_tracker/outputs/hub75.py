from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True, slots=True)
class Hub75Options:
    rows: int = 64
    cols: int = 128
    chain_length: int = 1
    parallel: int = 1
    brightness: int = 60
    gpio_slowdown: int = 2
    hardware_mapping: str = "regular"
    pwm_bits: int = 11
    limit_refresh_hz: int = 0
    pixel_mapper: str = ""


class Hub75Output:
    """Guarded adapter for hzeller/rpi-rgb-led-matrix.

    The Pi-only dependency is intentionally imported inside ``open`` so installing
    and running the desktop application never requires GPIO packages.
    """

    def __init__(self, options: Hub75Options | None = None) -> None:
        self.options = options or Hub75Options()
        self._matrix: object | None = None

    def open(self) -> None:
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "HUB75 output requires hzeller/rpi-rgb-led-matrix on a supported "
                "Raspberry Pi. Use the desktop GUI or PNG output on this computer."
            ) from exc
        settings = RGBMatrixOptions()
        settings.rows = self.options.rows
        settings.cols = self.options.cols
        settings.chain_length = self.options.chain_length
        settings.parallel = self.options.parallel
        settings.brightness = self.options.brightness
        settings.gpio_slowdown = self.options.gpio_slowdown
        settings.hardware_mapping = self.options.hardware_mapping
        settings.pwm_bits = self.options.pwm_bits
        if self.options.limit_refresh_hz:
            settings.limit_refresh_rate_hz = self.options.limit_refresh_hz
        if self.options.pixel_mapper:
            settings.pixel_mapper_config = self.options.pixel_mapper
        self._matrix = RGBMatrix(options=settings)

    def present(self, frame: Image.Image) -> None:
        if self._matrix is None:
            raise RuntimeError("HUB75 output is not open")
        if frame.size != (128, 64):
            raise ValueError(f"Expected 128x64 frame, got {frame.size}")
        self._matrix.SetImage(frame.convert("RGB"), 0, 0)  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._matrix is not None:
            self._matrix.Clear()  # type: ignore[attr-defined]
        self._matrix = None
