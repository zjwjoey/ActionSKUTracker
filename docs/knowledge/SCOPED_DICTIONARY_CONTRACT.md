# Scoped Dictionary V1 合同

术语匹配依据西语事实 `cat1_es/cat2_es`，不依赖中文类目。支持五级范围：
`GLOBAL、CAT1、CAT2、PRODUCT、FIELD`，具体范围优先于一般范围。

同等 specificity、同等 priority 却得到不同结果时必须 `REVIEW_REQUIRED`，不能随机选择。
新增或修改范围前必须生成 blast-radius 预览（影响 SKU、字段、前后样例）；当前
`scoped_dictionary.enabled=false`，只允许离线读取和预览。
