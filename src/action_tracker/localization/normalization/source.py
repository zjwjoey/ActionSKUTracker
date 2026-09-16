from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Mapping


def normalize_source_text(value: Any) -> str:
    """Mechanical normalization only; never changes product meaning."""
    text = "" if value is None else html.unescape(str(value))
    text = unicodedata.normalize("NFC", text).replace("\u00a0", " ")
    text = re.sub(r"[\t\r\n]+", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def normalize_source_fields(fields: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): normalize_source_text(value) for key, value in fields.items()}
