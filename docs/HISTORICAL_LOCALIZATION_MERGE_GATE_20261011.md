# 基础代码合并 Gate 与实验策略隔离

用户目标附件 `pasted-text-1.txt`（2026-10-11）授权在完整Gate满足后创建PR并正常合并main，再从合并后main创建`experiment/localization-quality-validation-v1`执行200、500、1000 SKU实验；不授权部署、新字典发布、默认翻译策略切换或绕过PRIMARY审批。阶段间无需再次确认。

## A/B边界

A包含已验证的历史来源审计/恢复、Registry及缓存绑定、字段级QA、有限delegated Approval、不可变补丁Apply、Master投影、幂等性和真实来源回归。上述能力保留在既有Translation/QA/Approval/Apply系统中，没有新增数据库迁移。

B包含历史商品主体新词项、发圈/软糖等新候选消歧、洗涤用途和包耳特性候选、talla尺码标签生成。此前这些QA词项被planner和provider术语隐式复用；本轮将其与默认生成策略分离。增强语义事实仍用于QA及独立审核，但候选值、模型上下文、模型术语和失败重试术语默认使用原生成规则。`LocalizationEngine(experimental_historical_candidates=True)`显式启用B，仅供隔离实验。生产runtime builder没有打开此参数，正式配置没有新开关或默认值变化。formatter的`experimental_size_labels`默认false，与显式实验参数联动。

`parse_semantic_facts(historical_quality=True)`保留增强QA事实；生成路径明确使用false。新增参数均为可选keyword，既有调用兼容。已有字段来源hash合同及数据库schema未改动。实验生成不产生审批，正确已有中文仍先由锁/同源审批/可信TM复用。

## 合并验证证据

执行目录：`F:\ActionSKUTracker_history_20261009`；Windows、Python 3.12。测试进程移除`ACTION_TRACKER_PROJECT_ROOT`，设置`PYTHONPATH=src`，隔离临时文件。远端CI使用现有Ubuntu/Windows矩阵，未修改workflow、依赖或分支保护。

命令：

```powershell
git fetch --all --prune
git status --short
git rev-parse origin/main origin/fix/historical-localization-recovery-20261009 HEAD
python -m pytest -q tests/test_historical_strategy_isolation.py
python -m pytest -q
```

CI_SAFE按`.github/workflows/ci.yml`执行：设置`CI=true`，读取`tests/ci_safe_tests.txt`全部明确文件并交给`pytest.main(['-q', *files])`。新隔离测试覆盖默认/显式实验候选、默认provider上下文和术语、失败重试术语、增强QA仍阻断错误品名、严格布尔参数及数值/型号保护。

生产副本模拟：通过只读/query_only连接及SQLite Backup API取得一致副本，复制Master到独立temp，全部Apply/审批/Sync/Resume指向该副本及独立锁。用已有可信来源和批准中文的三处句末标点等价变更验证正式链路，不将模拟改动计入生产补译。比较当前和历史真实SKU的baseline/candidate默认字段值及模型上下文；发现talla生成差异后将该规则一并隔离。核验products、price_history、event_history、observations、lifecycle_state、product_fact_versions、CURRENT中西文本和全部西语来源不变。

生产目录当时有另一项`qwen-reviewed-missing-chinese-apply`运行（PID6496，已实时检查进程及共享锁），并有用户的dirty脚本/产物；本任务不切换该checkout、不编辑其配置或文件、不停止任务、不更新正在使用的代码。GitHub合并只更新远端main，不等于生产部署。

精确结果和SHA保存在`F:\ActionSKUTracker\runtime\reports\historical_localization_20261009\three_stage_validation_20261011\phase1`：生产副本Gate、最终默认对比、每批Apply/Sync/Resume及完整性报告、测试和远端CI、PR与合并证明。Gate必须以该目录最终验收记录为准；文档存在不构成Gate通过。

## 未完成部分

三轮实验、独立语义质量指标、候选词典/提示词/回滚方案及发布建议尚待进行。生产启用必须另获明确发布授权。不能将基础代码合并或机械QA通过当成新翻译策略稳定性验收。
