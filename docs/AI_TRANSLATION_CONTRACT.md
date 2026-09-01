# AI Translation Contract

Providers are injected through an interface and credentials are read only from runtime configuration. The offline runner validates SKU, source hash, field names, URLs, lengths and numeric preservation. It stores candidate metadata (provider/model/prompt version) and validation status; it does not apply or promote dictionary rules. AI is disabled by default.

The local Qwen3:8B integration uses the interchangeable `LocalOpenAICompatibleProvider`. Endpoint/model are configuration, JSON-only responses are required, and `localization-ai-status` / `localization-ai-check` are explicit read-only diagnostics. Human approval is required before any reusable candidate is promoted.
# Localization Intelligence V1 boundary

AI 仅处理确定性知识无法覆盖的 UNKNOWN，默认 `DisabledProvider`。候选必须经过 source hash、SKU、数字、技术 Token 和普通西语残留校验；AI 候选从不直接写入 PRIMARY。
