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
    field_scope: str | None = None
    cat1_scope: str | None = None
    cat2_scope: str | None = None
    product_type_scope: str | None = None
    context_key: str | None = None
    match_mode: str = "SUBSTRING"
    keep_original: bool = False
    forbidden_target: str | None = None
    term_id: str | None = None


class TerminologyRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def resolve(self, source_text: str, *, scope: str = "GLOBAL", field_name: str | None = None,
                cat1: str | None = None, cat2: str | None = None, product_type: str | None = None,
                context_key: str | None = None, limit: int = 20) -> tuple[TermHint, ...]:
        source = str(source_text or "")
        try:
            with connect(self.db_path) as db:
                rows = db.execute("""SELECT term_id,source_term,target_term,scope,priority,do_not_translate,
                field_scope,cat1_scope,cat2_scope,product_type_scope,context_key,match_mode,
                case_sensitive,keep_original,forbidden_target FROM terminology_entries
                WHERE approval_status='APPROVED' AND (scope=? OR scope='GLOBAL' OR scope IS NULL)
                ORDER BY CASE WHEN product_type_scope IS NOT NULL THEN 0 WHEN cat2_scope IS NOT NULL THEN 1 WHEN cat1_scope IS NOT NULL THEN 2 WHEN field_scope IS NOT NULL THEN 3 ELSE 4 END,
                         priority DESC, LENGTH(source_term) DESC, source_term LIMIT ?""", (scope, max(limit * 4, limit))).fetchall()
        except Exception as exc:
            if "no such table" in str(exc).lower(): return ()
            raise
        def matches(row) -> bool:
            term, mode = str(row[1] or ""), str(row[11] or "SUBSTRING").upper()
            hay = source if int(row[12] or 0) else source.casefold()
            needle = term if int(row[12] or 0) else term.casefold()
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
            if row[10] and context_key and str(row[10]).casefold() != context_key.casefold(): return False
            if row[10] and not context_key: return False
            if row[3] not in {scope, "GLOBAL", None}: return False
            return True
        hints = [TermHint(str(row[1]), str(row[2]), str(row[3] or "GLOBAL"), int(row[4] or 0), bool(row[5]), row[6], row[7], row[8], row[9], row[10], str(row[11] or "SUBSTRING"), bool(row[13]), row[14], str(row[0])) for row in rows if matches(row)]
        return tuple(hints[:limit])

    def as_qwen_options(self, source_text: str, *, scope: str = "GLOBAL", field_name: str | None = None,
                        cat1: str | None = None, cat2: str | None = None, product_type: str | None = None,
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
        } for item in self.resolve(source_text, scope=scope, field_name=field_name, cat1=cat1, cat2=cat2, product_type=product_type, context_key=context_key, limit=limit)]
