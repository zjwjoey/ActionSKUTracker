"""CI_SAFE: pure functions; no network, provider or production file access."""
import pytest

from action_tracker.exporting.repair_tokens import token_is_preserved, source_model_tokens
from action_tracker.exporting.repair_report import _token_equivalence_allows


@pytest.mark.parametrize("source,target", [
    ("AA", "使用3节AAA电池"), ("AAA", "使用3节AA电池"),
    ("A4", "型号A40"), ("IP44", "防护等级IP440"), ("PZ2", "型号PZ20"),
])
def test_changed_model_is_not_a_formatting_equivalence(source, target):
    assert not token_is_preserved(source, target)


@pytest.mark.parametrize("source,target", [
    ("AA", "AA/AAA电池"), ("AAA", "AA/AAA电池"),
    ("PZ 2", "型号PZ2"), ("PZ2", "型号PZ 2"),
    ("20.000 mAh", "容量20000mAh"), ("M-XXL", "尺码M–XXL"),
])
def test_legitimate_whole_tokens_and_formatting_remain_supported(source, target):
    assert token_is_preserved(source, target)


def test_aa_to_aaa_change_is_not_exempted_from_release_audit():
    assert not _token_equivalence_allows(
        "PROTECTED_TOKEN_CHANGED", {"value": "aa", "token_type": "TECH"},
        "Funciona con 3 pilas AA", "使用3节AAA电池",
    )


def test_model_scanner_never_joins_adjacent_lines_or_gsm_composition():
    assert source_model_tokens("500 GSM\n100 % algodón") == ()
    assert source_model_tokens("500 GSM 100 % algodón") == ()
    assert source_model_tokens("Modelo PZ 2") == ("PZ2",)
