# AI Translation Contract

Providers are injected through an interface and credentials are read only from runtime configuration. The offline runner validates SKU, source hash, field names, URLs, lengths and numeric preservation. It stores candidate metadata (provider/model/prompt version) and validation status; it does not apply or promote dictionary rules. AI is disabled by default.

The local Qwen3:8B integration uses the interchangeable `LocalOpenAICompatibleProvider`. Endpoint/model are configuration, JSON-only responses are required, and `localization-ai-status` / `localization-ai-check` are explicit read-only diagnostics. Human approval is required before any reusable candidate is promoted.

## Offline fine-tuning data preparation

`scripts/build_local_qwen_dataset.py` builds a Qwen-compatible `train.jsonl`, `valid.jsonl` and `manifest.json` under `runtime/local_ai/training_data/`. It reads only trusted product/term dictionary rows, applies the field-level naming/spec placement policy, preserves source numbers and technical tokens, excludes unreviewed or inconsistent examples, and performs no model/network call. The generated dataset is an offline training input; it does not modify the dictionary, Learning Pool or PRIMARY. The local QLoRA runner requires schema-v2 policy evidence and trains assistant JSON only; it remains in the separate F-drive environment and must pass shadow evaluation before any adapter is configured for inference.

Final closure (2026-09-01): the local `qwen3:8b` endpoint passed product and numeric/technical-token JSON smoke tests. The provider remains opt-in; smoke output is not written to the dictionary or PRIMARY. Source-damaged fields are never sent to the provider, requested fields are field-scoped, and promotion always rechecks every structured evidence row against current PRIMARY source hashes.

The offline Qwen3:8B adapter pilot also completed on 2026-09-01 using 364 trusted examples (333 train / 31 valid) and the full naming/spec policy projection. Its fixed-fixture evaluation passed after the deterministic `format_spec` step; the adapter is stored under `F:\LocalAI\Adapters\action-localization-qwen3-8b` and is not production-enabled.
# Localization Intelligence V1 boundary

AI 仅处理确定性知识无法覆盖的 UNKNOWN，默认 `DisabledProvider`。候选必须经过 source hash、SKU、数字、技术 Token 和普通西语残留校验；AI 候选从不直接写入 PRIMARY。
