# 历史中文翻译模块远端提交准备

2026-10-11（Asia/Shanghai）。本次核对覆盖独立开发分支 `fix/historical-localization-recovery-20261009` 相对重新 fetch 后 `origin/main` 的全部本地改动。远端仓库为 `https://github.com/zjwjoey/ActionSKUTracker.git`；main 基线为 `b09bcea89e35e6a69367a6542ff871bab5ba1921`。原审计头 `6d4401d` 在此基线上新增50个提交，未落后main，涉及76个文件。远端尚无同名开发分支。

## 改动核对

| 范围 | 改动及审查结论 |
| --- | --- |
| 历史来源审计与恢复 | 新增只读证据审计、冲突/语言/缺失分类、可恢复备份验证；来源恢复使用既有CommitBundle和base head，限定历史SKU及缺失字段。原始来源、文件SHA和观察时间保留，不用中文猜西语。 |
| 翻译和字段级QA | 补充来源限定的商品主体、用途、材质、布尔属性、护理指令、单位、型号、数量及西语残留保护。新增别名用于QA等价判断，不是正式字典自动发布；机械PASS仍须独立语义审核。 |
| 结构化详情及规范化 | 保护复合字段键、字段顺序和重复键；支持历史字典文本与中文分号，不将普通键前缀误拆。 |
| 缓存及Resume | 有限候选批次绑定完整请求、provider/model、adapter配置和提示词摘要；变更计划拒绝复用；已完成旧缓存明确标为配置未验证，未完成旧缓存不继续调用。候选保持PENDING，不产生隐式审批。 |
| Registry与来源缺失 | 保留None/缺少键与明确空字符串的区别，禁止业务显示品名作为西语回退。局部来源变化仅让对应字段失效，有效审批字段继续复用。 |
| delegated Approval与Apply | 有限授权绑定actor、期限、具体revision、source/target hash和审核证据SHA；Apply再次核对历史状态、来源、QA、当前revision和审批绑定。禁止委托流程带入unit prices，不绕过不可变补丁及事务。 |
| PRIMARY与Master兼容投影 | 历史恢复不创建当前类目补采任务；已审批、来源匹配的历史中文通过正式同步投影到已有长期Master列。无schema迁移或价格/Presence/生命周期改动。 |
| 配置和测试 | 历史来源配置的10个路径改为实际action表格目录，全部路径存在。新增真实来源fixture和边界负例；所有本分支修改的测试文件均列入CI白名单。默认生产安全配置、CI流程、依赖及正式字典未改变。 |

## 本次提交前修复

1. 来源审计曾将快照None转换成官方明确空值，可能遮蔽较低优先级的可信归档。现跳过None证据，保留真实空字符串，新增两项回归证明缺源、支持来源和明确空源互不混淆。
2. 数字保留检查与新增数字检查的格式解析不一致，原样保留`m2`、逗号分隔整数区间或撇号小数时误报新增数字。提取共同格式解析函数，新增四类有效格式案例及一个错误数值/型号保护负例；没有扩大跨字段事实授权。

本轮不调用翻译服务、不修改PRIMARY或Master；历史实际Apply统计保持在对应批次的不可变验收记录中。新修复改变后续来源审计行为，旧审计分类应标注为历史快照，不能冒称已由本次准备工作重新审计生产全量数据。

## 验证与提交边界

- 修改前全量测试：1288 PASS（98.16秒）。
- 两项修复后针对性测试：35 PASS（2.21秒）。
- 最终完整回归：1295 PASS（105.02秒）。
- 按`.github/workflows/ci.yml`原样读取120个白名单文件、`CI=true`的本地CI验证：1295 PASS（105.85秒）。本次为Windows/Python 3.12，远端Ubuntu和Windows矩阵待推送后验证。
- `git diff --check`通过；24个JSON fixture解析通过；历史50提交的218个不同改动blob凭据模式扫描未发现匹配，扫描不等于完整安全认证。
- 所有原50提交均无runtime、数据库、Excel、图片或其他运行二进制产物。来源fixture是有意纳入Git的回归证据；实际模型调用与入库运行日志保留本地runtime。
- 生产默认开关仍为false：knowledge/localization production_apply、localization auto_approval/ai、knowledge fallback_to_spanish、dictionary_apply production_enabled。本次没有部署。

确定性候选planner仍有已记录的商品身份误判，相关候选已被语义审核阻断；全历史中文独立语义审查、人工复核队列及完整在线daily验收尚未完成。本分支准备提交以保存已验证实现，不能将其称为整体目标完成或生产发布验收。

## 远端提交准备

保留全部可追溯提交，不改写或压缩历史。准备推送当前开发分支，未创建PR、未合并或直接更新main。推送命令：

```powershell
git push -u origin fix/historical-localization-recovery-20261009
```

仓库`AGENTS.md`第8节要求“未经用户明确授权不得 push、合并 main、创建 PR 或发布字典基线”。当前请求是核对并准备提交，因此实际push等待明确授权。

本地详细清单与扫描记录：`F:\ActionSKUTracker\runtime\reports\historical_localization_20261009\translation_remote_preparation_20261011\branch_audit.json`。拟用远端说明：同目录`proposed_pr_description.md`，仅为准备文本，不代表已创建PR。
