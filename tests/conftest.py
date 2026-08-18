from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from train_tracker.clock import BRISBANE


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)


@pytest.fixture
def tiny_gtfs_zip(tmp_path: Path) -> Path:
    root = Path(__file__).parents[1] / "fixtures" / "tiny_gtfs"
    target = tmp_path / "tiny_gtfs.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for source in sorted(root.glob("*.txt")):
            archive.write(source, source.name)
    return target


@pytest.fixture
def rail_gtfs_zip(tmp_path: Path) -> Path:
    root = Path(__file__).parents[1] / "fixtures" / "rail_gtfs"
    target = tmp_path / "rail_gtfs.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for source in sorted(root.glob("*.txt")):
            archive.write(source, source.name)
    return target


@pytest.fixture
def bus_gtfs_zip(tmp_path: Path) -> Path:
    root = Path(__file__).parents[1] / "fixtures" / "bus_gtfs"
    target = tmp_path / "bus_gtfs.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for source in sorted(root.glob("*.txt")):
            archive.write(source, source.name)
    return target
