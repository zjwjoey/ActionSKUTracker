# 用户历史表中文候选 504 项复审

本轮范围是历史表精确西语配对候选中未 Apply 的 504 个字段：204 品名、136 描述、146 详情，以及上一轮留待复核的 18 个短字段。逐项独立核对本字段西语、历史中文及六字段上下文，不能把机械 QA PASS 当语义审查结果。

历史文件 `F:\按日期整理` 全程只读。候选保留历史工作簿 SHA、表/行/列、配对西语及 PRIMARY 来源证据。准确中文保留；普通措辞、来源遗漏和品牌显示按字段合同修正。型号/技术参数、主体、材料、数量或认证存在不确定性时保留 REVIEW_REQUIRED。QA FAIL 不绕过，源冲突不猜测修订官方事实。

## 本地代码改动

`localization/qa.py` 修复材料限定词判定：本字段只有 `aspecto de madera` 时接受木纹/木质外观，不接受木制/木质商品；本字段只有 `fibras de bambú` 时认可竹纤维，不把它当实体竹制品。只消费完整本字段短语，另一个字段不得提供限定，存在额外真实木材/竹材事实时继续保护。

加入 3 个带真实来源证据的历史案例（3203980、3004723、3004727），及真实材质/跨字段/限定词遗漏的负例。测试 CI_SAFE，已加入显式白名单；没有更新正式字典或默认生产开关。有效测试修复前 6 FAIL / 3 PASS，修复后全量 `python -m pytest -q`：1276 PASS，95.42 秒。

## 审查证据

报告位于 `F:\ActionSKUTracker\runtime\reports\historical_localization_20261009\user_archive_chinese_20261010`，最终审查版本 `remaining_all504_v2_20261010_*`。初次按字段分类 QA、独立语义注释与修正文案均保留，最终版本重新核对来源 SHA、字段当前为空与 PRIMARY head。

Apply 只使用现有 `run_historical_localization_pilot.py` 正式链路及用户自主审批委托。其事务之前自动备份并验证恢复，事务之后核对保护表、字段 diff、完整性和 Master Sync，再实际重复 Resume 验证幂等。当前进度与实际入库数以验收 JSON 和 CURRENT_STATE 的后续记录为准，不以候选或 preflight 数量冒充入库。

2026-10-10 22:36 的另一轮 `daily-run --dry-run`（PID 22900）持有共享生产锁，本轮第一次 Apply 被 `RUN_ALREADY_ACTIVE` 拒绝，没有写库；不删除锁、不终止另一用户任务，等待释放后再通过正式入口。
