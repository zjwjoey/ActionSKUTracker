"""Conservative, review-only checks for suspiciously compressed descriptions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/stage5/description_fidelity_policy.json"
_POLICY_ID = "ACTION_DESCRIPTION_FIDELITY_V1"


class DescriptionFidelityPolicyError(ValueError):
    """The description fidelity policy is missing or malformed."""


def validate_description_fidelity_policy(policy: Mapping[str, Any]) -> None:
    if not isinstance(policy, Mapping) or policy.get("policy_id") != _POLICY_ID:
        raise DescriptionFidelityPolicyError("DESCRIPTION_FIDELITY_POLICY_ID_INVALID")
    compression = policy.get("compression_review")
    if not isinstance(compression, Mapping):
        raise DescriptionFidelityPolicyError("DESCRIPTION_COMPRESSION_POLICY_INVALID")
    minimum_source = compression.get("minimum_source_characters")
    minimum_target = compression.get("minimum_target_characters")
    minimum_ratio = compression.get("minimum_target_source_ratio")
    if (
        not isinstance(minimum_source, int) or minimum_source < 1
        or not isinstance(minimum_target, int) or minimum_target < 1
        or not isinstance(minimum_ratio, (float, int)) or not 0 < float(minimum_ratio) < 1
        or compression.get("heuristic_only") is not True
        or policy.get("automatic_rewrite") is not False
    ):
        raise DescriptionFidelityPolicyError("DESCRIPTION_COMPRESSION_POLICY_INVALID")


def load_description_fidelity_policy(path: Path | None = None) -> dict[str, Any]:
    path = path or _POLICY_PATH
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DescriptionFidelityPolicyError(f"DESCRIPTION_FIDELITY_POLICY_READ_FAILED:{path}") from exc
    if not isinstance(policy, dict):
        raise DescriptionFidelityPolicyError("DESCRIPTION_FIDELITY_POLICY_ROOT_INVALID")
    validate_description_fidelity_policy(policy)
    return policy


def description_compression_finding(
    source: object, target: object, policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a review signal only; short Chinese prose is not itself an error."""
    validate_description_fidelity_policy(policy)
    source_text, target_text = str(source or "").strip(), str(target or "").strip()
    config = policy["compression_review"]
    source_length, target_length = len(source_text), len(target_text)
    if source_length < config["minimum_source_characters"]:
        return None
    ratio = target_length / source_length if source_length else 1.0
    if (
        target_length >= config["minimum_target_characters"]
        and ratio >= config["minimum_target_source_ratio"]
    ):
        return None
    return {
        "code": "DESCRIPTION_COMPRESSION_REVIEW",
        "source_characters": source_length,
        "target_characters": target_length,
        "target_source_ratio": round(ratio, 4),
        "minimum_target_characters": config["minimum_target_characters"],
        "minimum_target_source_ratio": config["minimum_target_source_ratio"],
        "heuristic_only": True,
    }


__all__ = [
    "DescriptionFidelityPolicyError", "description_compression_finding",
    "load_description_fidelity_policy", "validate_description_fidelity_policy",
]
