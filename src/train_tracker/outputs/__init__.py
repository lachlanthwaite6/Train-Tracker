"""Framebuffer output adapters."""

from train_tracker.outputs.base import FrameOutput
from train_tracker.outputs.image_file import ImageFileOutput

__all__ = ["FrameOutput", "ImageFileOutput"]
