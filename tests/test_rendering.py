from __future__ import annotations

import hashlib
from datetime import datetime

from train_tracker.providers.simulated import Scenario, SimulatedProvider
from train_tracker.rendering.layout import abbreviate
from train_tracker.rendering.renderer import MatrixRenderer


def test_long_text_abbreviation_is_bounded() -> None:
    result = abbreviate("A very long destination name beyond the board", 12)
    assert len(result) <= 12
    assert result.endswith(">")


def test_scene_is_exact_128_by_64_and_pixels_are_rgb(now: datetime) -> None:
    snapshot = SimulatedProvider(Scenario.DELAYS).refresh("SIM-CEN", now)
    frame = MatrixRenderer().render(snapshot, now)
    assert frame.size == (128, 64)
    assert frame.mode == "RGB"
    assert all(
        len(pixel) == 3 and all(0 <= channel <= 255 for channel in pixel)
        for pixel in frame.get_flattened_data()
    )


def test_special_states_render(now: datetime) -> None:
    renderer = MatrixRenderer()
    hashes = {
        hashlib.sha256(
            renderer.render(SimulatedProvider(s).refresh("SIM-CEN", now), now).tobytes()
        ).hexdigest()
        for s in (Scenario.EMPTY, Scenario.STALE, Scenario.DISCONNECTED, Scenario.CANCELLATIONS)
    }
    assert len(hashes) == 4


def test_renderer_golden_hash(now: datetime) -> None:
    frame = MatrixRenderer().render(SimulatedProvider().refresh("SIM-CEN", now), now)
    digest = hashlib.sha256(frame.tobytes()).hexdigest()
    assert digest == "cfbcb4e106fd761acb5a10db889747ee8d4c84a117cff39a7515c44c79d23081"
