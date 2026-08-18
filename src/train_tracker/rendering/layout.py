from __future__ import annotations

_WORDS = {
    "STATION": "STN",
    "JUNCTION": "JCT",
    "DOMESTIC": "DOM",
    "INTERNATIONAL": "INTL",
    "BRISBANE": "BNE",
    "VARSITY": "VARS",
    "LAKES": "LKS",
    "ROSEWOOD": "ROSEWD",
}


def abbreviate(text: str, max_chars: int) -> str:
    normalized = " ".join(text.upper().split())
    for word, replacement in _WORDS.items():
        normalized = normalized.replace(word, replacement)
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 1:
        return normalized[:max_chars]
    words = normalized.split()
    if len(words) > 1:
        compact = " ".join(word if len(word) <= 5 else word[:4] for word in words)
        if len(compact) <= max_chars:
            return compact
        normalized = compact
    return normalized[: max_chars - 1] + ">"
