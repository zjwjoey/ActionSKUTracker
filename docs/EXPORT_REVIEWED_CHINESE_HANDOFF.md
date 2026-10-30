# 中文表修复回归正式导出

## 复核说明清理

显式配置 `export_delivery.remove_review_notes: true`，Catalog和Template 1共用同一规则。
默认关闭以保留审阅版说明；必须使用YAML布尔值，字符串拒绝。只修改导出行，
不写Master、PRIMARY、字典或源表。中文处理换行、分号、半角／全角竖线分隔的内部
标记和完整占位值，保留正常详情、数量、单位、商品编号、业务标签和普通“复核”用语。
备注中识别“待核字段／品名待核”等完整复核块及后续引用原文，直至下一个业务标签；
正常详情列只删除有边界的说明片段，不按备注块规则吞掉后续正常属性。
西语只清理备注中的内部提示，不改官网事实字段。

清理在中文finalize及QA之前进行，事件记录完整before/after；空译文仍阻断严格发布，
审计行Hash与实际写表行Hash必须一致。Manifest记录清理策略、变化数和明细。
ANNOTATED_RELEASE要求展示来源／审批备注，不允许启用该清理选项。

## 已修中文的接入顺序

1. SQLite CURRENT继续读取PRIMARY本身的已应用中文，不重接旧产品字典。
2. MASTER_CURRENT和FORMAL_SNAPSHOT在SQLite PRIMARY模式下，按SKU、对应西语字段
   一致性及中文freshness复用PRIMARY；人工显式覆盖保持优先，已绑定PRIMARY品名／
   规格优先于旧产品字典或旧模型。缺少或变化的西语字段不得借用其他版本中文。
3. 只在表格里修过的内容不会自动进入数据库，也不能把整本表当成批准证据。
   核对字段原文、来源Hash、准确译文和审批后，复用现有
   `config/export_repair_overrides.csv`，不另建翻译／审批系统。
4. 可显式指定 `export_repair_overrides_path` 做隔离验证；默认路径不变。
   六个中文字段和单价均接入同一override引擎。审批行必须有SKU、允许字段、
   字段级Hash、replacement、reason、rule_version、approved_by、approved_at。
   `PENDING`候选不应用，旧聚合Hash需重审，同字段来源变化会阻断，其他字段变化不失效。
5. Override先应用，再进入已有QA／严格发布门禁。导出修正不是PRIMARY Apply，
   不等于完成正式入库，不提高未经批准的译文状态。修正事件记录实际审批文件路径。

`repair_overrides.prepare_reviewed_candidates`接受正式来源、当前投影、修复表和修复时
冻结的西语来源，输出同一CSV结构的PENDING候选。SKU集合不一致拒绝；缺源、来源变化、
空译文或仍含复核占位的字段单独列阻断。该接口不补审批人或时间，不生成APPROVED，
现有loader不会应用其PENDING结果。明确的字段provenance Hash不匹配时，也不得复用PRIMARY。
候选准备只比较六个中文字段，不把单价展示格式差异批量送审；单价如需修正，仍使用
同一现有审批CSV单独登记并校验对应原始单价。

不能批量给历史差异补APPROVED。真实对照中的差异仅是待确认候选，必须区分
确定修正、缺失来源、来源冲突、品牌策略和类目映射问题。来源与审批信息不足的
内容继续待核，不能为减少差异数去改源事实、价格、在售集合或现有交付表。
