from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from .assets import ImageAssetRecord, ImageManifest


class ImageSyncError(RuntimeError):
    pass


class ImageSyncService:
    """Incremental, resumable image sync isolated from product collection."""

    def __init__(self, *, asset_root: Path, manifest_path: Path, staging_root: Path, timeout_seconds: int = 20, max_retries: int = 3, download_workers: int = 1, downloader: Callable[[str, int], bytes] | None = None):
        self.asset_root = Path(asset_root)
        self.staging_root = Path(staging_root)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.download_workers = max(1, int(download_workers))
        self.downloader = downloader or self._download
        self.manifest = ImageManifest(manifest_path)

    def sync(self, rows: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat()
        counts = {key: 0 for key in ("source_sku_count", "available_count", "missing_source_url_count", "download_failed_count", "downloaded_count", "reused_count", "changed_count")}
        seen_skus: set[str] = set()
        for row in rows:
            sku = str(row.get("sku") or row.get("编号") or "").strip()
            if sku and sku in seen_skus:
                raise ImageSyncError(f"IMAGE_DUPLICATE_SKU: {sku}")
            if sku:
                seen_skus.add(sku)
        pending: list[tuple[str, str, Path, str, bool]] = []
        for row in rows:
            sku = str(row.get("sku") or row.get("编号") or "").strip()
            if not sku:
                continue
            counts["source_sku_count"] += 1
            url = str(row.get("image_url") or row.get("图片链接") or "").strip()
            previous = self.manifest.records.get(sku)
            if not url:
                self.manifest.upsert(ImageAssetRecord(sku=sku, canonical_id=str(row.get("canonical_id") or ""), download_status="NO_SOURCE_URL", normalize_status="SKIPPED", qa_status="SKIPPED", error_type="NO_SOURCE_URL"))
                counts["missing_source_url_count"] += 1
                continue
            target = self.asset_root / sku / "master.png"
            if previous and previous.source_image_url == url and previous.available and target.exists() and _sha256(target) == previous.master_hash:
                counts["reused_count"] += 1
                counts["available_count"] += 1
                continue
            if previous and previous.source_image_url and previous.source_image_url != url:
                counts["changed_count"] += 1
            pending.append((sku, url, target, str(row.get("canonical_id") or ""), bool(previous and previous.source_image_url and previous.source_image_url != url)))

        # Network work is bounded and concurrent; manifest promotion remains in
        # this caller thread so the checkpoint is deterministic and atomic.
        def fetch(item: tuple[str, str, Path, str, bool]) -> ImageAssetRecord:
            sku, url, target, canonical_id, source_changed = item
            previous = self.manifest.records.get(sku)
            try:
                record = self._sync_one(sku, url, target, canonical_id, run_id)
            except Exception as exc:  # defensive worker isolation
                record = ImageAssetRecord(
                    sku=sku, canonical_id=canonical_id, source_image_url=url,
                    master_image_path=str(target), download_status="DOWNLOAD_FAILED",
                    normalize_status="FAILED", qa_status="FAILED",
                    error_type=type(exc).__name__, error_message=str(exc),
                )
            record.source_changed = source_changed
            if record.last_downloaded_at:
                record.first_downloaded_at = (previous.first_downloaded_at if previous and previous.first_downloaded_at else record.last_downloaded_at)
            if source_changed and record.download_status == "DOWNLOAD_FAILED":
                # Preserve the failure reason while retaining the fact that the
                # source changed in the explicit boolean audit field.
                record.source_changed = True
            return record

        with ThreadPoolExecutor(max_workers=self.download_workers) as pool:
            records = list(pool.map(fetch, pending))
        for record in records:
            self.manifest.upsert(record)
            if record.available:
                counts["available_count"] += 1
                counts["downloaded_count"] += 1
            else:
                counts["download_failed_count"] += 1
        self.manifest.save()
        counts["run_id"] = run_id
        counts["started_at"] = started
        counts["finished_at"] = datetime.now(timezone.utc).isoformat()
        counts["manifest_hash"] = self.manifest.hash()
        return counts

    def _sync_one(self, sku: str, url: str, target: Path, canonical_id: str, run_id: str) -> ImageAssetRecord:
        record = ImageAssetRecord(sku=sku, canonical_id=canonical_id, source_image_url=url, master_image_path=str(target), source_changed=True)
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ImageSyncError("INVALID_SOURCE_URL")
            payload = self._with_retries(url)
            record.source_hash = hashlib.sha256(payload).hexdigest()
            record.source_filesize = len(payload)
            source = Image.open(BytesIO(payload))
            source.load()
            record.source_format = str(source.format or "").upper()
            record.source_width, record.source_height = source.size
            if record.source_width <= 0 or record.source_height <= 0:
                raise ImageSyncError("INVALID_DIMENSION")
            record.download_status = "AVAILABLE"
            record.normalize_status = "PENDING"
            stage = self.staging_root / run_id / sku / "master.png"
            stage.parent.mkdir(parents=True, exist_ok=True)
            normalized = _normalize_to_png(source, stage)
            record.master_width, record.master_height = normalized.size
            normalized.close()
            source.close()
            record.normalize_status = "PASS"
            record.master_hash = _sha256(stage)
            record.master_filesize = stage.stat().st_size
            target.parent.mkdir(parents=True, exist_ok=True)
            # Validate the staged artifact before it can replace a prior master.
            # A failed QA must never promote a corrupt/empty image into assets.
            record.qa_status = "PASS" if _validate_master(stage) else "QA_FAILED"
            if record.qa_status == "PASS":
                stage.replace(target)
            else:
                record.download_status = "QA_FAILED"
            record.last_downloaded_at = datetime.now(timezone.utc).isoformat()
            record.last_checked_at = record.last_downloaded_at
        except UnidentifiedImageError as exc:
            record.download_status = "INVALID_CONTENT"; record.normalize_status = "FAILED"; record.qa_status = "FAILED"; record.error_type = "INVALID_CONTENT"; record.error_message = str(exc)
        except (OSError, urllib.error.URLError, ImageSyncError, ValueError) as exc:
            record.download_status = "DOWNLOAD_FAILED"; record.normalize_status = "FAILED"; record.qa_status = "FAILED"; record.error_type = type(exc).__name__; record.error_message = str(exc)
        return record

    def _with_retries(self, url: str) -> bytes:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.downloader(url, self.timeout_seconds)
            except Exception as exc:
                last = exc
                if attempt < self.max_retries:
                    time.sleep(min(8, 2 ** attempt))
        raise ImageSyncError(str(last or "DOWNLOAD_FAILED"))

    @staticmethod
    def _download(url: str, timeout: int) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "ActionSKUTracker/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()


def _normalize_to_png(source: Image.Image, target: Path) -> Image.Image:
    rgba = source.convert("RGBA")
    side = max(rgba.width, rgba.height)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2), rgba)
    canvas.save(target, format="PNG", optimize=True)
    rgba.close()
    return canvas


def _validate_master(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG" or image.width <= 0 or image.height <= 0 or path.stat().st_size <= 64:
                return False
            # Reject HTML/error payloads that happen to decode and fully
            # transparent canvases that are unusable in an export.
            alpha = image.convert("RGBA").getchannel("A")
            extrema = alpha.getextrema()
            alpha.close()
            return extrema is not None and extrema[1] > 0
    except (OSError, UnidentifiedImageError):
        return False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
