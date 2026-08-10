# AGENTS.md — Action 西班牙站商品每日监测程序

## 长期规则

1. Action 西班牙官网西语信息是事实数据。
2. 中文属于派生数据。
3. 历史文件只读。
4. 不允许为了提高匹配率猜 SKU。
5. 抓不到商品不能直接判断下架。
6. FIRST_SEEN 当天不能同时 REAPPEARED。
7. 不允许每天重新抓全部商品详情。
8. 不允许每天重新翻译全部商品。
9. 不允许每天重新下载全部图片。
10. Python 负责机械工作。
11. Agent 负责判断、异常分析和复杂匹配。
12. Excel 写入必须集中处理。
13. 多个子智能体不能同时修改 Action_Master.xlsx。
14. 所有正式更新前必须经过 QA。
15. QA 失败时保留当天数据，但不覆盖正式总表。
16. 所有日期、价格必须保持正确的数据类型。
17. 每次修改核心规则后运行测试。
18. 不绕过 CAPTCHA、Cloudflare 或网站安全机制。

## 数据边界

- `F:\按日期整理` 永远只读：禁止删除、修改、覆盖、移动、重命名其中任何文件。
- `F:\Action_Master\Action_Master.xlsx` 是历史正式总表：只允许复制，禁止修改原件。
- 正式工作总表为 `F:\ActionSKUTracker\runtime\master\Action_Master.xlsx`。

## 提交规范

- 禁止 `git reset --hard`、`git clean -fd` 等可能误删数据的 Git 操作。
- 不要 push 到远程仓库。
- 生成的数据文件（Excel/CSV/JSON）原则上不进 Git。
