# AGENTS.md — Action SKU Tracker 开发规则

本文件约束任何接手本仓库的 AI、脚本或开发者。变化中的数量、分支和进度放在 `docs/CURRENT_STATE.md`，不要写在这里。

## 1. 不可改变的事实边界

1. Action 西班牙官网西语信息是事实数据，中文是派生数据。
2. SKU 是商品唯一业务主键；禁止为了提高匹配率猜 SKU。
3. Sitemap、Listing、Nuevo、促销入口和 Detail 是不同证据，不得互相伪装。
4. 抓不到商品不等于商品下架；无效或不完整观测必须产生 `UNKNOWN`，不能推进缺失次数。
5. FIRST_SEEN 当天不得同时产生 REAPPEARED。
6. Detail 只补充字段，不决定 Presence、MISSING 或 OFFLINE。
7. 当日 SKU 数量是 QA 信号，不是固定业务目标。

## 2. 文件系统边界

- `F:\按日期整理` 永远只读：禁止删除、修改、覆盖、移动或重命名。
- `F:\Action_Master\Action_Master.xlsx` 是历史原件：只允许读取或复制。
- 正式工作 Master 是 `F:\ActionSKUTracker\runtime\master\Action_Master.xlsx`。
- `runtime/` 中的 Master、Snapshot、State、图片、导出和临时文件原则上不进入 Git。
- 多个执行者不得同时写 `Action_Master.xlsx`、正式字典或同一 Review Queue。
- Excel 写入必须集中、原子化并保留备份；不得由多个模块各自写同一工作簿。

## 3. 采集与访问控制

- 不绕过 CAPTCHA、Cloudflare、challenge 或网站安全机制。
- 401、403、429 和挑战页必须进入受控限制流程，不得当作正常商品页解析。
- Presence 阶段遇到访问限制，且没有满足降级条件的完整证据时，本轮失败。
- Presence 已冻结后，Detail 访问中断可以标为 `DETAIL_ACCESS_INTERRUPTED`，不得回滚有效 Presence。
- 不每天全量抓取详情；只处理 NEW、变化、待补和明确到期的 SKU。
- 不自动清 Cookie、重建 profile、换代理、伪造指纹或点击验证。

## 4. 生命周期与正式提交

- NEW：历史从未出现且今天有效出现。
- REAPPEARED：历史出现过，上一有效状态为 MISSING/OFFLINE，今天重新出现。
- MISSING 只在有效缺失观测中推进；达到配置次数后才 OFFLINE。
- 同一天重跑必须幂等，不推进多次缺失次数，不重复产生事件。
- Presence 必须在 Detail 前冻结。
- 所有正式写入必须经过 QA；QA FAIL 和 dry-run 只留证据，不覆盖 Master、known_skus 或 offline_skus。
- SQLite 代码保持冻结，不得未经单独立项改成生产主链。

## 5. 字典与中文数据

- 不每天全量翻译；增量范围仅限 NEW、官网事实哈希变化和 NEEDS_REVIEW。
- 人工覆盖按字段保护，不能因为改了中文品名就冻结同 SKU 的全部字段。
- 品牌名称可以保留原文；非品牌西语残留必须有明确 fallback/待审核标记。
- `source_hash` 变化时旧模型结果失效，不能静默复用。
- 模型或规则不得直接批量晋升术语；正式术语必须经过 Review Queue 人工确认。
- 字典不得改写 SKU、价格、商品链接、西语官网事实或在售结论。
- 正式字典基线必须先审计，再发布到 `data/dictionary/`。
- `dictionary-coverage`、`dictionary-enrich`、`review-queue` 和 `term-candidates` 必须保持离线：不得调用模型 API 或官网。
- `dictionary-apply` 默认只允许 `--dry-run`；`--commit` 已接入 QA/FULL_COMMIT/Audit Gate、字段 diff、备份、锁和原子替换路径，但 `dictionary_apply.production_enabled` 当前必须保持 false，开启需单独授权和回归审查。

## 6. Export 边界

- Export 是只读交付层，不访问官网、不翻译、不下载图片、不写回 Master/State/Dictionary。
- 正式 Export 只接受 QA PASS（含已定义的 PASS_PRESENCE_ONLY）和 FULL_COMMIT 来源。
- 当日在售数量取正式 Listing/CURRENT 有效集合，不取 Sitemap 原始数量。
- ES/ZH 当日清单必须拥有完全相同的 SKU 集合、顺序、价格和链接。
- 中文、详情或图片缺失不得删除 SKU，只能留空或标记待审核。
- Template 1 第一张表的当日 `1` 合计必须等于当日有效 SKU 数。
- 图片只从本地缓存读取；当前 Template 1 只有中文表允许嵌入 250×250 白底图片。
- 修改工作表数量、列头、列顺序、0/1 语义或图片规则必须提升 Profile 版本。

## 7. 修改与验证

- 先阅读 `docs/CURRENT_STATE.md`、`docs/ARCHITECTURE.md`、`docs/DATA_MODEL.md` 和 `docs/QA_RULES.md`。
- 保留用户已有修改；不要覆盖无关 dirty worktree。
- 修改核心规则后必须补测试，并运行 `python -m pytest -q`。
- 修改数据契约时同步更新配置、文档和测试，禁止只改其中一个。
- 文档必须区分：稳定主线、仅本地已实现、设计完成但未实现。
- 不把一次运行的 SKU 数量写成永久业务规则。

### CI 验证边界

- `.github/workflows/ci.yml` 只运行 `tests/ci_safe_tests.txt` 中明确列出的、可在临时目录完成的 `CI_SAFE` 测试。
- CI 使用 `requirements-dev.txt` 安装依赖，不安装 Playwright 浏览器，不访问 Action 官网。
- 真实采集、浏览器交互、dry-run、正式 Master/State 写入、字典基线发布和图片任务均属于 `LOCAL_ONLY`，不得被 CI 默认触发。
- 未完成安全分类的测试属于 `UNCERTAIN`，不得加入白名单，必须留在本地审查，不能为了让 CI 变绿而静默跳过。
- CI 通过只说明代码回归测试通过，不代表官网访问、QA、生命周期提交或导出正式来源已经通过。

## 8. Git 规则

- 禁止 `git reset --hard`、`git clean -fd`、未经确认的强制推送等破坏性操作。
- 不使用 `git add .`；只暂存本任务明确涉及的文件。
- 未经用户明确授权不得 push、合并 main、创建 PR 或发布字典基线。
- 用户明确要求 push 时，优先推送当前开发分支；没有明确要求不得直接改远端 main。
- 生成的 Excel、CSV、JSON、图片、报告和临时文件原则上不提交，正式字典基线除外。
