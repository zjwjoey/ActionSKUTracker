from __future__ import annotations

import hashlib
import json
import zipfile
import csv
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database.connection import connect


class ArtifactService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def record(self, *, artifact_id: str, artifact_type: str, file_path: Path, row_count: int,
               selection_id: str | None = None, profile_id: str | None = None,
               language: str | None = None, image_profile: str | None = None,
               source_commit_id: str | None = None, selection_source_commit_id: str | None = None,
               manifest_path: Path | None = None, status: str = "SUCCESS", error: str | None = None) -> dict[str, Any]:
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.exists() else None
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.db_path) as db:
            # Artifact metadata is an append-only delivery history.  Reusing
            # the same output path or caller-provided base id must never erase
            # an earlier generation.
            base_id = artifact_id
            while db.execute("SELECT 1 FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone():
                artifact_id = f"{base_id}_{uuid.uuid4().hex[:8]}"
            db.execute("""INSERT OR REPLACE INTO artifacts(artifact_id,artifact_type,selection_id,profile_id,language,image_profile,source_commit_id,selection_source_commit_id,created_at,file_path,file_hash,row_count,status,manifest_path,error)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (artifact_id,artifact_type,selection_id,profile_id,language,image_profile,source_commit_id,selection_source_commit_id,now,str(file_path),digest,row_count,status,str(manifest_path) if manifest_path else None,error))
        return {"artifact_id": artifact_id, "artifact_type": artifact_type, "selection_id": selection_id, "file_path": str(file_path), "file_hash": digest, "row_count": row_count, "status": status}

    def list(self, selection_id: str | None = None) -> list[dict[str, Any]]:
        with connect(self.db_path) as db:
            if selection_id:
                rows = db.execute("SELECT * FROM artifacts WHERE selection_id=? ORDER BY created_at DESC", (selection_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM artifacts ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def build_image_zip(self, selection_id: str, image_root: Path, output_path: Path) -> dict[str, Any]:
        """Package selected derivative images without changing product facts."""
        with connect(self.db_path) as db:
            members = [str(r[0]) for r in db.execute("SELECT official_sku FROM selection_members WHERE selection_id=? ORDER BY ordinal,official_sku", (selection_id,))]
            source = db.execute("SELECT source_commit_id FROM selection_sets WHERE selection_id=?", (selection_id,)).fetchone()
            artifact_source = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
        output_path.parent.mkdir(parents=True, exist_ok=True); missing=[]; included=[]
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for sku in members:
                path = Path(image_root) / f"{sku}.png"
                if path.exists(): archive.write(path, arcname=f"{sku}.png"); included.append(sku)
                else: missing.append(sku)
            archive.writestr("manifest.json", json.dumps({"selection_id":selection_id,"selection_source_commit_id":source[0] if source else None,"artifact_source_commit_id":artifact_source[0] if artifact_source else None,"requested_members":members,"included_members":included,"excluded_members":missing,"exclusion_reason":"IMAGE_NOT_AVAILABLE" if missing else None},ensure_ascii=False,indent=2))
        manifest_path = output_path.with_name(output_path.stem + ".manifest.json")
        manifest_path.write_text(json.dumps({"selection_id": selection_id, "selection_source_commit_id": source[0] if source else None, "artifact_source_commit_id": artifact_source[0] if artifact_source else None, "requested_members": members, "included_members": included, "excluded_members": missing, "exclusion_reason": "IMAGE_NOT_AVAILABLE" if missing else None}, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.record(artifact_id=f"artifact_{uuid.uuid4().hex}", artifact_type="IMAGE_ZIP", file_path=output_path, row_count=len(included), selection_id=selection_id, image_profile="excel_250_white_v1", source_commit_id=str(artifact_source[0]) if artifact_source else None, selection_source_commit_id=str(source[0]) if source else None, manifest_path=manifest_path, status="SUCCESS" if not missing else "DEGRADED") | {"included":len(included),"missing":len(missing),"missing_skus":missing}

    def build_csv(self, selection_id: str, output_path: Path) -> dict[str, Any]:
        from ..extraction.service import ExtractionService
        with connect(self.db_path) as db:
            # Keep membership, source commit and product facts on one
            # SQLite snapshot so a concurrent run cannot produce an artifact
            # whose provenance points at a different database head.
            db.execute("BEGIN")
            members = [str(r[0]) for r in db.execute("SELECT official_sku FROM selection_members WHERE selection_id=? ORDER BY ordinal,official_sku", (selection_id,))]
            source = db.execute("SELECT source_commit_id FROM selection_sets WHERE selection_id=?", (selection_id,)).fetchone()
            artifact_source = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
            rows = ExtractionService(self.db_path).execute({"skus": members, "statuses": ["CURRENT", "MISSING", "OFFLINE", "HISTORICAL"], "limit": 10000}, connection=db).items
        by_sku = {str(row["official_sku"]): row for row in rows}; missing = [sku for sku in members if sku not in by_sku]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["official_sku","name_es","zh_name","status","current_price","original_price","product_url","last_seen_at","image_status","localization_status"]
        included = [str(row["official_sku"]) for row in rows]
        manifest_path = output_path.with_name(output_path.stem + ".manifest.json")
        manifest_path.write_text(json.dumps({"selection_id": selection_id, "selection_source_commit_id": source[0] if source else None, "artifact_source_commit_id": artifact_source[0] if artifact_source else None, "requested_members": members, "included_members": included, "excluded_members": missing, "exclusion_reason": "SKU_NOT_FOUND" if missing else None}, ensure_ascii=False, indent=2), encoding="utf-8")
        with output_path.open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows({k: row.get(k) for k in fields} for row in rows)
        return self.record(artifact_id=f"artifact_{uuid.uuid4().hex}", artifact_type="CSV", file_path=output_path, row_count=len(rows), selection_id=selection_id, source_commit_id=str(artifact_source[0]) if artifact_source else None, selection_source_commit_id=str(source[0]) if source else None, manifest_path=manifest_path, status="SUCCESS" if not missing else "DEGRADED", error=(f"missing={','.join(missing)}" if missing else None)) | {"missing": missing}
