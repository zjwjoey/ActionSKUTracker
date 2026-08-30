from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


IMAGE_STATUSES = (
    "PENDING", "AVAILABLE", "NO_SOURCE_URL", "DOWNLOAD_FAILED", "INVALID_CONTENT",
    "INVALID_DIMENSION", "NORMALIZE_FAILED", "QA_FAILED", "SOURCE_CHANGED",
)


@dataclass
class ImageAssetRecord:
    sku: str
    canonical_id: str = ""
    source_image_url: str = ""
    master_image_path: str = ""
    master_format: str = "PNG"
    source_format: str = ""
    source_width: int = 0
    source_height: int = 0
    source_filesize: int = 0
    master_width: int = 0
    master_height: int = 0
    master_filesize: int = 0
    source_hash: str = ""
    master_hash: str = ""
    download_status: str = "PENDING"
    normalize_status: str = "PENDING"
    qa_status: str = "PENDING"
    first_downloaded_at: str = ""
    last_downloaded_at: str = ""
    last_checked_at: str = ""
    derivative_status: str = ""
    source_changed: bool = False
    error_type: str = ""
    error_message: str = ""

    @property
    def available(self) -> bool:
        return (
            self.download_status == "AVAILABLE"
            and self.normalize_status == "PASS"
            and self.qa_status == "PASS"
            and self.derivative_status not in {"FAILED", "MISSING"}
        )


class ImageManifest:
    HEADERS = tuple(asdict(ImageAssetRecord("_")))

    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: dict[str, ImageAssetRecord] = {}
        self.load()

    def load(self) -> None:
        self.records = {}
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("sku"):
                    values = {key: row.get(key, "") for key in self.HEADERS}
                    for key in ("source_width", "source_height", "source_filesize", "master_width", "master_height", "master_filesize"):
                        try:
                            values[key] = int(values[key] or 0)
                        except ValueError:
                            values[key] = 0
                    values["source_changed"] = str(values["source_changed"]).lower() == "true"
                    self.records[row["sku"]] = ImageAssetRecord(**values)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.HEADERS, extrasaction="ignore")
            writer.writeheader()
            for sku in sorted(self.records, key=_sku_key):
                writer.writerow(asdict(self.records[sku]))
        os.replace(temporary, self.path)

    def upsert(self, record: ImageAssetRecord) -> None:
        if not record.sku.strip():
            raise ValueError("IMAGE_SKU_MISSING")
        if record.download_status not in IMAGE_STATUSES:
            raise ValueError(f"IMAGE_STATUS_INVALID: {record.download_status}")
        self.records[record.sku.strip()] = record

    def hash(self) -> str:
        if not self.path.exists():
            return hashlib.sha256(b"").hexdigest()
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def _sku_key(value: str) -> tuple[int, int, str]:
    return (0, int(value), value) if value.isdigit() else (1, 0, value)
