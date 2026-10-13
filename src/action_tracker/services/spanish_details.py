"""Source-preserving Spanish detail formatting shared by extraction/export."""
from __future__ import annotations

import re
from typing import Any

# Only damaged compound labels observed in official specification tables.
# Values containing a colon, free prose, and conflicting facts are preserved.
_COMPOUND = re.compile(
    r"^(Ancho|Altura|Capacidad|Color|Forma|Longitud|Material|Número de piezas|"
    r"Peso|Potencia|Tamaño|Tipo de producto):\s*((?:de|del)\s+[^:;\n]+):\s*(.*)$",
    re.IGNORECASE,
)


def repair_detail_label(segment: str) -> str:
    match = _COMPOUND.fullmatch(segment.strip())
    if not match:
        return segment.strip()
    return f"{match[1]} {match[2].strip()}: {match[3].strip()}"


def project_spanish_details(value: Any) -> str | None:
    """Repair label punctuation and suppress exact display duplicates only.

    No unit inference, numeric equivalence, changed-value selection or spelling
    correction is allowed. Raw collection evidence and stored facts stay intact.
    """
    if value is None:
        return None
    # Reuse the collector's finite official-label delimiter contract. This
    # repairs legacy ``Sin lactosa No`` without inventing keys from prose.
    from .normalization import normalize_official_text
    text = normalize_official_text(value, field="details") or ""
    output: list[str] = []
    seen: set[str] = set()
    for part in re.split(r";|\n", text):
        segment = repair_detail_label(part)
        if not segment:
            continue
        # Only explicit key/value rows are deduplicated, never free prose.
        if ":" in segment and segment in seen:
            continue
        output.append(segment)
        if ":" in segment:
            seen.add(segment)
    return "; ".join(output) or None


def spanish_source_findings(description: Any, details: Any) -> list[dict[str, str]]:
    """Identify known corrupt source words; never guess their replacements."""
    findings: list[dict[str, str]] = []
    if re.search(r"\binoloras\b", str(description or ""), re.IGNORECASE):
        findings.append({"field": "desc_es", "code": "SOURCE_PROSE_TYPO", "token": "inoloras"})
    if re.search(r"\bSustancia\s*:\s*[^;\n]*\bVálido\b", str(details or ""), re.IGNORECASE):
        findings.append({"field": "details_es", "code": "SOURCE_PHYSICAL_STATE_INVALID", "token": "Válido"})
    return findings
