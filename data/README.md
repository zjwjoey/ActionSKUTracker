# 版本化数据

`data/dictionary/` 是经过审计、可以提交到 Git 的 Action 商品字典基线。

- `runtime/dictionary/`：本机每日运行目录，继续忽略，不提交快照、备份和临时文件。
- `data/dictionary/`：稳定字典基线，供新工作区初始化和审计复现。

发布前运行：

```powershell
$env:PYTHONPATH = "src"
python scripts/publish_dictionary_baseline.py
```

发布脚本会先重新运行字典审计；审计出现 FAIL 时不会复制任何文件。通过后才复制经过 CSV schema/主键校验的正式字典文件，并生成 `baseline_manifest.json`。

GitHub Actions 只验证字典 schema、优先级和审计相关的无副作用测试，不执行本脚本，也不会把 CI 测试结果当作正式基线发布。
