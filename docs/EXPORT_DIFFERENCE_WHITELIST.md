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

Python 交接复用现有 `prepare_reviewed_candidates(..., difference_whitelist=registry)`，
返回 `acknowledged_differences` 及 `does_not_waive_qa_or_approval=true`；
其余字段仍进入原 PENDING/QA/审批流程。Catalog 与 Template 1 共用同一个最终中文投影及 QA。

## 本次 QA 修复边界

支持同源 GB/SPF 的数值连写、PPP/DPI 同值表示、完整 Full HD/Epson EcoTank 源内短语、
3D 的语义对应及家具属性中的 PUFF。变值、型号后缀、其他字段或否定表达不能借用等价规则。
导出 QA 按字段自己的西语检查规格数字与颜色；旧 canonical-spec 迁移接口仍保留，
不允许它静默授权正式导出的跨字段事实添加。
新增一级类目映射对照，以及少量来源键/值绑定的明确语义错误检查，不自动改译文。

## 历史路径

开发分支 `config/history_sources.yaml` 使用用户确认的10.19历史矩阵作为 seed，
其它明确日期来源保留，四月份显式排除。失效源必须阻断，不搜索文件名猜身份，
不把 UNKNOWN 改为0。真实读取与隔离导出验收结果以本次任务报告为准；生产配置未变。
