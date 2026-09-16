from __future__ import annotations

from dataclasses import dataclass

from ...database.connection import connect


@dataclass(frozen=True)
class TermHint:
    source_term: str
    target_term: str
    scope: str
    priority: int = 0
    do_not_translate: bool = False


class TerminologyRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def resolve(self, source_text: str, *, scope: str = "GLOBAL", limit: int = 20) -> tuple[TermHint, ...]:
        source = str(source_text or "")
        with connect(self.db_path) as db:
            rows = db.execute("SELECT source_term,target_term,scope,notes FROM terminology_entries WHERE approval_status='APPROVED' AND (scope=? OR scope='GLOBAL') ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END, LENGTH(source_term) DESC, source_term LIMIT ?", (scope, scope, limit)).fetchall()
        return tuple(TermHint(str(row[0]), str(row[1]), str(row[2]), 1 if str(row[2]) == scope else 0, False) for row in rows if str(row[0]) and str(row[0]).casefold() in source.casefold())

    def as_qwen_options(self, source_text: str, *, scope: str = "GLOBAL") -> list[dict[str, str]]:
        return [{"source": item.source_term, "target": item.target_term} for item in self.resolve(source_text, scope=scope)]
