from __future__ import annotations

import re
import unicodedata


def normalize_target_text(value: str) -> str:
    """Conservative Chinese display normalization, not semantic rewriting."""
    text = unicodedata.normalize("NFC", str(value or "")).replace("\u00a0", " ")
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = text.replace("××", "×").replace("，", "，")
    return re.sub(r" {2,}", " ", text).strip()
