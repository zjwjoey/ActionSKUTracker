"""Structured sidecar for official source labels.

Business remarks remain presentation text.  This artifact preserves every
raw tag token and its identity separately, so a new source label cannot be
silently reduced to a generic ``Nuevo``/``Sostenible`` remark.
"""
from __future__ import annotations

import csv
import hashlib
import io
from typing import Any, Iterable, Mapping

OFFICIAL_LABEL_COLUMNS = [
    "label_id", "sku", "raw_label", "normalized_label", "label_type", "source", "occurrence",
]
_KNOWN = {
    "nuevo": ("Nuevo", "new"), "new": ("Nuevo", "new"),
    "promocion": ("Promoción", "promotion"), "promoción": ("Promoción", "promotion"),
    "sostenible": ("Sostenible", "sustainable"),
    "una opción más sostenible": ("Una opción más sostenible", "sustainable"),
}


def _norm(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def build_official_label_rows(records: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in records:
        sku = str(record.get("sku") or record.get("official_sku") or "").strip()
        occurrence = 0
        for raw in str(record.get("raw_tags") or "").split("|"):
            raw = raw.strip()
            if not raw:
                continue
            occurrence += 1
            normalized = _norm(raw)
            canonical, label_type = _KNOWN.get(normalized, (raw, "unknown"))
            identity = f"{sku}|{occurrence}|{raw}|{label_type}"
            rows.append({
                "label_id": hashlib.sha256(("OFFICIAL_LABEL_V1|" + identity).encode("utf-8")).hexdigest(),
                "sku": sku, "raw_label": raw, "normalized_label": canonical,
                "label_type": label_type, "source": "official_raw_tags", "occurrence": str(occurrence),
            })
    return rows


def render_official_label_sidecar(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=OFFICIAL_LABEL_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


__all__ = ["OFFICIAL_LABEL_COLUMNS", "build_official_label_rows", "render_official_label_sidecar"]
