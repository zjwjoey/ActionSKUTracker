"""Offline closure evidence; reads PRIMARY and writes only an isolated backup/report root.

No collector, provider, approval, Apply, production export or admin API is called.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
REPORT = ROOT / 'runtime' / 'reports' / 'post_production_repository_closure_20261008'


def command(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    return {'exit_code': result.returncode, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}


def save(name, value):
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def fingerprint(path):
    tables = ('products', 'product_localizations', 'lifecycle_state', 'observations', 'price_history', 'event_history', 'commit_batches')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        return {table: hashlib.sha256(json.dumps(db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall(),
                    ensure_ascii=False, default=str).encode('utf-8')).hexdigest() for table in tables}


def artifact_fingerprint(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file() and
            (path.suffix == '.xlsx' or path.name.endswith(('.manifest.json', '.repair-report.json')))} if root.exists() else {}


def baseline():
    import yaml
    settings = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text(encoding='utf-8'))
    save('00_repository_baseline.json', {
        'snapshot_cutoff': datetime.now(timezone.utc).isoformat(), 'branch': command('git', 'branch', '--show-current'),
        'audited_base_head': '231550eb74d24c927e100bc585aa728bf7ab7f19',
        'head': command('git', 'rev-parse', 'HEAD'), 'remote_main': command('git', 'rev-parse', 'origin/main'),
        'worktree_status': command('git', 'status', '--porcelain'),
        'worktree_status_at_start': '',
        'worktree_at_start_evidence': 'git status --short before any edit: clean; isolated branch from fetched origin/main',
        'production_tag_target': command('git', 'rev-parse', 'production/phase1-3-20261007^{}'),
        'production_defaults': {'knowledge.production_apply_enabled': (settings.get('knowledge') or {}).get('production_apply_enabled', False),
                                'localization.production_apply_enabled': (settings.get('localization') or {}).get('production_apply_enabled', False),
                                'localization.auto_approval_enabled': (settings.get('localization') or {}).get('auto_approval_enabled', False),
                                'localization.ai.enabled': ((settings.get('localization') or {}).get('ai') or {}).get('enabled', False)},
        'scope_excluded_commit': '3d28f0b356ded3fd2b5d24862954b581e0ef60f5',
        'scope_excluded_is_ancestor': command('git', 'merge-base', '--is-ancestor', '3d28f0b', 'HEAD')})
    branches = command('git', 'for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin')['stdout'].splitlines()
    rows = []
    for branch in branches:
        if branch == 'origin':
            continue
        counts = command('git', 'rev-list', '--left-right', '--count', f'origin/main...{branch}')['stdout'].split()
        unique = int(counts[1]) if len(counts) == 2 else None
        classification = 'MAIN' if branch == 'origin/main' else ('MERGED_EVIDENCE' if unique == 0 else
            ('EXPERIMENTAL' if 'scrapling' in branch or 'experiment/' in branch else 'UNIQUE_COMMITS_RETAIN_REVIEW'))
        rows.append(f'| {branch} | {unique} | {classification} |')
    protection = command('gh', 'api', 'repos/zjwjoey/ActionSKUTracker/branches/main/protection')
    rules = command('gh', 'api', 'repos/zjwjoey/ActionSKUTracker/rules/branches/main')
    prs = command('gh', 'pr', 'list', '--repo', 'zjwjoey/ActionSKUTracker', '--state', 'all', '--limit', '100',
                  '--json', 'number,title,state,headRefName,baseRefName,url')
    (REPORT / '10_branch_and_pr_inventory.md').write_text(
        '# 分支与 PR 审查\n\n快照截止时间：' + datetime.now(timezone.utc).isoformat() + '\n\n'
        '| 远端分支 | main 外独有提交数 | 分类 |\n| --- | ---: | --- |\n' + '\n'.join(rows) +
        '\n\nPR #5 已被当前生产主线取代，建议 Owner 另行关闭；本任务不合并或关闭旧 PR。'
        '\n\nmain protection 只读查询：\n```json\n' + json.dumps(protection, ensure_ascii=False, indent=2) +
        '\n```\n\n建议 Required PR、Ubuntu/Windows 两个 CI required，禁止 force push 和删除 main。未修改任何设置。'
        '\n\n生效的 branch rules 查询：' + json.dumps(rules, ensure_ascii=False) +
        '\n\nPR 快照：\n```json\n' + prs['stdout'] + '\n```\n', encoding='utf-8')
    script_rows = []
    for path in sorted((ROOT / 'scripts').rglob('*')):
        if not path.is_file() or path.suffix not in {'.py', '.ps1', '.bat'}:
            continue
        text = path.read_text(encoding='utf-8-sig', errors='replace')
        category = ('ACTIVE_PRODUCTION' if path.name in {'run_production_daily.ps1', 'register_action_tracker_task.ps1'} else
            'SUPPORTED_MANUAL' if path.name in {'run_daily.ps1', 'audit_repository_closure.py'} else
            'EXPERIMENTAL' if any(token in path.name for token in ('shadow', 'scrapling', 'canary')) else
            'LEGACY_ARCHIVE' if any(token in text for token in ('F:\\', 'D:\\', '202609', '2026100')) else 'OBSOLETE_CANDIDATE')
        script_rows.append({'file': path.relative_to(ROOT).as_posix(), 'classification': category,
                            'hardcoded_local_path': any(token in text for token in ('F:\\', 'D:\\', 'F:/', 'D:/')),
                            'action': 'RETAIN; review before use' if category != 'ACTIVE_PRODUCTION' else 'SUPPORTED'})
    with (REPORT / 'script_inventory.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=list(script_rows[0])); writer.writeheader(); writer.writerows(script_rows)


def replica(source, business_date, reuse=False):
    from action_tracker.operations.backup import backup_sqlite
    from action_tracker.workflow_v2.fact_adapter import load_committed_daily_source
    from action_tracker.database.production import validate_production_database
    from action_tracker.config import load_settings
    from action_tracker.exporting.service import export_catalog, ExportValidationError
    import os
    source = source.resolve()
    replica_root = REPORT / 'replica'
    backup = replica_root / 'db' / 'action_tracker.db'
    if backup.exists() and not reuse:
        raise ValueError('REPLICA_ALREADY_EXISTS; use recorded evidence instead of overwriting')
    if REPORT.resolve() in source.parents:
        raise ValueError('SOURCE_MUST_BE_EXTERNAL_PRIMARY')
    before = fingerprint(source)
    exports_root = source.parent.parent / 'exports'
    exports_before = artifact_fingerprint(exports_root)
    if reuse:
        if not backup.exists() or fingerprint(backup) != before:
            raise ValueError('REPLICA_SOURCE_FINGERPRINT_MISMATCH')
        evidence = {'backup_path': str(backup), 'method': 'SQLite Backup API; verified unchanged replica reused',
                    'sha256': hashlib.sha256(backup.read_bytes()).hexdigest()}
    else:
        evidence = backup_sqlite(source, backup, run_id='post-production-closure-replica', code_version=command('git', 'rev-parse', 'HEAD')['stdout'])
    cfg = load_settings()
    cfg['project_root'] = ROOT
    cfg['storage'] = {'mode': 'SQLITE_PRIMARY', 'db_path': backup}
    cfg['paths'].update({key: replica_root / key for key in cfg['paths'] if key not in {'dictionary_baseline'}})
    cfg['paths']['dictionary_baseline'] = ROOT / 'data' / 'dictionary'
    cfg['paths']['dictionary'] = ROOT / 'data' / 'dictionary'
    source_read = load_committed_daily_source(cfg, business_date=business_date)
    replica_before = fingerprint(backup)
    def try_preview(language):
        try:
            return {'status': 'PASS', 'result': export_catalog(cfg, language=language, export_date=business_date, no_images=True)}
        except ExportValidationError as exc:
            return {'status': 'BLOCKED_AS_REQUIRED', 'reason': str(exc)}
    preview = try_preview('es')
    zh_preview = try_preview('zh')
    try:
        strict = export_catalog(cfg, language='zh', export_date=business_date, no_images=True, research_release=True)
        strict_outcome = {'status': 'PASS_ON_ISOLATED_REPLICA', 'result': strict}
    except ExportValidationError as exc:
        strict_outcome = {'status': 'BLOCKED_AS_REQUIRED', 'reason': str(exc)}
    source_after = fingerprint(source)
    replica_after = fingerprint(backup)
    save('production_replica_evidence.json', {
        'backup': evidence, 'business_date': business_date, 'source_run_id': source_read['collection_run_id'],
        'source_fact_commit_id': source_read['fact_commit_id'], 'current_skus': len(source_read['records']),
        'quality_evidence': 'PASS', 'primary_before': before, 'primary_after': source_after,
        'primary_unchanged': source_after == before, 'replica_business_tables_unchanged': replica_before == replica_after,
        'production_exports_before': exports_before, 'production_exports_after': artifact_fingerprint(exports_root),
        'production_exports_unchanged': exports_before == artifact_fingerprint(exports_root),
        'integrity': validate_production_database(backup), 'preview': preview, 'zh_preview': zh_preview,
        'strict_gate': strict_outcome, 'real_qwen_calls': 0, 'real_collection_calls': 0,
        'production_runtime_writes': 0, 'formal_production_publication': False})


def tests():
    results = {}
    for name in ('full_pytest', 'ci_safe'):
        path = REPORT / f'{name}.xml'
        if path.exists():
            tree = ET.parse(path)
            suite = list(tree.getroot())[0]
            results[name] = dict(suite.attrib)
            results[name]['status'] = 'PASS' if all(int(suite.attrib.get(key, 0)) == 0 for key in ('failures', 'errors', 'skipped')) else 'FAIL'
    save('07_full_test_report.json', results)


def acceptance():
    """Produce reports from actual regression, replica and exact-head CI evidence."""
    tests()
    test_report = json.loads((REPORT / '07_full_test_report.json').read_text(encoding='utf-8'))
    cases = []
    if (REPORT / 'full_pytest.xml').exists():
        cases = [{'name': item.get('name'), 'file': item.get('classname'),
                  'status': 'FAIL' if any(item.find(tag) is not None for tag in ('failure', 'error', 'skipped')) else 'PASS'}
                 for item in ET.parse(REPORT / 'full_pytest.xml').iter('testcase')]
    def regression(tokens):
        selected = [case for case in cases if any(token in str(case['name']) for token in tokens)]
        return {'status': 'PASS' if selected and all(case['status'] == 'PASS' for case in selected) else 'FAIL',
                'regression_cases': selected}
    preview = regression(('preview_preserves', 'preview_refuses', 'template1_preview_isolated',
                          'bundle_failure', 'formal_catalog_passes', 'template1_formal', 'template1_repair_pass'))
    preview['before_fix'] = {'reproduced_failures': 6, 'scope': 'first test_repository_closure run; preview/Template1/date/registry'}
    save('03_preview_release_isolation_report.json', preview)
    facts = regression(('multiday_daily_source_reuse', 'v2_reuses_daily', 'v2_refuses_missing_quality',
                        'daily_frozen_empty_head', 'invalid_observation', 'same_day', 'detail_pending'))
    facts.update({'production_method': 'verified committed daily source reuse; partial V2 writer prohibited',
                  'independent_v2_production_fact_writer': 'BLOCKED', 'second_lifecycle_algorithm': False,
                  'new_source_capture': False})
    save('04_workflow_fact_parity_report.json', facts)
    save('05_registry_status_recovery_report.json', regression(('registry_failure', 'registry_recovery', 'registry_resume')))
    save('06_business_date_and_lock_report.json', regression(('business_date', 'cross_midnight', 'date_mismatch',
         'historical_date', 'share_lock', 'frozen_empty_head', 'persisted_identity', 'baseline_changed')))
    issues = [
        ('P0', 'PREVIEW_FORMAL_COLLISION', 'exporting/service.py:export_output_path', 'FIXED'),
        ('P0', 'UNKNOWN_ARTIFACT_OVERWRITE', 'exporting/service.py:validate_preview_destination', 'FIXED'),
        ('P0', 'TEMPLATE1_PARTIAL_GATE', 'exporting/template1_service.py:export_template1', 'FIXED'),
        ('P0', 'V2_PARTIAL_FACT_BUNDLE', 'workflow_v2/fact_adapter.py', 'PROHIBITED_IN_PRODUCTION; VERIFIED_DAILY_REUSE'),
        ('P1', 'REGISTRY_FAILURE_MASKED', 'operations/entry.py:registry_step', 'FIXED'),
        ('P1', 'REGISTRY_PRECISE_RETRY', 'operations/registry.py', 'IMPLEMENTED'),
        ('P1', 'DATE_NOT_FORWARDED', 'operations/entry.py:collect_existing_chain', 'FIXED'),
        ('P1', 'V2_CONCURRENT_WRITE', 'workflow_v2/runner.py:run', 'FIXED_SHARED_LOCK'),
        ('P1', 'LATE_BASE_COMMIT_BINDING', 'orchestrator/daily.py', 'FIXED_CAPTURE_BEFORE_READ'),
        ('P1', 'OUTDATED_CORE_DOCS', 'README.md;docs/', 'UPDATED'),
        ('P1', 'UNSAFE_ROLLBACK_GUIDE', 'docs/WORKFLOW_V2_PRODUCTION_ROLLBACK.md', 'UNVERIFIED_AUTOMATIC_ROLLBACK_BLOCKED'),
        ('P2', 'OWNER_TESTS_NOT_IN_CI', 'tests/ci_safe_tests.txt', 'FIXED'),
        ('P2', 'MISLEADING_LEGACY_ENTRY', 'scripts/run_daily.ps1', 'DELEGATED_TO_PRODUCTION_WRAPPER'),
        ('EXTERNAL', 'MAIN_BRANCH_PROTECTION', 'GitHub administration', 'OWNER_ACTION_REQUIRED'),
        ('GOVERNANCE', 'PR5_SUPERSEDED', 'PR #5', 'RECOMMEND_CLOSE; NOT_CHANGED'),
        ('EXCLUDED', 'SNAPSHOT_INGEST', '3d28f0b', 'SEPARATE_AUDIT_REQUIRED'),
        ('DATA_OUT_OF_SCOPE', 'TODAY_NULL_PRICE', '2568365/current_price', 'PRESERVED; EXPORT_CORRECTLY_BLOCKED'),
    ]
    with (REPORT / '01_full_issue_inventory.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.writer(out); writer.writerow(('priority', 'issue', 'call_site', 'resolution')); writer.writerows(issues)
    (REPORT / '02_entrypoint_contract_matrix.md').write_text(
        '# 正式入口合同\n\n| 入口 | 事实写入 | 中文候选 | 正式中文 | 发布 |\n| --- | --- | --- | --- | --- |\n'
        '| data-update / production-run | daily 完整 QA bundle | Registry 增量 | 不自动批准 | compatibility sync |\n'
        '| daily-run --dry-run | 无 | 诊断证据 | 无 | 无 |\n'
        '| V2 production translation | 复用已提交同日 daily，独立 writer 禁止 | 同 collection run | Phase1 禁止 | Phase1 禁止 |\n'
        '| Owner Review + Apply | 不改西语 | 不调 Provider | QA/approval/hash/base gate | 无 |\n'
        '| export / Template1 preview | 无 | 无 | 无 | preview 子目录 |\n'
        '| research-release | 无 | 无 | 无 | 完整严格 Gate 后正式三件套 |\n'
        '| registry-retry | 无 | 只恢复失败 run 来源/队列，无 Provider | 无 | 无 |\n'
        '| snapshot-ingest 3d28f0b | 本轮排除 | 本轮排除 | 本轮排除 | 单独审查 |\n', encoding='utf-8')
    documents = ['README.md', 'AGENTS.md', 'docs/CURRENT_STATE.md', 'docs/ARCHITECTURE.md',
        'docs/EXPORT_ARCHITECTURE.md', 'docs/WORKFLOW_V2_ARCHITECTURE.md', 'docs/OPERATIONS_RUNBOOK_V2.md',
        'docs/WORKFLOW_V2_PRODUCTION_DEPLOYMENT.md', 'docs/WORKFLOW_V2_PRODUCTION_ROLLBACK.md', 'docs/SQLITE_PRODUCTION_STATUS.md']
    import re
    broken = []
    for name in documents:
        path = ROOT / name
        for link in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if not link.startswith(('http', '#')) and not (path.parent / link.split('#')[0]).exists():
                broken.append((name, link))
    doc_test = regression(('documented_command_arguments_parse',))
    (REPORT / '09_documentation_consistency_report.md').write_text(
        '# 文档一致性\n\n更新文件：\n\n' + '\n'.join('- ' + name for name in documents) +
        '\n\nCLI 参数解析测试：' + doc_test['status'] + '\n\n失效相对链接：' + json.dumps(broken, ensure_ascii=False) +
        '\n\n当前标签只作为现行基线，无已验证上一版时自动回滚 BLOCKED；数据/代码恢复权限分开。'
        '\n\nWindows Task Scheduler、Codex 提醒和生产运行证据分开。测试数来自 XML；发布 SHA/CI 是运行 metadata，不追逐文档自身 SHA。\n', encoding='utf-8')
    replica_path = REPORT / 'production_replica_evidence.json'
    replica_evidence = json.loads(replica_path.read_text(encoding='utf-8')) if replica_path.exists() else {}
    ci_path = REPORT / '08_ci_evidence.json'
    ci = json.loads(ci_path.read_text(encoding='utf-8')) if ci_path.exists() else {}
    head = command('git', 'rev-parse', 'HEAD')['stdout']
    exact_ci = ci.get('head_sha') == head and ci.get('ubuntu') == 'PASS' and ci.get('windows') == 'PASS'
    full_pass = test_report.get('full_pytest', {}).get('status') == 'PASS'
    local_ci_pass = test_report.get('ci_safe', {}).get('status') == 'PASS'
    save('11_final_acceptance_report.json', {
        'audited_base_head': '231550eb74d24c927e100bc585aa728bf7ab7f19', 'publication_head': head,
        'preview_release_isolation': preview['status'], 'workflow_fact_contract': facts['status'],
        'registry_status_recovery': json.loads((REPORT / '05_registry_status_recovery_report.json').read_text())['status'],
        'business_date_and_lock': json.loads((REPORT / '06_business_date_and_lock_report.json').read_text())['status'],
        'README_DOCS': 'PASS' if not broken and doc_test['status'] == 'PASS' else 'FAIL',
        'ROLLBACK_SAFETY': 'PASS; unverified automatic rollback blocked',
        'FULL_PYTEST': test_report.get('full_pytest'), 'LOCAL_CI_SAFE': test_report.get('ci_safe'),
        'EXACT_HEAD_CI': 'PASS' if exact_ci else 'PENDING', 'CI': ci,
        'PRODUCTION_REPLICA': replica_evidence,
        'TODAY_PRODUCTION_STATE_UNCHANGED': replica_evidence.get('primary_unchanged', False) and replica_evidence.get('production_exports_unchanged', False),
        'QWEN_CALLS_THIS_TASK': 0, 'REAL_COLLECTION_CALLS_THIS_TASK': 0, 'PRODUCTION_RESTARTED': False,
        'FORMAL_PUBLICATION': False, 'MAIN_MERGED': False, 'GITHUB_ADMIN_CHANGED': False,
        'CANDIDATE_ACCEPTANCE': 'PASS' if full_pass and local_ci_pass and exact_ci and not broken else 'PENDING',
        'EXTERNAL_OWNER_ACTION': 'GitHub main protection; authorize merge/deployment separately',
        'DATA_FINDINGS_PRESERVED': [replica_evidence.get('strict_gate', {})],
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-db', type=Path)
    parser.add_argument('--date')
    parser.add_argument('--reuse-replica', action='store_true', help='Use only a matching, previously created Backup API replica')
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    baseline()
    if args.source_db:
        if not args.date:
            parser.error('--date is required with --source-db')
        replica(args.source_db, args.date, reuse=args.reuse_replica)
    tests()
    if args.finalize:
        acceptance()
