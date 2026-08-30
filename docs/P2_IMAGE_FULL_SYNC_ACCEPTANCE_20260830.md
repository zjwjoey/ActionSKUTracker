# P2 Full CURRENT Image Sync Acceptance

执行日期：2026-08-30  
来源：SQLite PRIMARY CURRENT，run `2026-08-30_074743`  
同步 run：`p2-full-20260830`

| 指标 | 结果 |
|---|---:|
| CURRENT | 5,396 |
| AVAILABLE | 5,396 |
| NO_SOURCE_URL | 0 |
| DOWNLOAD_FAILED | 0 |
| INVALID_CONTENT | 0 |
| INVALID_DIMENSION | 0 |
| NORMALIZE_FAILED | 0 |
| QA_FAILED | 0 |
| Coverage | 100% |
| 新下载 | 4,396 |
| 分级阶段复用 | 1,000 |
| 收尾本地复用 | 5,396 |
| SOURCE_CHANGED | 0 |
| 总耗时 | 1,814.706 秒 |

源 URL 全部为正式 Product `image_url`；源图全部解码为 WEBP 1080×1080。5,396 个 master 均为 PNG，5,396 个 Excel derivative 均为 250×250 RGB 白底 contain。下载、解码、标准化、QA 完成后才原子晋级，失败不会改变 Product/Lifecycle。

证据：`runtime/temp/p2_image_scale_20260830/full_result.json`、`runtime/images/manifests/image_manifest.csv`、`runtime/temp/p2_final_acceptance_20260830/p2_export_parity.json`。
