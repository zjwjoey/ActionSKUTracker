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


class TranslationMemoryRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def exact(self, source_text: str, *, field_name: str | None = None, context_key: str | None = None) -> TMMatch | None:
        source_hash = value_hash(source_text)
        with connect(self.db_path) as db:
            row = db.execute("SELECT source_text,target_text,field_name,context_key,source_hash FROM translation_memory_entries WHERE approval_status='APPROVED' AND source_hash=? AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '') ORDER BY created_at DESC LIMIT 1", (source_hash, field_name, context_key)).fetchone()
        return TMMatch(str(row[0]), str(row[1]), row[2], row[3], str(row[4])) if row else None

    def normalized_exact(self, source_text: str, *, field_name: str | None = None, context_key: str | None = None) -> TMMatch | None:
        normalized = normalize_memory_source(source_text)
        with connect(self.db_path) as db:
            rows = db.execute("SELECT source_text,target_text,field_name,context_key,source_hash FROM translation_memory_entries WHERE approval_status='APPROVED' AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '')", (field_name, context_key)).fetchall()
        for row in rows:
            if normalize_memory_source(str(row[0])) == normalized:
                return TMMatch(str(row[0]), str(row[1]), row[2], row[3], str(row[4]))
        return None

    def suggest(self, source_text: str, *, field_name: str | None = None, context_key: str | None = None, limit: int = 3) -> tuple[TMMatch, ...]:
        normalized = normalize_memory_source(source_text)
        with connect(self.db_path) as db:
            rows = db.execute("SELECT source_text,target_text,field_name,context_key,source_hash FROM translation_memory_entries WHERE approval_status='APPROVED' AND COALESCE(field_name,'')=COALESCE(?, '') AND (context_key=? OR context_key IS NULL) ORDER BY created_at DESC LIMIT 200", (field_name, context_key)).fetchall()
        scored = []
        for row in rows:
            candidate = normalize_memory_source(str(row[0]))
            common = len(set(normalized.split()) & set(candidate.split()))
            score = common / max(1, len(set(normalized.split()) | set(candidate.split())))
            if score > 0:
                scored.append(TMMatch(str(row[0]), str(row[1]), row[2], row[3], str(row[4]), score))
        return tuple(sorted(scored, key=lambda item: (-item.score, item.source_text))[:limit])
