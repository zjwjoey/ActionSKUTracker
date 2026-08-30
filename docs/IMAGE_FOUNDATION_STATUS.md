# Image Foundation V1 状态

更新日期：2026-08-30

## 已实现

图片链路已独立于商品 Presence/Lifecycle：

```text
正式 CURRENT image_url → staging 下载 → 解码/标准化 → QA → assets/<SKU>/master.png
→ derivatives/excel_250/<SKU>.png → With-Images Export
```

- 只接受正式 Export 来源中的 CURRENT SKU；不重新访问商品列表；
- 低并发线程池、超时、指数退避和单 SKU 失败隔离；
- staging 完成验证后才原子 promotion；
- Manifest 是可恢复 checkpoint，URL/hash/QA 未变时复用；
- 透明图保留透明背景，Excel derivative 为 250×250 RGB 白底 contain；
- 无图片 URL、坏内容、下载失败、标准化失败和 QA 失败均保留 SKU；
- ES/ZH 带图 Export 使用与无图版本完全相同的字段和 SKU 集合，只读取本地衍生图；
- Template 1 带图版本已接通：仅“今日中文清单”嵌入图片，历史上下架矩阵与今日西语清单不嵌图；
- SQLite PRIMARY 模式下可把 Manifest 元数据镜像到 `image_assets`，不存图片二进制。
- Image Sync 正式闭环包含 derivative 生成/复用/重建统计；带图导出通过当前 URL、master hash 和 derivative cache key 做 eligibility 校验，不能仅因文件存在就嵌入旧图。
- ES/ZH 带图 Writer 在文本布局后保留图片行高下限 190；图片失败只增加 missing，不删除商品 SKU。

## 真实验收现状

2026-08-30 正式 SQLite CURRENT（run `2026-08-30_074743`）已完成 P2 全量验收：

- CURRENT 5,396 个 SKU；Manifest 5,396 条，全部 `AVAILABLE`；
- 全量下载 4,396、从分级阶段复用 1,000，0 缺 URL、0 失败；
- 源图实际解码均为 WEBP 1080×1080；master 为 PNG，derivative 为 250×250 RGB 白底；
- ES/ZH 无图与带图导出各 5,396 SKU，带图各嵌入 5,396 张；Template 1 今日中文清单嵌入 5,396 张；
- 带图/无图 SKU 集合与业务事实 parity 均为 0 mismatch；
- SQLite `image_assets` 已镜像 5,396 条元数据，不存图片 BLOB；
- 所有工作簿已 reopen 验证，冻结首行、筛选范围和图片对象数正常。
- 独立审计 M-01/M-02/M-03 已全部 CLOSED；真实 re-acceptance 证据位于 `runtime/temp/p2_final_reacceptance_20260830/`，eligible image 5,396/5,396，带图导出嵌入数 5,396/5,396。

## 验收结论

- 20–50、100、500、1000、FULL 切片/增量/性能基线均已完成；
- FULL 同步支持复用与中断后继续，图片失败不会删除或阻断 Product/Lifecycle；
- 通过 `docs/P2_IMAGE_FOUNDATION_FINAL_ACCEPTANCE_20260830.md` 与 `runtime/temp/p2_final_acceptance_20260830/p2_export_parity.json` 复核后，P2 Image Foundation V1 = RELEASED。

后续运行仍可使用：`python -m action_tracker image-sync --date YYYY-MM-DD`；Template 1 带图命令：`python -m action_tracker export-template1 --date YYYY-MM-DD --with-images`。

## 证据

- 实现：`src/action_tracker/images/assets.py`、`sync.py`、`derivatives.py`、`service.py`；
- 导出：`src/action_tracker/exporting/excel_writer.py`、`service.py`；
- 测试：`tests/test_image_foundation.py`、`tests/test_exporting.py`。
