from __future__ import annotations

import re
from typing import Iterable


def parse_detail_fields(value: str) -> list[tuple[str, str]]:
    """Parse ``Campo: Valor;`` without deleting duplicate official fields."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return []
    result: list[tuple[str, str]] = []
    for segment in re.split(r"\s*;\s*", text):
        segment = segment.strip()
        if not segment:
            continue
        if ":" not in segment:
            result.append((segment, ""))
            continue
        key, val = segment.split(":", 1)
        key = re.sub(r":+$", "", key.strip())
        result.append((key, val.lstrip(":").strip()))
    return result


def format_detail_fields(fields: Iterable[tuple[str, str]]) -> str:
    return "; ".join(f"{re.sub(r':+$', '', str(key).strip())}: {str(value).strip()}" for key, value in fields if str(key).strip())
