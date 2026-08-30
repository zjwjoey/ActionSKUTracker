"""读取并校验正式导出 Profile；不包含任何导出执行逻辑。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ExportProfileError(ValueError):
    """导出 Profile 缺失或不符合冻结契约。"""


@dataclass(frozen=True)
class ExportProfile:
    profile_id: str
    version: int
    language: str
    filename_pattern: str
    include_embedded_images: bool
    implementation_status: str
    columns: tuple[dict[str, Any], ...]
    workbook_format: dict[str, Any]
    source_policy: dict[str, Any]
    field_sources: dict[str, str]

    def filename_for(self, date_compact: str) -> str:
        return self.filename_pattern.format(date_compact=date_compact)


def _profile_path(cfg: dict[str, Any]) -> Path:
    custom = cfg.get("export_profiles_path")
    if custom:
        return Path(custom)
    return Path(cfg["project_root"]) / "config" / "export_profiles.yaml"


def load_profile(cfg: dict[str, Any], *, language: str, no_images: bool) -> ExportProfile:
    """返回已冻结的 ES/ZH Profile；图片版只读取本地资产。"""
    profile_id = f"full_{language}_{'no_images' if no_images else 'with_images'}"
    path = _profile_path(cfg)
    if not path.exists():
        raise ExportProfileError(f"EXPORT_PROFILE_CONFIG_MISSING: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles") or {}
    profile = profiles.get(profile_id)
    if not profile:
        raise ExportProfileError(f"EXPORT_PROFILE_NOT_FOUND: {profile_id}")
    columns = tuple(raw.get("common_columns") or ())
    headers = [str(c.get("header") or "") for c in columns]
    expected = ["图片", "编号", "标题", "分类1", "分类2", "规格", "折后价", "原价", "单价",
                "描述", "产品详情", "图片链接", "商品链接", "备注"]
    if headers != expected:
        raise ExportProfileError(f"EXPORT_PROFILE_HEADERS_MISMATCH: {headers}")
    # A profile marked as planned/defined is documentation only.  It must not
    # become executable merely because its filename and columns happen to be
    # valid; only an explicit implementation marker can reach the writer.
    if profile.get("implementation_status") != "implemented":
        raise ExportProfileError(f"EXPORT_PROFILE_NOT_IMPLEMENTABLE: {profile_id}")
    return ExportProfile(
        profile_id=profile_id,
        version=int(profile.get("profile_version") or 0),
        language=str(profile.get("language") or ""),
        filename_pattern=str(profile.get("filename_pattern") or ""),
        include_embedded_images=bool(profile.get("include_embedded_images")),
        implementation_status=str(profile.get("implementation_status") or ""),
        columns=columns,
        workbook_format=dict(raw.get("workbook_format") or {}),
        source_policy=dict(raw.get("source_policy") or {}),
        field_sources=dict(profile.get("field_sources") or {}),
    )
