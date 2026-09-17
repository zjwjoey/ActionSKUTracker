from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...database.connection import connect


@dataclass(frozen=True)
class TermHint:
    source_term: str
    target_term: str
    scope: str
    priority: int = 0
    do_not_translate: bool = False
    field_scope: str | None = None
    cat1_scope: str | None = None
    cat2_scope: str | None = None
    product_type_scope: str | None = None
    family_scope: str | None = None
    context_key: str | None = None
    match_mode: str = "SUBSTRING"
    keep_original: bool = False
    forbidden_target: str | None = None
    term_id: str | None = None


@dataclass(frozen=True)
class TerminologyConflict:
    source_term: str
    target_terms: tuple[str, ...]
    specificity: int
    priority: int
    term_ids: tuple[str, ...] = ()


class TerminologyRepository:
    def __init__(self, db_path):
        self.db_path = db_path
        self.last_conflicts: tuple[TerminologyConflict, ...] = ()

    def resolve(self, source_text: str, *, scope: str = "GLOBAL", field_name: str | None = None,
                cat1: str | None = None, cat2: str | None = None, product_type: str | None = None,
                family_id: str | None = None,
                context_key: str | None = None, limit: int = 20) -> tuple[TermHint, ...]:
        source = str(source_text or "")
        try:
            with connect(self.db_path) as db:
                rows = db.execute("""SELECT term_id,source_term,target_term,scope,priority,do_not_translate,
                field_scope,cat1_scope,cat2_scope,product_type_scope,family_scope,context_key,match_mode,
                case_sensitive,keep_original,forbidden_target FROM (SELECT term_id,source_term,target_term,scope,priority,do_not_translate,
                field_scope,cat1_scope,cat2_scope,product_type_scope,family_scope,context_key,match_mode,
                case_sensitive,keep_original,forbidden_target,approval_status FROM terminology_entries
                UNION ALL SELECT term_id,source_term,target_term,scope,priority,do_not_translate,
                field_scope,cat1_scope,cat2_scope,product_type_scope,family_scope,context_key,match_mode,
                case_sensitive,keep_original,forbidden_target,approval_status FROM terminology_scoped_entries) terms
                WHERE approval_status='APPROVED' AND (scope=? OR scope='GLOBAL' OR scope IS NULL)
                ORDER BY CASE WHEN family_scope IS NOT NULL THEN 0 WHEN product_type_scope IS NOT NULL THEN 1 WHEN cat2_scope IS NOT NULL THEN 2 WHEN cat1_scope IS NOT NULL THEN 3 WHEN field_scope IS NOT NULL THEN 4 ELSE 5 END,
                         priority DESC, LENGTH(source_term) DESC, source_term LIMIT ?""", (scope, max(limit * 4, limit))).fetchall()
        except Exception as exc:
            if "no such table" in str(exc).lower(): return ()
            raise
        def matches(row) -> bool:
            term, mode = str(row[1] or ""), str(row[12] or "SUBSTRING").upper()
            hay = source if int(row[13] or 0) else source.casefold()
            needle = term if int(row[13] or 0) else term.casefold()
            if mode == "EXACT" and hay != needle: return False
            if mode != "EXACT" and needle not in hay: return False
            if row[6] and field_name and str(row[6]).casefold() != field_name.casefold(): return False
            if row[6] and not field_name: return False
            if row[7] and cat1 and str(row[7]).casefold() != cat1.casefold(): return False
            if row[7] and not cat1: return False
            if row[8] and cat2 and str(row[8]).casefold() != cat2.casefold(): return False
            if row[8] and not cat2: return False
            if row[9] and product_type and str(row[9]).casefold() != product_type.casefold(): return False
            if row[9] and not product_type: return False
            if row[10] and family_id and str(row[10]).casefold() != family_id.casefold(): return False
            if row[10] and not family_id: return False
            if row[11] and context_key and str(row[11]).casefold() != context_key.casefold(): return False
            if row[11] and not context_key: return False
            if row[3] not in {scope, "GLOBAL", None}: return False
            return True
        matched = [row for row in rows if matches(row)]
        self.last_conflicts = ()
        by_source: dict[str, list[Any]] = {}
        for row in matched:
            by_source.setdefault(str(row[1]).casefold(), []).append(row)
        conflicted_ids: set[str] = set()
        conflicts: list[TerminologyConflict] = []
        for source_term, candidates in by_source.items():
            def rank(row):
                specificity = sum(bool(row[index]) for index in (6, 7, 8, 9, 10, 11))
                return specificity, int(row[4] or 0)
            top_rank = max(rank(row) for row in candidates)
            top = [row for row in candidates if rank(row) == top_rank]
            targets = tuple(sorted({str(row[2]) for row in top}))
            if len(targets) > 1:
                ids = tuple(str(row[0]) for row in top)
                conflicts.append(TerminologyConflict(source_term, targets, top_rank[0], top_rank[1], ids))
                conflicted_ids.update(ids)
        self.last_conflicts = tuple(conflicts)
        hints = [TermHint(str(row[1]), str(row[2]), str(row[3] or "GLOBAL"), int(row[4] or 0), bool(row[5]), row[6], row[7], row[8], row[9], row[10], row[11], str(row[12] or "SUBSTRING"), bool(row[14]), row[15], str(row[0])) for row in matched if str(row[0]) not in conflicted_ids]
        return tuple(hints[:limit])

    def as_qwen_options(self, source_text: str, *, scope: str = "GLOBAL", field_name: str | None = None,
                        cat1: str | None = None, cat2: str | None = None, product_type: str | None = None,
                        family_id: str | None = None,
                        context_key: str | None = None, limit: int = 20) -> list[dict[str, str]]:
        # Keep the complete selected hint internally.  qwen_mt.to_qwen_term
        # deliberately projects this down to the official {source,target}
        # wire contract, so scope and approval metadata never leak to Qwen.
        return [{
            "term_id": item.term_id, "source": item.source_term,
            "target": item.target_term, "scope": item.scope,
            "priority": item.priority, "field_scope": item.field_scope,
            "cat1_scope": item.cat1_scope, "cat2_scope": item.cat2_scope,
            "product_type_scope": item.product_type_scope,
            "context_key": item.context_key, "match_mode": item.match_mode,
            "do_not_translate": item.do_not_translate,
            "keep_original": item.keep_original,
            "forbidden_target": item.forbidden_target,
            "family_scope": item.family_scope,
        } for item in self.resolve(source_text, scope=scope, field_name=field_name, cat1=cat1, cat2=cat2, product_type=product_type, family_id=family_id, context_key=context_key, limit=limit)]
