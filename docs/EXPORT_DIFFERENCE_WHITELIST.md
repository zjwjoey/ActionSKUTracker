# 字段差异白名单（本地候选，未部署）

用于记录已确认的字段差异及原因，避免重复生成相同修复候选。它不提供中文值，
不创建审批或 Apply，不修改 Master/PRIMARY/字典，不按整个 SKU 免检。
默认 `export_difference_whitelist.enabled=false`，只有显式指定登记文件才加载。
本次真实登记与复核材料保存在独立 ScratchTask，不把未经最终验收的候选发布到生产。

## 只读查看

```powershell
python -m action_tracker export-difference-whitelist --registry '<已审核登记文件.json>'
python -m action_tracker export-difference-whitelist --registry '<已审核登记文件.json>' --sku 2506346
```

此入口不初始化生产路径，也不打开数据库。`tracked_skus` 包含待核记录；
登记进名单不等于已确认、已批准或可以发布。

## 两种动作

- `ACK_DIFFERENCE`：仅在两版本及西语来源 Hash 全部匹配时，将已确认的格式／
  命名政策差异标记为已知，不再重复产生同一个 CSV 修复候选。仍执行字段 QA。
- `NO_BRAND_TITLE`：仅处理经明确证据核对、当前标题已按 NO_BRAND 政策省略的
  品牌词误拦。只匹配 name_zh、精确西语品牌片段、指定 TECH token 和指定中文 Hash，
  每条例外进入 QA 审计报告。不能豁免数字、型号、认证、规格、描述、详情或来源异常。

## 登记合同

顶层 `contract=EXPORT_DIFFERENCE_WHITELIST_V1`、`entries` 数组。
每项包括 `id/sku/field/action/status/classification/source_hash/current_hash/reviewed_hash`，
以及 `reason/evidence/reviewed_by/reviewed_at/expires_at`。
标题政策例外另需 `brand_span/brand_tokens`。
`status=PENDING` 只登记待核；`CONFIRMED` 只确认差异判断，不是业务审批。
超过90天的登记期限不生效；到期、源文变化、任一中文值变化均失效。
真正错译、来源缺失、冲突及未核实项保持待核，不转为豁免。

## 本地日常来源冲突检查（本地已实现，未部署）

持续登记继续使用同一合同。只有显式带 `tracking_only=true`、`status=PENDING`、
`automatic_apply=false` 和 `does_not_waive_qa_or_approval=true` 的字段进入持续检查；
旧登记中其他 PENDING 或 CONFIRMED 条目不会因此变成来源冲突或整体 SKU 免检。

日常只核对登记字段，可在正式 daily 完成后独立运行：

```powershell
python -m action_tracker export-difference-whitelist --registry '<持续登记.json>' --check-current '<PRIMARY.sqlite3>'
```

这个入口使用 `mode=ro` 和 `query_only`，不加载生产配置、不初始化路径、不采集、
不调用模型、不写入数据库、Master 或登记文件。结果为 JSON：

- `TRACKED_PENDING`：本字段西语和当前中文 Hash 均未变，保留原冲突备注，继续待核。
- `RECHECK_REQUIRED`：本字段来源或中文变化、登记到期、元数据不完整，或 SKU 不在本次
  核对范围。列出原因和新旧 Hash，保留备注，仍不自动批准或 Apply。
- `NO_TRACKED_CONFLICTS`：没有显式的持续冲突登记。

正常完成检查返回0，不表示来源获批；缺文件、非法合同、非法跟踪开关或数据库错误返回2。
只读入口及本地审计的 JSON 使用 ASCII 转义保持内容完整，兼容 Windows 默认 GBK
输出流；解码 JSON 后中文与 Ø 等字符不变。编码错误不能冒充来源或数据库 BLOCKED。
不在核对范围不能解释为下架；缺失来源值不能解释为官网已明确为空。登记不会自动续期、
修改 Hash 或转 CONFIRMED。其他字段的变化不会误触发同 SKU 这项字段的复核。

已有完整本地审计入口可显式接入同一检查：

```powershell
python -m action_tracker localization-audit --current --conflict-registry '<持续登记.json>'
```

也可通过本地 profile 配置 `localization.source_conflict_tracking.enabled=true` 和 `path`。
默认关闭，路径不硬编码；本轮不修改生产配置、生产 daily 入口或定时任务。
完整审计的输出目录及普通字段AI设置仍取已有配置；隔离验收应把输出放入 ScratchTask，
将AI关闭，避免把只读跟踪与真实采集或补译混为一谈。

完整审计生成 `source_conflict_tracking.json`，将逐字段备注加入原有审查队列。
包含未决字段的 SKU 整体仍为 REVIEW_REQUIRED；`qa_readiness` 仅保留原机械校验结果。
登记字段不会进入AI请求或兼容入口的自动补丁准备；同 SKU 未登记的正常字段继续原条件，
人工锁定、QA、审批、Immutable Patch 和正式Apply合同不变。跟踪本身从不创建业务审批。

Python 交接复用现有 `prepare_reviewed_candidates(..., difference_whitelist=registry)`，
返回 `acknowledged_differences` 及 `does_not_waive_qa_or_approval=true`；
其余字段仍进入原 PENDING/QA/审批流程。Catalog 与 Template 1 共用同一个最终中文投影及 QA。

## 本次 QA 修复边界

支持同源 GB/SPF 的数值连写、PPP/DPI 同值表示、完整 Full HD/Epson EcoTank 源内短语、
3D 的语义对应及家具属性中的 PUFF。变值、型号后缀、其他字段或否定表达不能借用等价规则。
导出 QA 按字段自己的西语检查规格数字与颜色；旧 canonical-spec 迁移接口仍保留，
不允许它静默授权正式导出的跨字段事实添加。
新增一级类目映射对照，以及少量来源键/值绑定的明确语义错误检查，不自动改译文。

候选复核补充：完整品牌 `XXL Nutrition` 在品名中不能变成 XXL 规格；
品牌片段仅从品名的技术词扫描中分离，同一来源另有真实 XXL 时仍严格保护，
描述/详情不使用品名的省略策略。品牌身份见 [品牌官方介绍](https://xxlnutrition.com/en/about-us)。
`percal` 与府绸混淆进入 QA 阻断，织法定义依据
[CottonWorks Percale](https://cottonworks.com/encyclopedia-item/percale/)，不补纱支或克重。
历史缺冒号详情仅恢复有限的已知属性边界，保留原片段、顺序及重复项，不猜未知标签。
详情错误检查绑定属性键和值：页面横线/空白、块状物态、便池用途、防泼水，以及本字段
明确为口服营养补充剂时的软糖形态；颜色、普通橡胶和其他产品用途不套用这些规则。
同名属性数一致时按出现次序比较，避免把另一项的合法值当作错译；数量不一致时保持保守检查，
不据此生成豁免，不合并或重排重复属性。

PRIMARY 已审批的旧值即使违反新字段规则，也不由导出层静默覆盖；保留保护与 QA 阻断，
候选修正只能在独立复核材料中验证，后续业务变更仍需原审批和 Apply 流程。

## 历史路径

开发分支 `config/history_sources.yaml` 使用用户确认的10.19历史矩阵作为 seed，
其它明确日期来源保留，四月份显式排除。失效源必须阻断，不搜索文件名猜身份，
不把 UNKNOWN 改为0。真实读取与隔离导出验收结果以本次任务报告为准；生产配置未变。
