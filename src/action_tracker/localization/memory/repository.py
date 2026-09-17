from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...database.connection import connect
from ..hashes import value_hash


def normalize_memory_source(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


@dataclass(frozen=True)
class TMMatch:
    source_text: str
    target_text: str
    field_name: str | None
    context_key: str | None
    source_hash: str
    score: float = 1.0
    match_type: str = "EXACT"
    normalization_version: str = "TM_NORMALIZATION_V1"
    family_id: str | None = None


class TranslationMemoryRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def exact(self, source_text: str, *, field_name: str | None = None, family_id: str | None = None, context_key: str | None = None) -> TMMatch | None:
        source_hash = value_hash(source_text)
        try:
            with connect(self.db_path) as db:
                row = db.execute("SELECT source_text,target_text,field_name,family_id,context_key,source_hash,match_type,normalization_version FROM translation_memory_entries WHERE approval_status='APPROVED' AND source_hash=? AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(family_id,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '') ORDER BY created_at DESC LIMIT 1", (source_hash, field_name, family_id, context_key)).fetchone()
        except Exception as exc:
            if "no such table" in str(exc).lower(): return None
            raise
        return TMMatch(str(row[0]), str(row[1]), row[2], row[4], str(row[5]), 1.0, str(row[6] or "EXACT"), str(row[7] or "TM_NORMALIZATION_V1"), row[3]) if row else None

    def normalized_exact(self, source_text: str, *, field_name: str | None = None, family_id: str | None = None, context_key: str | None = None) -> TMMatch | None:
        normalized = normalize_memory_source(source_text)
        try:
            with connect(self.db_path) as db:
                normalized_hash = value_hash(normalized)
                rows = db.execute("SELECT source_text,target_text,field_name,family_id,context_key,source_hash,match_type,normalization_version FROM translation_memory_entries WHERE approval_status='APPROVED' AND normalized_source_hash=? AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(family_id,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '')", (normalized_hash, field_name, family_id, context_key)).fetchall()
                # Backward-compatible fallback for rows created before the
                # additive normalized hash column existed. New rows use the
                # indexed path above; legacy rows are a finite migration tail.
                if not rows:
                    rows = db.execute("SELECT source_text,target_text,field_name,family_id,context_key,source_hash,match_type,normalization_version FROM translation_memory_entries WHERE approval_status='APPROVED' AND normalized_source_hash IS NULL AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(family_id,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '')", (field_name, family_id, context_key)).fetchall()
        except Exception as exc:
            if "no such table" in str(exc).lower(): return None
            raise
        for row in rows:
            if normalize_memory_source(str(row[0])) == normalized:
                return TMMatch(str(row[0]), str(row[1]), row[2], row[4], str(row[5]), 1.0, "NORMALIZED_EXACT", str(row[7] or "TM_NORMALIZATION_V1"), row[3])
        return None

    def suggest(self, source_text: str, *, field_name: str | None = None, family_id: str | None = None, context_key: str | None = None, limit: int = 3) -> tuple[TMMatch, ...]:
        normalized = normalize_memory_source(source_text)
        try:
            with connect(self.db_path) as db:
                rows = db.execute("SELECT source_text,target_text,field_name,family_id,context_key,source_hash,match_type,normalization_version FROM translation_memory_entries WHERE approval_status='APPROVED' AND COALESCE(field_name,'')=COALESCE(?, '') AND (family_id=? OR family_id IS NULL) AND (context_key=? OR context_key IS NULL) ORDER BY created_at DESC LIMIT 200", (field_name, family_id, context_key)).fetchall()
        except Exception as exc:
            if "no such table" in str(exc).lower(): return ()
            raise
        scored = []
        for row in rows:
            candidate = normalize_memory_source(str(row[0]))
            common = len(set(normalized.split()) & set(candidate.split()))
            score = common / max(1, len(set(normalized.split()) | set(candidate.split())))
            if score > 0:
                scored.append(TMMatch(str(row[0]), str(row[1]), row[2], row[4], str(row[5]), score, "FUZZY", str(row[7] or "TM_NORMALIZATION_V1"), row[3]))
        return tuple(sorted(scored, key=lambda item: (-item.score, item.source_text))[:limit])
