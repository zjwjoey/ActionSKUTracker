# 剩余可信来源432字段处理

用户已授权符合QA及现有自主审批条件的字段直接Apply，不逐批确认。当前范围在PRIMARY重新读取为空且仍非CURRENT：品名210、规格15、描述91、详情116。258项来自上轮已独立审查的阻断队列，本轮重验来源、空值和QA并保留原审核证据；174项另行处理，其中1项旧归档带null.前缀与PRIMARY清理版本不一致，不生成虚假精确匹配。

其余173项通过现有TranslationResolver在生产数据库的候选副本生成：145个Qwen-MT服务候选、28个确定性候选，真实请求计数145。生成结束后Codex逐项读取六字段上下文独立审查，review provider调用0；模型/确定性候选不直接批准，机械PASS不能替代语义审核。只按本字段来源翻译，其他字段仅消歧。产品身份、安全性能、数值/材质来源不明确或冲突的项保留待审核。

## 本地QA改进

面积单位m2/cm2/mm2/km2的数字指数不再当作独立数量，单位比较按同义平方单位进行；不是用忽略数字换取通过。两个逗号分隔整数区间不再误读为小数，单一小数范围保持原保护。iPhone完整同字段型号表达的pro后缀不视为普通西语，单独pro和其他残留仍拒绝。氛围照明、RGB多色灯光及闪烁LED灯光只在本字段完整限定语存在时接受；不能从另一个字段借限定。Max & More及Dr. Candy Lolli Popperz仅作为同字段完整商业名称允许，不许可普通西语句子。

真实来源fixture包含2542126、2582174、3008153、3202438、3221160、3221364的西语及文件SHA。10个CI_SAFE测试核对有效翻译、面积单位与数值异常、区间遗漏、小数保留、型号后缀及普通西语、限定语跨字段和品牌注入。完整回归1288 PASS（93.50秒）。只改本地独立分支，未发布正式词典、启用生产默认翻译开关或部署到生产daily入口。

运行证据：`F:\ActionSKUTracker\runtime\reports\historical_localization_20261009\trusted432_20261010`，包括scope、源文件SHA、原候选及服务provenance、逐字段审查、preflight和Owner队列。实际Apply、Master Sync、完整性和同批Resume结论须以正式验收记录追加，不以候选计为已入库。


## 2026-10-11 正式入库与最终验收

432项完成本轮来源及语义审查：84个缺失中文字段通过既有QA、delegated Approval、Immutable Patch、正式Apply和Master Sync补入，涉及79个SKU（品名42、规格10、描述15、详情17）。23个候选原样采用，61个候选修正后采用；其中9项从此前阻断队列恢复。随后发现3处译文限定过细，经同一正式流程去除无来源支持的“分格”和“套件”：累计87次文本补丁Apply，仍为84个不同字段，不能把重复修正算成87个补译字段。

348项未入库：最终头下仍属历史范围347项，另1项所属商品已恢复CURRENT。机械QA由307 PASS /125 FAIL变为311 PASS /121 FAIL，227个机械PASS仍因来源、主体、材质、数值或其他语义风险被阻断。只按本字段来源生成事实，不能用机械PASS替代语义批准。候选生成包含145次真实Qwen-MT请求、28个确定性结果，独立review provider调用0，服务总token记录24263；确定性候选中的商品身份误判已记录和阻断，planner缺陷尚未整体修复。

两个正式批次Master Sync均SUCCESS；84项批次实际Resume为0 Apply、84 NO_OP，3项措辞批次实际Resume为0 Apply、3 NO_OP，两次均ALREADY_SYNCED。每批数据库完整性、外键、字段差异和八项受保护数据核验通过，23份来源文件SHA-256复核未改变。最终84项当前中文与批准值一致、来源绑定APPROVED/FRESH。入库前共享锁释放后重新核对全部六字段来源上下文、空值和历史状态，84项均无变化。并发生产daily及browser恢复任务使用同一写锁，未删除其锁或停止其进程；其外部变化不能计入本批改动。

本批84项写入头为`2026-10-10_historical_trusted432_20261010_4f12a5cfd758`，3项措辞修正头为`2026-10-10_historical_trusted432_wording3_20261010_8b076b117ad0`。最终读取PRIMARY头为`2026-10-10_detail_apply_20261010T161740273910Z_4c999604_7e3d96056e6e`；历史SKU为4243，六字段空值为4623。剩余清单覆盖完整性为True；来源分类沿用已有审计，未冒称在并发任务后重新全量审计。

核心独立分支`fix/historical-localization-recovery-20261009`提交`4ce68d3`，完整回归1288 PASS。本轮修改QA及真实来源测试，未部署到生产默认daily入口，也未训练模型参数或发布正式词典。已有隔离daily兼容测试随完整回归通过；整套在线daily验收、全部既有中文独立语义审查和整个历史优化目标仍未完成。

正式证据入口：`overall_final_summary.json`、`final_acceptance.json`、两个批次的`verification.json`及Resume结果；复核清单为`manual_review_index_at_final_head.json`，剩余历史空值为`remaining_historical_inventory.jsonl`。以上均位于`F:\ActionSKUTracker\runtime\reports\historical_localization_20261009\trusted432_20261010`，候选与实际Apply严格分开计数。
