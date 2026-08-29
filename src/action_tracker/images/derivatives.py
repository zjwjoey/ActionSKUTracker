from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image


class ImageDerivativeService:
    """Deterministically create non-authoritative export derivatives."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def excel_250(self, master_path: Path, sku: str) -> Path:
        target = self.root / "excel_250" / f"{sku}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
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
        return target

    @staticmethod
    def cache_key(master_hash: str, profile_version: str = "excel_250_v1") -> str:
        return hashlib.sha256(f"{master_hash}:{profile_version}".encode()).hexdigest()
