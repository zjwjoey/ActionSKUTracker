# 中文名称审核事实的复用

本地化QA、正式导出审计和同批次Resume复用现有Registry中的已审批名称审核证据。
这不是品牌字典发布、SKU白名单或新的审批入口；不改变生产启用配置，也不新增数据库Schema。

Repository读取当前translation unit指向的APPROVED、QA PASS revision，仅在review中确有
semantic_facts_additions时附带内部可选元数据`_approved_name_semantic_review`。旧数据库缺少
Registry表／列时不提供该元数据。多份历史来源版本均逐份验证，不以查询顺序选取可信事实。

`approved_name_review_facts`必须同时核对字段为name、SKU、当前name_es、字段source hash、
当前中文与provenance值、revision目标值、APPROVED/CURRENT状态、审批身份、CODEX审核身份、
Owner代理授权证据及原KEEP/CORRECTED决定。允许复用的额外事实仅为同一name_es中的
BRAND或原样保留的MODEL；每项须有证据及精确字段Hash。缺失、变动、STALE、未批准或畸形
证据均返回空，恢复原QA保护。其他字段不继承名称中的品牌豁免。

BRAND只覆盖审核过的完整商业标识的一次出现。标识外相同缩写、重复技术代码、型号、
接口、尺寸、电池和数值仍受保护。MODEL用于保留标识，不授权删除型号。受控的核心技术
token不能因被声明为BRAND而省略。

CURRENT审核入口发现新提交的审核事实尚未绑定时，重新执行原字段QA、审批及不可变补丁
流程；不会只因中文文本相同而跳过。来源、目标及审批仍一致且审核事实已绑定的Resume
才返回ALREADY_READY。正式Apply、备份与Master/State同步继续走原有入口。

材料别名仍以本字段原文为限：明确的calcetines de bambú及其本字段比例可表述为竹纤维，
不推广到竹制硬物、混合物体、否定和配件；masilla para madera中的木材是适用对象，
可以写作木器腻子，不据此推断腻子本身由木材制成。Sustancia中的Válido仍阻断，不猜物态。

回归测试使用真实来源冻结样本及独立负例，样本不作为人工Gold。修改必须运行完整pytest。
本文件描述本地代码契约；代码存在不代表已推送、部署或生产daily-run已使用这些规则。
