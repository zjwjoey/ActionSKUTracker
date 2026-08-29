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
- SQLite PRIMARY 模式下可把 Manifest 元数据镜像到 `image_assets`，不存图片二进制。

## 真实验收现状

2026-08-29 正式 run `2026-08-29_184646` 已生成 ES/ZH 无图和带图四份导出：

- 每份 5,396 个 SKU；
- 表头 14 列、冻结首行 `A2`、筛选范围 `A1:N5397`；
- 当前本地图片 Manifest 为空，因此带图版本为 `embedded=0 / missing=5396`，这是缺少本地图片资产的真实结果，不是删除 SKU。

## 尚待执行

- 对正式 CURRENT 做一次 20–50 SKU 真实图片切片；
- 再执行 50、500、1000、FULL 规模性能基线；
- 确认图片源 URL 可访问后运行 `image-sync --date YYYY-MM-DD`，再生成带图导出。

## 证据

- 实现：`src/action_tracker/images/assets.py`、`sync.py`、`derivatives.py`、`service.py`；
- 导出：`src/action_tracker/exporting/excel_writer.py`、`service.py`；
- 测试：`tests/test_image_foundation.py`、`tests/test_exporting.py`。
