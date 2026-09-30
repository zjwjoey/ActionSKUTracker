"""Canonical display policy for localized product titles."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/stage5/title_display_policy.json"
_REQUIRED_PRESERVED_NAME_FACTS = frozenset({
    "model", "series_identifier_when_technical", "technical_standard", "interface", "compatible_platform",
    "size_grade", "capacity", "dose", "color", "quantity", "function",
})
_REQUIRED_PROTECTED_TOKENS = frozenset({"F48", "F45", "F40", "D/G12", "T1500", "7-in-1", "USB-C", "XL", "XXL"})
_UNRESOLVED_CJK_BRAND_MARKER = re.compile(r"[\u3400-\u9fff]{2,12}牌(?=[\u3400-\u9fff])")


class TitlePolicyError(ValueError):
    """The title display policy is missing, inconsistent, or unsupported."""


def validate_title_display_policy(policy: dict[str, Any]) -> None:
    if policy.get("policy_id") != "NO_BRAND_TITLE_V1":
        raise TitlePolicyError("TITLE_POLICY_ID_UNSUPPORTED")
    if policy.get("mode") != "REMOVE_CONFIRMED_BRAND" or policy.get("title_field") != "name":
        raise TitlePolicyError("TITLE_POLICY_MODE_UNSUPPORTED")
    preserved = set(policy.get("preserve_name_facts") or ())
    if not _REQUIRED_PRESERVED_NAME_FACTS.issubset(preserved):
        raise TitlePolicyError("TITLE_POLICY_DROPS_REQUIRED_FACT_CLASS")
    if set(policy.get("preserve_brand_fields") or ()) != {"description", "details"}:
        raise TitlePolicyError("TITLE_POLICY_NON_TITLE_BRAND_SCOPE_INVALID")
    protected = {str(item).strip() for item in policy.get("protected_name_tokens") or () if str(item).strip()}
    if not _REQUIRED_PROTECTED_TOKENS.issubset(protected):
        raise TitlePolicyError("TITLE_POLICY_PROTECTED_TOKEN_SET_INCOMPLETE")
    # Source-bound owner-approved exceptions are not implemented yet. Fail
    # closed instead of allowing a config-only brand-retention bypass.
    if policy.get("exceptions") != []:
        raise TitlePolicyError("TITLE_POLICY_EXCEPTIONS_NOT_IMPLEMENTED")


def load_title_display_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = Path(path) if path is not None else _POLICY_PATH
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TitlePolicyError(f"TITLE_POLICY_READ_FAILED: {policy_path}") from exc
    if not isinstance(policy, dict):
        raise TitlePolicyError("TITLE_POLICY_NOT_OBJECT")
    validate_title_display_policy(policy)
    return policy


def title_policy_prompt(policy: dict[str, Any]) -> str:
    """Render the canonical display rule for insertion into the model prompt."""
    validate_title_display_policy(policy)
    return (
        "标题字段 name 执行 NO_BRAND：删除已确认的商业品牌名；保留型号、具有技术/规格含义的系列代号、技术标准、接口、兼容平台、"
        "尺寸等级、容量、剂量、颜色、数量和功能等客观事实。受保护 token（" +
        "、".join(str(item) for item in policy.get("protected_name_tokens") or ()) +
        "）不得删除。description/details 中的品牌、IP、系列和认证必须保留。"
        "当前策略不接受未实现的品牌例外。"
    )


def has_unresolved_chinese_brand_marker(title: object, confirmed_source_brands: object) -> bool:
    """Flag likely translated ``品牌牌商品`` titles for human review.

    The current brand dictionary has no approved Chinese aliases, so a
    confirmed Spanish brand cannot safely identify its Chinese transliteration.
    This narrow marker is a review signal only; it never rewrites the title and
    is not a complete Chinese brand detector.
    """
    if not confirmed_source_brands:
        return False
    return bool(_UNRESOLVED_CJK_BRAND_MARKER.search(str(title or "")))


__all__ = [
    "TitlePolicyError", "has_unresolved_chinese_brand_marker", "load_title_display_policy",
    "title_policy_prompt", "validate_title_display_policy",
]
