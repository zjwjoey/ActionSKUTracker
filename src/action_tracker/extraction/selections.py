from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database.connection import connect
from .contracts import ExtractionQuery
from .service import ExtractionService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SavedViewService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def create(self, name: str, query: ExtractionQuery | dict[str, Any], description: str = "", *, is_system: bool = False) -> dict[str, Any]:
        q = query if isinstance(query, ExtractionQuery) else ExtractionQuery.from_dict(query)
        now = _now(); view_id = f"view_{uuid.uuid4().hex[:12]}"
        with connect(self.db_path) as db:
            db.execute("INSERT INTO saved_views(view_id,name,description,query_json,query_hash,is_system,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (view_id,name,description,q.canonical_json(),q.query_hash(),int(is_system),now,now))
        return self.get(view_id)

    def get(self, view_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM saved_views WHERE view_id=?", (view_id,)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as db: rows = db.execute("SELECT * FROM saved_views ORDER BY name,view_id").fetchall()
        return [self._row(r) for r in rows]

    def update(self, view_id: str, *, name: str | None = None, description: str | None = None, query: ExtractionQuery | dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.get(view_id)
        if not current: raise KeyError("VIEW_NOT_FOUND")
        q = query if isinstance(query, ExtractionQuery) else (ExtractionQuery.from_dict(query) if query is not None else ExtractionQuery.from_dict(current["query"]))
        with connect(self.db_path) as db:
            db.execute("UPDATE saved_views SET name=?,description=?,query_json=?,query_hash=?,updated_at=? WHERE view_id=?", (name or current["name"],description if description is not None else current["description"],q.canonical_json(),q.query_hash(),_now(),view_id))
        return self.get(view_id)

    def delete(self, view_id: str) -> None:
        with connect(self.db_path) as db: db.execute("DELETE FROM saved_views WHERE view_id=? AND is_system=0", (view_id,))

    @staticmethod
    def _row(row) -> dict[str, Any]:
        result = dict(row); result["query"] = json.loads(result.pop("query_json")); result["is_system"] = bool(result["is_system"]); return result


class SelectionService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def create(self, name: str, query: ExtractionQuery | dict[str, Any], *, description: str = "", view_id: str | None = None) -> dict[str, Any]:
        q = query if isinstance(query, ExtractionQuery) else ExtractionQuery.from_dict(query)
        # `limit`/`offset` are presentation controls, not selection scope.
        # Always materialize the complete matched set from offset zero so a
        # paged UI cannot create a partial or duplicated membership snapshot.
        scope = q.normalized(); scope.pop("limit", None); scope.pop("offset", None)
        scope.update({"limit": 10000, "offset": 0})
        with connect(self.db_path) as db:
            # Hold a SQLite write transaction while reading the fixed
            # membership and its source commit. A concurrent production
            # commit therefore cannot make the recorded provenance disagree
            # with the rows used to build the Selection.
            db.execute("BEGIN IMMEDIATE")
            extractor = ExtractionService(self.db_path)
            result = extractor.execute(scope, connection=db)
            all_rows = list(result.items)
            while len(all_rows) < result.matched_count:
                next_page = extractor.execute(ExtractionQuery.from_dict({**scope, "offset": len(all_rows), "limit": min(10000, result.matched_count-len(all_rows))}), connection=db)
                all_rows.extend(next_page.items)
            # A selection is a fixed membership snapshot; read current facts
            # now, but store no product fields in the membership table.
            selection_id = f"sel_{uuid.uuid4().hex[:12]}"; now = _now()
            source = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
            scope_query = ExtractionQuery.from_dict(scope)
            db.execute("INSERT INTO selection_sets(selection_id,name,description,created_at,created_from_view_id,query_json,query_hash,source_commit_id,matched_count) VALUES(?,?,?,?,?,?,?,?,?)", (selection_id,name,description,now,view_id,scope_query.canonical_json(),scope_query.query_hash(),str(source[0]) if source else None,len(all_rows)))
            db.executemany("INSERT INTO selection_members(selection_id,official_sku,ordinal) VALUES(?,?,?)", [(selection_id,str(row["official_sku"]),i) for i,row in enumerate(all_rows,1)])
        return self.get(selection_id)

    def get(self, selection_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM selection_sets WHERE selection_id=?", (selection_id,)).fetchone()
            if not row: return None
            members = [str(r[0]) for r in db.execute("SELECT official_sku FROM selection_members WHERE selection_id=? ORDER BY ordinal,official_sku", (selection_id,))]
        result = dict(row); result["query"] = json.loads(result.pop("query_json")); result["members"] = members; return result

    def list(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as db: rows = db.execute("SELECT * FROM selection_sets ORDER BY created_at DESC").fetchall()
        return [{**dict(r), "query": json.loads(r["query_json"])} for r in rows]

    def members(self, selection_id: str) -> list[str]:
        with connect(self.db_path) as db: return [str(r[0]) for r in db.execute("SELECT official_sku FROM selection_members WHERE selection_id=? ORDER BY ordinal,official_sku", (selection_id,))]
