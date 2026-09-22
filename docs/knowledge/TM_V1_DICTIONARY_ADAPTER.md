# TM V1 → Dictionary 受控适配器

## 目的

Translation Memory V1 的字段级候选不能直接当作正式 Dictionary。适配器只把有
SKU provenance、Owner `APPROVE`、且当前西语 `localization_source_hash_v1` 新鲜的
品名/规格/类目字段物化为 `manual_overrides.csv` 兼容预览；描述和产品详情继续留在
Shadow Termbase。

## 字段边界

| TM 字段 | 正式产品覆盖字段 | 条件 |
|---|---|---|
| `name` | `name_zh_standard` | SKU provenance + APPROVE + source hash 一致 |
| `cat1` | `cat1_zh` | 同上 |
| `cat2` | `cat2_zh` | 同上；仍保留完整西语上下文证据 |
| `spec` | `spec_zh_standard` | 同上 |
| `description` | 不写入正式字典 | Shadow-only |
| `details` | 不写入正式字典 | Shadow-only |

## 门禁

每条候选在生成和提交前都重新校验：

1. TM source hash 与 SQLite `product_localizations(language='es')` 当前 source hash 相等。
2. `manual_overrides.csv` 当前 hash 与预览绑定值相等。
3. 同一 `(scope, key, field)` 不存在不同值冲突。
4. `production_enabled` 和显式 `--commit` 同时满足。
5. 写入前保留 before snapshot，使用临时文件 + 原子替换，并记录 after hash。

任何失败都阻断整批，不做部分写入。

## 命令

生成 SKU 覆盖预览：

```text
python scripts/build_tm_v1_product_override_preview.py ...
```

执行受控 Apply（默认只预演）：

```text
python scripts/apply_tm_v1_product_overrides.py ...
```

只有显式 `--commit` 且 `config/settings.yaml` 中
`dictionary_apply.production_enabled=true` 时才可能写入。当前默认关闭。
