# Action SKU Tracker QA 规则

## 1. QA 结果

| 状态 | 含义 | 是否允许正式提交 |
| --- | --- | --- |
| PASS | Presence 和必要质量检查完整 | 是，且必须非 dry-run |
| PASS_PRESENCE_ONLY | 有效 Sitemap Presence 已冻结，后续 Listing/Detail 不完整 | 只允许提交 Presence/Lifecycle；字段来源必须标记 |
| FAIL | Presence 或关键数据不可信 | 否 |

QA FAIL 和 dry-run 都可以保存 Snapshot、Staging 和报告，但不得更新 Master、known_skus 或 offline_skus。

## 2. Collection 规则

### QA-COL-001：Sitemap 有效性

Sitemap 响应、解析和数量必须健康。无效 Sitemap 不能作为 Presence 或 Listing 降级依据。

### QA-COL-002：主 Listing 覆盖

15 个主类目必须记录计划页、完成页、错误和限制状态。无有效 Sitemap fallback 时，主 Listing 不完整导致 FAIL。

### QA-COL-003：补充入口

Nuevo 和 Promoción semanal 是补充 Presence/标签证据，不替代全部主类目覆盖。

### QA-COL-004：访问限制

401、403、429、挑战页或 BLOCKED 必须显式记录。拒绝页不得当商品页解析。

## 3. Presence 规则

### QA-PRES-001：不完整观测

观测不完整时相关 SKU 为 UNKNOWN；不能推进 missing_count、OFFLINE 或批量缺失。

### QA-PRES-002：冻结顺序

Presence 必须在 Detail 之前冻结。Detail 结果不能增删已冻结的当日 CURRENT 集合。

### QA-PRES-003：Sitemap-only

Sitemap-only SKU 可以进入证据和 Review，但不能未经有效商品确认直接写入当日清单。

### QA-PRES-004：数量阈值

SKU 总量、新品、缺失和 Sitemap/Listing gap 按 `config/settings.yaml` 阈值检查。数量只用于异常检测，不是固定目标。

## 4. Lifecycle 规则

### QA-LIFE-001：FIRST_SEEN

历史从未出现且今天有效出现的 SKU 才是 NEW/FIRST_SEEN。

### QA-LIFE-002：REAPPEARED

历史出现过、上一有效状态为 MISSING/OFFLINE、今天有效重新出现，才产生 REAPPEARED。FIRST_SEEN 同日不得 REAPPEARED。

### QA-LIFE-003：缺失推进

只有有效缺失观测才能把 MISSING_FIRST 推进为 MISSING_CONTINUED，并在达到 `offline_confirmation_runs` 后转 OFFLINE。

### QA-LIFE-004：同日幂等

同一业务日期重复运行不得重复增加 missing_count、重复事件或重复首次出现。

## 5. Detail 规则

### QA-DETAIL-001：非权威性

Detail 只补字段。详情失败、空白或访问中断不表示下架。

### QA-DETAIL-002：字段来源

每个 Snapshot 行必须区分 Listing、Detail、Baseline/Pending 来源。带入字段不得伪装为当天新抓取。

### QA-DETAIL-003：访问中断

Presence 已完整冻结后，Detail 中断记为 DETAIL_ACCESS_INTERRUPTED/ACCESS_INTERRUPTED，可停止详情队列，但不否定 CURRENT。

### QA-DETAIL-004：补充应用

detail-retry 结果只有在父 observation 正式有效、SKU 一致、详情 QA 通过时才能通过 detail-apply/backfill 写回。

## 6. Master 与价格规则

### QA-MASTER-001：CURRENT 语义

CURRENT 只包含真正当前有效商品，不混入历史 OFFLINE 商品。

### QA-MASTER-002：集中写入

Master 只能通过集中 Writer 原子更新，失败时保留原文件和诊断。

### QA-PRICE-001：类型和范围

价格必须为有效数值，并通过配置的最小/最大范围检查。

### QA-PRICE-002：原价

原价只有在有效且严格大于当前售价时显示；当前售价/原价不能单独推断促销。

### QA-PRICE-003：已知无效批次

已确认的历史字段错位和无效价格批次必须由明确规则排除，不能作为模型或导出的可信价格。

## 7. Dictionary 规则

### QA-DICT-001：主键与 schema

商品 SKU、品牌关系、类目关系、术语键和人工覆盖键必须唯一；schema 不匹配直接失败。

### QA-DICT-002：字段级优先级

人工字段覆盖 > 有效商品字典 > 正式品牌/类目/术语 > source hash 有效模型结果 > 西语 fallback。

### QA-DICT-003：source hash

西语源字段变化时，旧模型结果失效并进入审核；不得静默沿用。

### QA-DICT-004：源事实损坏

SOURCE_DAMAGED/SOURCE_POLLUTED 不得通过中文回译伪造西语事实。

### QA-DICT-005：增量范围

日常只处理 NEW、source hash changed、NEEDS_REVIEW。未变化老 SKU 不产生新翻译，也不修改 updated_at。

### QA-DICT-006：术语晋升

TERM_CANDIDATE 必须经人工 APPROVED 才能进入正式术语字典。

### QA-DICT-007：Review 去重

同一稳定 review_id 不得每天重复入队；解决后转 RESOLVED，拒绝后保留 REJECTED 审计。

### QA-DICT-008：西语残留

中文品名和中文规格中的普通西语残留不得标记为 AUTO_READY；品牌、型号和技术缩写可
按品牌字典/人工确认保留原文。描述和详情当前允许西语 fallback，但必须标记字段待补，
不得伪装成中文已完成。

### QA-DICT-009：Apply Gate

Dictionary Apply 默认只允许 dry-run。预览必须逐字段记录旧值、新值、来源、Resolver 状态和原因；
`field_diff.csv` 只允许六类中文派生字段，旧值等于新值不得计为实际变化。未来 `--commit` 必须同时满足
QA/FULL_COMMIT、未过期的 Audit、Resolver 全部 AUTO_READY、CURRENT SKU 集合一致、Master hash 未并发变化，
并逐文件验证选中字典和基线 manifest 的 SHA-256 一致。`production_enabled` 和正式品牌策略只能是 YAML
布尔值；字符串 `"false"` 等配置必须拒绝。默认正式 Apply 不接受 PROVISIONAL/UNKNOWN 品牌。写入还必须
通过唯一备份、锁、暂存、不可变事实校验、原子替换及替换后回读；后续校验失败必须恢复备份并把状态写入
manifest。`dictionary_apply.production_enabled=false` 时必须拒绝。
未 AUTO_READY 的 SKU 只能进入 review_required.csv，不能部分写入 Master。

## 8. Export 规则

### QA-EXP-001：正式来源

正式 Export 只接受 QA PASS/PASS_PRESENCE_ONLY + FULL_COMMIT；拒绝 dry-run、QA FAIL 和未提交 staging。

### QA-EXP-002：当日 SKU 集合

当日清单以正式有效 Listing/CURRENT 集合为准，不以 Sitemap 数量为准。

### QA-EXP-003：三表对账

Template 1 必须满足：

```text
第一张表当日为 1 的 SKU 集合
  == 今日西班牙语清单 SKU 集合
  == 今日中文清单 SKU 集合
  == CURRENT_VALID SKU 集合
```

### QA-EXP-004：ES/ZH 事实一致

ES/ZH 的 SKU、顺序、当前售价、原价、图片链接和商品链接逐 SKU 一致。

### QA-EXP-005：中文缺失

中文缺失保留 SKU，以 fallback 和精确待审核标记处理。禁止为了得到“纯中文”而删除商品。

### QA-EXP-006：历史 Presence

每个日期的 0/1 只来自该日期源批次唯一 SKU 集合。不得由 first_seen/last_seen 推断。

### QA-EXP-007：图片

只有中文表嵌入本地 250×250 白底图片；缺图只标记，不阻断 SKU 输出。Export 不负责下载。

### QA-EXP-008：只读性

导出前后 Master、State、Dictionary 和历史源文件内容/hash 不变。

## 9. 发布门槛

正式发布前至少验证：

1. 完整回归测试通过；
2. 历史数据 dry-run 通过；
3. 同日期重复执行幂等；
4. 一次真实完整 run 达成 QA PASS；
5. export preview 三表对账通过；
6. manifest 数量、hash 和图片统计正确；
7. 没有把本机 runtime、密钥、Cookie、图片或正式 Excel 提交到 Git。

## 10. CI 门禁

### QA-CI-001：安全测试范围

CI 只运行标记为 `CI_SAFE` 的本地测试，测试数据使用临时目录或内存 fixture；真实官网采集、浏览器安装与交互、图片下载和模型网络调用不属于 CI。

### QA-CI-002：依赖可复现

CI 使用 Python 3.12 和仓库中的 `requirements-dev.txt`。新增运行时依赖时必须同步更新依赖文件、文档和测试。

### QA-CI-003：无生产副作用

CI 不得修改 Master、State、Dictionary、历史源文件或 `runtime/` 生产证据，也不得执行 baseline 发布、push 或 merge。

### QA-CI-004：门禁含义

CI 全绿只表示代码回归测试通过；正式运行仍必须经过本地 dry-run、QA、正式提交和导出预览。
