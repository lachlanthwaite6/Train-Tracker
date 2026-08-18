from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{key}.json"
        staging = target.with_suffix(".json.new")
        staging.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        staging.replace(target)

    def get(self, key: str) -> dict[str, Any] | None:
        target = self.directory / f"{key}.json"
        if not target.exists():
            return None
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
