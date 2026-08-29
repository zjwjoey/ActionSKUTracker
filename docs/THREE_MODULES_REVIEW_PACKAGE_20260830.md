# ActionSKUTracker 三模块审查总清单

更新日期：2026-08-30

## 1. 目标模式的来源

`EXCEL_PRIMARY`、`SQLITE_SHADOW`、`SQLITE_PRIMARY` 不是官网业务判断，也不是按 SKU 数量自动推导的状态。
它们来自 SQLite 接管方案中的生产存储角色：

- `EXCEL_PRIMARY`：当前默认值。Excel/Master 仍是正式写入主链，SQLite 不参与业务决策；
- `SQLITE_SHADOW`：Excel 先正式提交，SQLite 同步写入并做 parity，对账失败不阻断 Excel 主链；
- `SQLITE_PRIMARY`：显式切换后，SQLite 是 CURRENT/生命周期/本地化事实的主来源，Excel/CSV 仅为兼容导出视图。

模式不会自动切换；`db-promote-primary` 是单独的显式门禁。

## 2. Export Foundation V1

已实现：

- ES/ZH 单表无图导出；
- ES/ZH 单表带图导出；
- Template 1 三表无图导出；
- Template 1 带图导出（仅“今日中文清单”嵌入图片）；
- 来源只接受正式 QA/FULL_COMMIT run，不重新访问官网；
- SKU、价格、URL、日期、表头、冻结首行、筛选范围和 manifest 校验；
- ES/ZH SKU 集合必须完全一致，中文缺失只能 fallback/标记，不得删除 SKU。

主要入口：

```powershell
python -m action_tracker export --lang es --no-images --date YYYY-MM-DD
python -m action_tracker export --lang zh --no-images --date YYYY-MM-DD
python -m action_tracker export --lang es --with-images --date YYYY-MM-DD
python -m action_tracker export --lang zh --with-images --date YYYY-MM-DD
python -m action_tracker export-template1 --date YYYY-MM-DD
python -m action_tracker export-template1 --date YYYY-MM-DD --with-images
```

真实验收样本：`2026-08-29_184646`，5,396 个 CURRENT SKU；Template 1 历史 union 为 14,672 个 SKU。

## 3. SQLite Production Source of Truth

已实现：

- V2 schema、外键、完整性、事务和 rollback 保护；
- `CommitBundle`、run 幂等、`base_commit_id` 乐观门禁；
- Shadow/Primary 写入编排和兼容导出同步队列；
- SQLite PRIMARY Read Repository；
- SQLite PRIMARY 下 ES/ZH Export 与 Template 1 直接读取数据库本地化投影；
- parity 审查、reviews/source_records 等审计表。

真实运行现状：

- 生产 runtime 仍是旧 `ACTION_SQLITE_MIRROR 1.0.0` 数据库；
- 当前配置仍为 `EXCEL_PRIMARY`；
- 已用真实 Master/State 建立临时 V2 基线：6,046 products/observations，integrity PASS、FK PASS、parity 0 mismatch；
- 这份临时验收数据库没有替换生产数据库，也没有自动执行 Primary 切换。

生产切换前仍必须人为决定并留证：正式 `db-migrate-baseline` 目标文件、连续 Shadow parity（建议 3 次）、备份/恢复演练、回滚演练和 Primary 切换窗口。

## 4. Image Foundation V1

已实现：

- 正式 CURRENT 的 image_url 才能进入同步；
- staging 下载、解码、标准化、QA 后原子 promotion；
- Manifest checkpoint、复用、失败隔离、重试和低并发线程池；
- 250×250 RGB 白底 contain derivative；
- ES/ZH 带图导出缺图保留 SKU；
- Template 1 只在中文清单嵌图，历史/西语清单不嵌图；
- SQLite PRIMARY 可镜像图片元数据到 `image_assets`，不把二进制写入数据库。

真实验收现状：本地 Manifest 当前为空，因此带图导出统计为 `embedded=0 / missing=5396`；这是缺图事实，不是 SKU 删除。

正式图片任务入口：

```powershell
python -m action_tracker image-sync --date YYYY-MM-DD
python -m action_tracker image-status
```

图片下载仍是独立任务，不会由 Export 自动触发。

## 5. 回归与提交

- 全量测试：`261 passed`；
- Template 1 图片回归：验证中文表独占嵌图、ES/历史表无图、缺图计数；
- SQLite 临时基线：integrity/FK/parity 全部通过；
- 最近相关提交：
  - `62c1b23 feat: connect sqlite production modes and image exports`
  - `67f5761 feat: complete sqlite review and primary export contracts`
  - `a2b11b4 feat: add sqlite parity audit and safe boolean handling`
  - `f32f814 feat: add template1 chinese image export`
  - `5c91542 test: record template1 image profile metadata`
  - `31a2dda fix: keep template1 sqlite primary on localized source`

## 6. 当前不能误称为“已完成”的事项

以下是生产运维门禁，不是代码静默自动完成的内容：

1. 旧 runtime SQLite 数据库尚未替换为 V2 生产库；
2. 尚未完成连续多轮真实 Shadow parity；
3. 尚未执行全量图片下载和 50/500/1000/FULL 性能基线；
4. 尚未把 `storage.mode` 改为 `SQLITE_PRIMARY`；
5. 尚未把图片同步自动接入 daily 主链。

因此当前准确表述是：**三模块代码、接口、测试和旁路验收已完成；生产切换和真实图片运行仍按显式门禁执行。**
