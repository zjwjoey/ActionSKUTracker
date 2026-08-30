# P2 Image Foundation V1 Gap Analysis

审查日期：2026-08-30  
分支：`feat/image-foundation-v1`（parent `f5e5aac`）

## 组件审查

| Component | Status | Evidence / remaining work |
|---|---|---|
| ImageAsset model | IMPLEMENTED | `images/assets.py`，SKU、URL、路径、hash、尺寸、状态、时间和错误字段齐全。 |
| Directory layout | IMPLEMENTED | `assets/<SKU>/master.png`、`staging/<run_id>/<SKU>/`、derivative 与 manifest 路径已配置。 |
| Image manifest | IMPLEMENTED | CSV 原子替换、按 SKU 唯一、可恢复 checkpoint。 |
| SQLite `image_assets` | IMPLEMENTED | 仅镜像元数据，不写图片 BLOB；Product/Lifecycle 不被图片流程修改。 |
| Downloader | IMPLEMENTED | 只访问正式 `image_url`，超时/退避/并发受配置控制。 |
| Decoder / normalizer | IMPLEMENTED | 实际解码后转 PNG；保持比例，透明图保留透明 master。 |
| Validator / QA | PARTIAL | 已验证 PNG、尺寸和文件大小；需补非空像素/非全透明等 QA 断言。 |
| Incremental planner | IMPLEMENTED | 相同 URL + hash + QA PASS 复用；URL 变化、缺失或损坏进入下载。 |
| Retry / checkpoint / resume | PARTIAL | 重试与 manifest 已有；需用回归测试证明 URL 变化、损坏重下、失败后恢复。 |
| Derivative cache | PARTIAL | profile cache key 已定义；需补 master hash/profile 变化时重建与可重建测试。 |
| ES with-images export | IMPLEMENTED | 只读取本地 derivative，缺图保留 SKU；需真实 CURRENT 验收。 |
| ZH with-images export | IMPLEMENTED | 与 ES 使用同一 CURRENT 集合；需真实 CURRENT 验收。 |
| Template1 images | IMPLEMENTED | 仅今日中文清单嵌图；需真实 CURRENT 验收。 |
| CLI | IMPLEMENTED | `image-sync`、`image-status` 和 with-images export 已接通。 |
| Performance 100/500/1000/FULL | NEEDS_REAL_VALIDATION | 代码有并发配置，尚未在当前正式 CURRENT 上形成基线。 |
| Full CURRENT sync | NEEDS_REAL_VALIDATION | 需从 SQLite PRIMARY 当前 CURRENT 执行一次真实同步；不可硬编码 SKU 数。 |

## 结论与边界

当前没有发现必须重写架构的 HIGH 问题。P2 的代码基础已存在，剩余工作集中在中等风险回归覆盖、真实切片/全量运行、带图导出重开校验与 parity 证据。图片失败仍是 enrichment 状态，不是 Product/Lifecycle 提交门禁；P3–P6 gates 保持关闭。
