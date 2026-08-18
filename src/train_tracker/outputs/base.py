from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from PIL import Image


class FrameOutput(Protocol):
    def open(self) -> None: ...

    def present(self, frame: Image.Image) -> None: ...

    def close(self) -> None: ...


class OutputContext:
    def __init__(self, output: FrameOutput) -> None:
        self.output = output

    def __enter__(self) -> Self:
        self.output.open()
        return self

    def present(self, frame: Image.Image) -> None:
        self.output.present(frame)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.output.close()
