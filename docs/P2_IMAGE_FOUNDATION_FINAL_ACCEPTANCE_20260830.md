# P2 Image Foundation V1 Final Acceptance

日期：2026-08-30  
分支：`feat/image-foundation-v1`  
Parent：`f5e5aac`  
代码 HEAD：`8b8aa5d`

## Full CURRENT 图片同步

| 指标 | 结果 |
|---|---:|
| CURRENT SKU | 5,396 |
| AVAILABLE | 5,396 |
| NO_SOURCE_URL | 0 |
| DOWNLOAD_FAILED | 0 |
| INVALID_CONTENT | 0 |
| INVALID_DIMENSION | 0 |
| NORMALIZE_FAILED | 0 |
| QA_FAILED | 0 |
| Image Coverage | 100% |
| 新下载 | 4,396 |
| 复用 | 1,000（分级阶段）/ 5,396（收尾复用） |
| SOURCE_CHANGED | 0 |
| FULL 耗时 | 1,814.706 秒 |

源图全部实际解码为 WEBP 1080×1080；master 全部为有效 PNG；Excel derivative 全部为 250×250 RGB 白底 contain。Manifest 与正式 `runtime/images` 资产均为 5,396 条，SQLite `image_assets` 镜像 5,396 条，仅保存元数据。

## 增量、恢复与安全

- 20–50 切片：50/50 AVAILABLE；
- 100：100/100 AVAILABLE，39.371 秒；
- 500：500/500 AVAILABLE，复用 100、下载 400，164.776 秒；
- 1000：1000/1000 AVAILABLE，复用 500、下载 500，200.523 秒；
- 相同 URL、hash 和 QA PASS 会复用；URL 变化、损坏 master 会重下；失败会留在 Manifest，不删除 SKU；
- 下载、解码、标准化和 QA 均在 staging 完成后才原子晋级；图片流程不修改 Product/Lifecycle/Presence/价格/事件。
- 审查收口：PRIMARY 本地化恢复现在强制校验 SQLite V2.0.0、正式 `FULL_COMMIT` + QA PASS 快照证据；Excel derivative 缓存命中前会重新验证 PNG 可解码、250×250、RGB 完整性，损坏缓存自动重建。

## 带图导出与 parity

- ES 无图 / 带图：各 5,396 SKU；带图嵌入 5,396；
- ZH 无图 / 带图：各 5,396 SKU；带图嵌入 5,396；
- Template 1：历史上下架 14,672 SKU；今日 ES/ZH 各 5,396；仅今日中文清单嵌入 5,396；
- ES 带图对无图：SKU mismatch 0，业务事实 mismatch 0；
- ZH 带图对无图：SKU mismatch 0，业务事实 mismatch 0；
- ES 与 ZH SKU 集合：mismatch 0；
- 所有工作簿 reopen 后，冻结首行、筛选范围、表头、行数和图片对象数均正常。

机器证据：`runtime/temp/p2_image_scale_20260830/full_result.json`、`runtime/temp/p2_image_scale_20260830/manifests/image_manifest.csv`、`runtime/temp/p2_final_acceptance_20260830/p2_export_parity.json`。

## Gate 与结论

## Final Independent Audit Closure

本轮重新审查的三个 MEDIUM 已关闭：

- **M-01 Derivative lifecycle：CLOSED** — Image Sync 在 master 成功或复用后自动生成/复用/重建 `excel_250` derivative，并记录生成统计；仅 derivative 缺失或损坏时重建，不重新下载 master。
- **M-02 Stale source export：CLOSED** — 带图导出必须同时满足 Manifest 当前 URL、AVAILABLE、master hash 和 derivative cache key 一致；URL 变更下载失败时保留 SKU 与旧 master，但不嵌入旧图。导出全程无网络。
- **M-03 Image row height：CLOSED** — ES/ZH 带图写入器在文本布局后仍保证图片行高至少 190；保存后重新打开工作簿验证图片对象和行高。

重新验收证据目录：`runtime/temp/p2_final_reacceptance_20260830/`。本次真实生产 CURRENT 为 5,396 条，eligible image 5,396 条，ES/ZH 带图各嵌入 5,396 张，Template 1 今日中文清单嵌入 5,396 张；SKU 与业务事实 parity 均为 0 mismatch。

P3–P6（Dictionary Production、Scoped Dictionary、AI Translation、Auto Approval）保持关闭；`images.enabled` 仍为 false，图片作为独立 Enrichment/Asset Layer，不成为 Product Commit 条件。

HIGH：0。MEDIUM：0（本阶段已修复 staging 先 QA、透明空图、缓存与时间元数据缺口）。LOW/SUGGESTION：仅保留历史证据与性能基线，不阻断发布。

**P2_IMAGE_FOUNDATION = RELEASED**
