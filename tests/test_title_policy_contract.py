from pathlib import Path

from action_tracker.translation.title_policy import load_title_display_policy, title_policy_prompt


def test_title_policy_requires_protected_technical_tokens():
    policy = load_title_display_policy(Path("config/stage5/title_display_policy.json"))
    assert {"F48", "T1500", "USB-C", "XL", "XXL"}.issubset(set(policy["protected_name_tokens"]))
    prompt = title_policy_prompt(policy)
    assert "不得删除" in prompt
    assert "description/details" in prompt
