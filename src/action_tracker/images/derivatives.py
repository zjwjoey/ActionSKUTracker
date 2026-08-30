from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def _valid_excel_derivative(path: Path) -> bool:
    """Return whether a cached derivative is a complete expected PNG."""
    try:
        with Image.open(path) as image:
            image.load()
            return (
                image.format == "PNG"
                and image.size == (250, 250)
                and image.mode == "RGB"
                and path.stat().st_size > 64
            )
    except (OSError, UnidentifiedImageError):
        return False


class ImageDerivativeService:
    """Deterministically create non-authoritative export derivatives."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def excel_250(self, master_path: Path, sku: str) -> Path:
        target, _ = self.ensure_excel_250(master_path, sku)
        return target

    def ensure_excel_250(self, master_path: Path, sku: str) -> tuple[Path, str]:
        """Build or reuse a derivative and report the cache action."""
        target = self.root / "excel_250" / f"{sku}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        master_hash = hashlib.sha256(Path(master_path).read_bytes()).hexdigest()
        profile_version = "excel_250_white_v1"
        cache_key = self.cache_key(master_hash, profile_version)
        metadata = target.with_suffix(".json")
        if target.exists() and metadata.exists():
            try:
                cached = json.loads(metadata.read_text(encoding="utf-8"))
                if cached.get("cache_key") == cache_key and _valid_excel_derivative(target):
                    return target, "reused"
            except (OSError, ValueError, TypeError):
                pass
        action = "rebuilt" if target.exists() else "generated"
        image = Image.open(master_path).convert("RGBA")
        image.thumbnail((250, 250), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (250, 250), "white")
        left = (250 - image.width) // 2
        top = (250 - image.height) // 2
        canvas.paste(image, (left, top), image)
        temporary = target.with_name(f".{target.name}.tmp")
        canvas.save(temporary, format="PNG", optimize=True)
        canvas.close()
        temporary.replace(target)
        metadata_tmp = metadata.with_name(f".{metadata.name}.tmp")
        metadata_tmp.write_text(json.dumps({"cache_key": cache_key, "master_hash": master_hash, "profile_version": profile_version}, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        metadata_tmp.replace(metadata)
        return target, action

    @staticmethod
    def cache_key(master_hash: str, profile_version: str = "excel_250_v1") -> str:
        return hashlib.sha256(f"{master_hash}:{profile_version}".encode()).hexdigest()
