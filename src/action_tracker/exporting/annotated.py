"""Pure, source-bound delivery annotations; never mutate facts or approval state."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, TYPE_CHECKING

from ..localization.hashes import value_hash
from ..localization.delegated_approval import CURRENT_ACTOR
from ..localization.normalization.structured_details import parse_structured_details
from ..localization.provenance import coerce_field_provenance
from ..services.hashing import localization_field_source_hash, localization_source_hash

if TYPE_CHECKING:
    from .service import ExportSource


class AnnotatedDeliveryError(ValueError):
    pass


CONTRACT = 'ANNOTATED_DELIVERY_V1'
HEADERS = ('图片', '编号', '标题', '分类1', '分类2', '规格', '折后价', '原价', '单价',
           '描述', '产品详情', '图片链接', '商品链接', '备注')
FIELDS = {
    'name': ('name_es', 'name_zh', '标题'),
    'cat1': ('cat1_es', 'cat1_zh', '分类1'),
    'cat2': ('cat2_es', 'cat2_zh', '分类2'),
    'spec': ('spec_es', 'spec_zh', '规格'),
    'description': ('desc_es', 'desc_zh', '描述'),
    'details': ('details_es', 'details_zh', '产品详情'),
}
_FIELD_NAMES = {**{k: k for k in FIELDS}, **{v[1]: k for k, v in FIELDS.items()}}
_APPROVED = {'VERIFIED', 'APPROVED', 'HUMAN_APPROVED', 'HUMAN_REVIEWED'}
ACTIONS = {'RETAIN_SOURCE_NOTE', 'QUARANTINE', 'RETAIN_WITH_CONFLICT_NOTE', 'RETAIN_PENDING_BLANK'}
QUARANTINE_TEXT = '待复核：来源字段存在异常，详见备注'
_QUARANTINE_CODES = {
    'SOURCE_DETAILS_TYPED_VALUE_INVALID', 'SPANISH_RESIDUAL',
    'NUMERIC_DROPPED', 'NUMERIC_ADDED', 'UNIT_DROPPED', 'UNIT_CHANGED',
    'PROTECTED_TOKEN_CHANGED', 'PROTECTED_TOKEN_MISSING', 'MODEL_DROPPED',
    'DETAIL_BOOLEAN_FIELD_MISSING', 'DETAIL_BOOLEAN_POLARITY_CHANGED',
    'DETAILS_COUNT_MISMATCH', 'DETAILS_STRUCTURE_INVALID',
    'STRUCTURED_DETAILS_COUNT_MISMATCH', 'STRUCTURED_DETAILS_SCHEMA_INVALID',
}


def _fail(message: str):
    raise AnnotatedDeliveryError(message)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def _cell(value: Any):
    # XLSX reads an empty string back as None. Other text is exact, including
    # whitespace and all evidence in remarks. Float formatting matches Excel.
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return ['bool', value]
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            _fail('ANNOTATED_NONFINITE_CELL')
        return ['number', format(float(value), '.15g')]
    if isinstance(value, str):
        return ['text', value]
    _fail('ANNOTATED_UNSUPPORTED_CELL_TYPE')


def full_delivery_rows_hash(rows, headers) -> str:
    """Hash every published column, including remarks, in delivery row order."""
    if tuple(headers) != HEADERS:
        _fail('ANNOTATED_HEADERS_MISMATCH')
    materialized = list(rows)
    if any(set(row) != set(HEADERS) for row in materialized):
        _fail('ANNOTATED_ROW_SCHEMA_MISMATCH')
    return _digest([[_cell(row[column]) for column in HEADERS] for row in materialized])


def _required_text(value, code):
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value


def _canonical_field(field):
    if field not in _FIELD_NAMES:
        _fail(f'ANNOTATED_UNKNOWN_FIELD:{field}')
    return _FIELD_NAMES[field]


def _validate_source(source, rows, plan, source_hash):
    from .service import canonical_source_hash, build_es_rows, validate_zh_rows_against_source
    if source.kind != 'SQLITE_CURRENT':
        _fail('ANNOTATED_REQUIRES_SQLITE_CURRENT')
    if not isinstance(plan, dict) or plan.get('contract') != CONTRACT:
        _fail('ANNOTATION_PLAN_REQUIRED')
    if not source.source_commit_id or plan.get('base_commit_id') != source.source_commit_id:
        _fail('ANNOTATED_BASE_COMMIT_MISMATCH')
    if source_hash != canonical_source_hash(source.records) or plan.get('source_hash') != source_hash:
        _fail('ANNOTATED_SOURCE_HASH_MISMATCH')
    if plan.get('export_date') != source.export_date:
        _fail('ANNOTATED_DATE_MISMATCH')
    for key in ('authorization_ref', 'authorization_text', 'approved_by'):
        _required_text(plan.get(key), 'ANNOTATED_AUTHORIZATION_REQUIRED:' + key)
    if plan.get('approval_mode') != 'OWNER_AUTHORIZED_PROXY' or plan['approved_by'] != CURRENT_ACTOR:
        _fail('ANNOTATED_OWNER_PROXY_IDENTITY_REQUIRED')
    dates = {}
    for key in ('approved_at', 'expires_at'):
        try:
            stamp = datetime.fromisoformat(_required_text(plan.get(key), 'ANNOTATED_APPROVAL_TIME_REQUIRED:' + key).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            _fail('ANNOTATED_APPROVAL_TIME_INVALID:' + key)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            _fail('ANNOTATED_APPROVAL_TIME_NAIVE:' + key)
        dates[key] = stamp.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if (dates['approved_at'] > now or dates['expires_at'] <= now
            or dates['expires_at'] <= dates['approved_at']
            or dates['expires_at'] - dates['approved_at'] > timedelta(days=7)):
        _fail('ANNOTATED_APPROVAL_EXPIRED_OR_INVALID')
    if not isinstance(plan.get('fields'), list) or not plan['fields']:
        _fail('ANNOTATED_FIELDS_REQUIRED')
    full_delivery_rows_hash(rows, HEADERS)
    keys = [str(r.get('编号') or '') for r in rows]
    records = {str(r.get('sku') or ''): r for r in source.records}
    if len(records) != len(source.records) or '' in records:
        _fail('ANNOTATED_DUPLICATE_SOURCE_SKU')
    expected_order = [str(r['编号']) for r in build_es_rows(source.records)]
    if keys != expected_order or len(keys) != len(set(keys)):
        _fail('ANNOTATED_SKU_SET_OR_ORDER_MISMATCH')
    try:
        validate_zh_rows_against_source(rows, source.records)
    except ValueError as exc:
        _fail('ANNOTATED_FACT_PARITY_MISMATCH:' + str(exc))
    return records


def _validated_note(source_es, current_zh, displayed):
    pairs = Counter((p.key, p.value) for p in parse_structured_details(source_es)
                    if p.key in {'Sustancia', 'Incluye oído'})
    eligible = [(key, value) for key, value in pairs
                if (key == 'Sustancia' and any(t.strip() in {'Válido', 'valido', 'válido'} for t in value.split(',')))
                or (key == 'Incluye oído' and value in {'Sí', 'No'})]
    if not eligible:
        _fail('ANNOTATED_VERBATIM_SOURCE_NOTE_REQUIRED')
    for key, value in eligible:
        marker = '来源异常：官网字段' + key + '=' + value
        # A full source value must survive, not a shortened prefix of one.
        pattern = re.compile(r'(?:^|[;；|｜\n])\s*' + re.escape(marker) + r'(?=$|[;；|｜\n（(])')
        for text in (current_zh, displayed):
            if len(pattern.findall(text)) != pairs[key, value]:
                _fail('ANNOTATED_VERBATIM_SOURCE_NOTE_REQUIRED')


def _repair_codes(repair_audit):
    if int(repair_audit.get('unresolved_blocking_count') or 0):
        _fail('ANNOTATED_UNMAPPED_REPAIR_FINDINGS')
    result = {}
    for item in repair_audit.get('findings') or []:
        if not item.get('blocking'):
            continue
        sku = str(item.get('sku') or '')
        field = _canonical_field(item.get('field') or item.get('field_name'))
        code = item.get('code') or item.get('rule_id')
        if not isinstance(code, str) or not code:
            _fail('ANNOTATED_BAD_REPAIR_FINDING')
        result.setdefault((sku, field), set()).add(code)
    return result


def _validate_strict(strict_audit, entries, *, disclosed_original_prices=False):
    if not isinstance(strict_audit, dict) or strict_audit.get('status') not in {'PASS', 'FAIL'}:
        _fail('ANNOTATED_STRICT_AUDIT_REQUIRED')
    counts = strict_audit.get('counts') or {}
    permitted = {'STALE_ZH', 'SPANISH_RESIDUAL', 'UNAPPROVED_ZH', 'MISSING_REQUIRED_ZH'}
    if disclosed_original_prices:
        permitted.add('MASTER_QUALITY_BLOCKED')
    for code, count in counts.items():
        if int(count or 0) and code not in permitted:
            _fail('ANNOTATED_UNHANDLED_STRICT_RULE:' + str(code))
    traced = Counter()
    missing_skus = set()
    for issue in strict_audit.get('issues') or []:
        if issue == 'MASTER_QUALITY_BLOCKED' and disclosed_original_prices:
            traced['MASTER_QUALITY_BLOCKED'] += 1
            continue
        parts = str(issue).split(':')
        if len(parts) < 3 or parts[0] not in permitted or parts[0] == 'MISSING_REQUIRED_ZH':
            _fail('ANNOTATED_UNHANDLED_STRICT_ISSUE:' + str(issue))
        pair = (parts[1], _canonical_field(parts[2]))
        action = entries.get(pair, {}).get('action')
        if not (action == 'QUARANTINE' and parts[0] in {'STALE_ZH', 'SPANISH_RESIDUAL'} or
                action == 'RETAIN_PENDING_BLANK' and parts[0] == 'UNAPPROVED_ZH'):
            _fail('ANNOTATED_STRICT_FIELD_NOT_QUARANTINED:' + str(issue))
        if parts[0] == 'UNAPPROVED_ZH' and len(parts) == 3:
            missing_skus.add(parts[1])
        else:
            traced[parts[0]] += 1
    traced['UNAPPROVED_ZH'] += len(missing_skus)
    for code in permitted - {'MISSING_REQUIRED_ZH'}:
        if int(counts.get(code) or 0) != traced[code]:
            _fail('ANNOTATED_STRICT_COUNTS_UNTRACED:' + code)
    missing_traced = sum(1 for issue in strict_audit.get('issues') or []
                         if str(issue).startswith('UNAPPROVED_ZH:') and len(str(issue).split(':')) == 3)
    if int(counts.get('MISSING_REQUIRED_ZH') or 0) != missing_traced:
        _fail('ANNOTATED_STRICT_COUNTS_UNTRACED:MISSING_REQUIRED_ZH')
    if strict_audit['status'] == 'FAIL' and not any(traced.values()):
        _fail('ANNOTATED_UNEXPLAINED_STRICT_FAILURE')


def _remarks(entry):
    label = FIELDS[entry['field']][2]
    kind = ('中文缺失，待复核' if entry['action'] == 'RETAIN_PENDING_BLANK' else
            '未发布旧中文，待复核' if entry['action'] == 'QUARANTINE' else '来源异常/冲突待核')
    important = [p.source_text for p in parse_structured_details(entry['source_es'])
                 if p.key in {'Sustancia', 'Incluye oído'}]
    excerpt = '; '.join(important) if important else entry['source_es']
    excerpt = excerpt[:240] + ('…' if len(excerpt) > 240 else '')
    reason = entry['reason'][:280] + ('…' if len(entry['reason']) > 280 else '')
    return (f'【{label}：{kind}】{reason}；'
            f'原文节选：{excerpt}；完整原文见交付manifest；'
            f'字段来源Hash：{entry["field_source_hash"][:12]}；'
            f'处理：{entry["action"]}；来源事实未确认。')


def original_price_notices(source, rows, master_quality):
    """Disclose only verified absent display of equal/lower original prices."""
    if not isinstance(master_quality, dict):
        _fail('ANNOTATED_PRICE_QUALITY_REQUIRED')
    issues = master_quality.get('issues') or []
    if (master_quality.get('status') != 'FAIL' or master_quality.get('release_ready') is not False
            or master_quality.get('commit_id') != source.source_commit_id
            or master_quality.get('records_checked') != len(source.records) or not issues):
        _fail('ANNOTATED_PRICE_QUALITY_BINDING_INVALID')
    expected = []
    for issue in issues:
        parts = str(issue).split(':')
        if len(parts) != 2 or parts[0] != 'INVALID_ORIGINAL_PRICE' or not parts[1]:
            _fail('ANNOTATED_NON_PRICE_MASTER_ISSUE')
        expected.append(parts[1])
    if len(set(expected)) != len(expected) or master_quality.get('counts', {}).get('INVALID_ORIGINAL_PRICE') != len(expected):
        _fail('ANNOTATED_PRICE_COUNTS_UNTRACED')
    records = {str(r.get('sku') or ''): r for r in source.records}
    displayed = {str(r.get('编号') or ''): r for r in rows}
    notices = []
    for sku in sorted(expected):
        rec, row = records.get(sku), displayed.get(sku)
        if rec is None or row is None:
            _fail('ANNOTATED_PRICE_SKU_MISSING')
        # Repository CURRENT intentionally masks invalid observation fields.
        # Its retained PRIMARY value is evidence only, never a current price fallback.
        retained_original = rec.get('last_valid_original_price', rec.get('original_price'))
        if any(isinstance(x, bool) for x in (rec.get('current_price'), retained_original, row.get('折后价'))):
            _fail('ANNOTATED_PRICE_VALUE_INVALID:' + sku)
        try:
            current = float(str(rec.get('current_price')).strip().replace(',', '.'))
            original = float(str(retained_original).strip().replace(',', '.'))
            shown = float(row.get('折后价'))
        except (ValueError, TypeError):
            _fail('ANNOTATED_PRICE_VALUE_INVALID:' + sku)
        if (not all(math.isfinite(x) for x in (current, original, shown)) or
                current <= 0 or original <= 0 or original > current or shown != current or
                row.get('原价') not in (None, '')):
            _fail('ANNOTATED_PRICE_DISPLAY_NOT_SAFE:' + sku)
        note = (f'【原价待核】PRIMARY保留原价：{original:g}欧元；当前售价：{current:g}欧元；'
                '保留原价不高于当前售价，导出原价留空；保留值不代表当前有效原价，未修改来源价格，不能据此计算折扣。')
        notices.append({'sku': sku, 'source_current_price': rec.get('current_price'),
            'source_original_price': retained_original, 'effective_original_price': rec.get('original_price'), 'note': note,
            'price_source_hash': _digest([sku, rec.get('current_price'), rec.get('original_price'), retained_original])})
    return notices


def build_tracking_annotation_plan(source, registry, repair_audit, *, authorization_ref, authorization_text, now=None):
    """Build explicit delivery evidence from unchanged, source-bound tracking entries."""
    from .difference_whitelist import DifferenceWhitelist, pending_conflict_report
    from .service import canonical_source_hash
    _required_text(authorization_ref, 'ANNOTATED_AUTHORIZATION_REQUIRED:authorization_ref')
    _required_text(authorization_text, 'ANNOTATED_AUTHORIZATION_REQUIRED:authorization_text')
    if registry is None:
        _fail('ANNOTATED_TRACKING_NOT_ENABLED')
    by_sku = {str(r.get('sku') or ''): r for r in source.records}
    selected = [x for x in registry.entries.values() if x.get('tracking_only') is True and x['sku'] in by_sku]
    scoped = DifferenceWhitelist(selected)
    state = pending_conflict_report(scoped, source.records, now=now)
    if state['recheck_required_count']:
        changed = next(x for x in state['entries'] if x['requires_recheck'])
        _fail('ANNOTATED_TRACKING_RECHECK_REQUIRED:' + changed['sku'] + ':' + changed['field'] + ':' + ','.join(changed['changes']))
    actual_codes = _repair_codes(repair_audit)
    fields = []
    for item in selected:
        sku = item['sku']; field = _canonical_field(item['field']); rec = by_sku[sku]
        source_key, target_key, _ = FIELDS[field]
        target = rec.get(target_key); codes = actual_codes.get((sku, field), set())
        if not str(target or '').strip():
            action = 'RETAIN_PENDING_BLANK'
        elif codes == {'SOURCE_DETAILS_TYPED_VALUE_INVALID'} and field == 'details':
            action = 'RETAIN_SOURCE_NOTE'
        elif not codes:
            action = 'RETAIN_WITH_CONFLICT_NOTE'
        else:
            _fail('ANNOTATED_TRACKING_UNSUPPORTED_FINDING:' + sku + ':' + field)
        fields.append({'sku': sku, 'field': field, 'source_es': rec.get(source_key), 'current_zh': target,
            'source_hash': localization_source_hash(rec), 'field_source_hash': localization_field_source_hash(rec, field),
            'current_target_hash': value_hash(target), 'action': action, 'reason': item['reason'],
            'allowed_repair_codes': sorted(codes), 'tracking_entry_id': item['id']})
    if not fields:
        _fail('ANNOTATED_FIELDS_REQUIRED')
    now = now or datetime.now(timezone.utc)
    return {'contract': CONTRACT, 'base_commit_id': source.source_commit_id,
        'source_hash': canonical_source_hash(source.records), 'export_date': source.export_date,
        'authorization_ref': authorization_ref, 'authorization_text': authorization_text,
        'approved_by': CURRENT_ACTOR, 'approval_mode': 'OWNER_AUTHORIZED_PROXY',
        'approved_at': now.isoformat(), 'expires_at': (now + timedelta(hours=1)).isoformat(), 'fields': fields}


def apply_annotation_plan(source: 'ExportSource', rows: list[dict], plan: dict, *,
                          source_hash: str, repair_audit: dict, strict_audit: dict,
                          conflict_registry=None, master_quality=None) -> tuple[list[dict], dict]:
    """Create an audited delivery copy while retaining the original failed gate."""
    records = _validate_source(source, rows, plan, source_hash)
    baseline = deepcopy(rows)
    final = deepcopy(rows)
    by_sku = {str(row['编号']): row for row in final}
    actual_codes = _repair_codes(repair_audit)
    entries = {}
    audits = []
    for original in plan['fields']:
        if not isinstance(original, dict):
            _fail('ANNOTATED_FIELD_ENTRY_INVALID')
        entry = deepcopy(original)
        sku = _required_text(entry.get('sku'), 'ANNOTATED_SKU_REQUIRED')
        field = entry.get('field')
        if field not in FIELDS:
            _fail('ANNOTATED_UNKNOWN_FIELD:' + str(field))
        pair = (sku, field)
        if pair in entries or sku not in records:
            _fail('ANNOTATED_DUPLICATE_OR_UNKNOWN_ENTRY:' + sku + ':' + field)
        rec = records[sku]
        source_key, target_key, column = FIELDS[field]
        source_es = rec.get(source_key)
        target = rec.get(target_key)
        if not isinstance(source_es, str) or not source_es.strip():
            _fail('ANNOTATED_SOURCE_MISSING:' + sku + ':' + field)
        if entry.get('source_es') != source_es or entry.get('current_zh') != target:
            _fail('ANNOTATED_SOURCE_OR_TARGET_CHANGED:' + sku + ':' + field)
        field_hash = localization_field_source_hash(rec, field)
        if (entry.get('source_hash') != localization_source_hash(rec)
                or entry.get('field_source_hash') != field_hash
                or entry.get('current_target_hash') != value_hash(target)):
            _fail('ANNOTATED_FIELD_HASH_MISMATCH:' + sku + ':' + field)
        metadata = coerce_field_provenance(rec.get('zh_field_provenance')).get(field) or {}
        if metadata.get('source_hash') != field_hash or metadata.get('value') != target:
            _fail('ANNOTATED_PROVENANCE_MISMATCH:' + sku + ':' + field)
        action = entry.get('action')
        if action == 'RETAIN_PENDING_BLANK':
            from .difference_whitelist import DifferenceWhitelist, pending_conflict_report
            item = (conflict_registry.entries.get((sku, target_key, 'ACK_DIFFERENCE'))
                    if conflict_registry is not None else None)
            if not item or item.get('id') != entry.get('tracking_entry_id'):
                _fail('ANNOTATED_PENDING_BLANK_TRACKING_REQUIRED:' + sku + ':' + field)
            checked = pending_conflict_report(DifferenceWhitelist([item]), [rec])
            if checked['tracked_field_count'] != 1 or checked['recheck_required_count']:
                _fail('ANNOTATED_TRACKING_RECHECK_REQUIRED')
            if str(target or '').strip() or str(metadata.get('review_status') or '').upper() not in {'PENDING', 'REVIEW_REQUIRED'}:
                _fail('ANNOTATED_PENDING_BLANK_REQUIRED:' + sku + ':' + field)
        elif str(metadata.get('review_status') or '').upper() not in _APPROVED:
            _fail('ANNOTATED_UNAPPROVED_FIELD:' + sku + ':' + field)
        if action not in ACTIONS:
            _fail('ANNOTATED_UNKNOWN_ACTION:' + str(action))
        if str(metadata.get('freshness_status') or '').upper() != 'CURRENT' and action != 'QUARANTINE':
            _fail('ANNOTATED_STALE_MUST_BE_QUARANTINED:' + sku + ':' + field)
        _required_text(entry.get('reason'), 'ANNOTATED_REASON_REQUIRED')
        declared = entry.get('allowed_repair_codes')
        if not isinstance(declared, list) or not all(isinstance(x, str) for x in declared) or len(declared) != len(set(declared)):
            _fail('ANNOTATED_DECLARED_REPAIR_CODES_REQUIRED')
        codes = actual_codes.get(pair, set())
        if set(declared) != codes:
            _fail('ANNOTATED_REPAIR_CODES_CHANGED:' + sku + ':' + field)
        if action == 'RETAIN_SOURCE_NOTE':
            if field != 'details' or codes - {'SOURCE_DETAILS_TYPED_VALUE_INVALID'}:
                _fail('ANNOTATED_SOURCE_NOTE_CANNOT_COVER_REPAIR:' + sku)
            _validated_note(source_es, str(target or ''), str(by_sku[sku].get(column) or ''))
        elif action in {'RETAIN_WITH_CONFLICT_NOTE', 'RETAIN_PENDING_BLANK'}:
            if codes:
                _fail('ANNOTATED_CONFLICT_NOTE_CANNOT_COVER_REPAIR:' + sku)
        elif codes - _QUARANTINE_CODES:
            _fail('ANNOTATED_QUARANTINE_RULE_UNSUPPORTED:' + sku)
        entries[pair] = entry
        before = by_sku[sku].get(column)
        after = QUARANTINE_TEXT if action == 'QUARANTINE' else before
        addition = _remarks(entry)
        existing = by_sku[sku].get('备注')
        if existing is not None and not isinstance(existing, str):
            _fail('ANNOTATED_REMARKS_NOT_TEXT')
        by_sku[sku][column] = after
        by_sku[sku]['备注'] = (existing + '\n' if existing else '') + addition
        if len(by_sku[sku]['备注']) > 32767:
            _fail('ANNOTATED_REMARKS_EXCEED_EXCEL_LIMIT')
        audits.append({**entry, 'column': column, 'original_display_value': before,
                       'final_value': after, 'after': after, 'note': addition,
                       'remarks_addition': addition, 'review_status': metadata.get('review_status'),
                       'freshness_status': metadata.get('freshness_status'),
                       'source_approved_by': metadata.get('approved_by'),
                       'source_fact_resolved': False})
    if set(actual_codes) - set(entries):
        _fail('ANNOTATED_UNDECLARED_BLOCKING_FIELD')
    price_notes = plan.get('original_price_notices', [])
    if price_notes:
        if strict_audit.get('status') != 'FAIL' or strict_audit.get('counts', {}).get('MASTER_QUALITY_BLOCKED') != 1:
            _fail('ANNOTATED_PRICE_STRICT_AUDIT_MISMATCH')
        if price_notes != original_price_notices(source, baseline, master_quality):
            _fail('ANNOTATED_PRICE_PLAN_CHANGED')
        for notice in price_notes:
            row = by_sku[notice['sku']]
            row['备注'] = (row.get('备注') + '\n' if row.get('备注') else '') + notice['note']
            if len(row['备注']) > 32767:
                _fail('ANNOTATED_REMARKS_EXCEED_EXCEL_LIMIT')
    _validate_strict(strict_audit, entries, disclosed_original_prices=bool(price_notes))
    report = {
        'contract': CONTRACT, 'source_commit_id': source.source_commit_id,
        'source_hash': source_hash, 'export_date': source.export_date,
        'annotation_plan': deepcopy(plan), 'plan_digest': _digest(plan),
        'fields': audits, 'fields_count': len(audits),
        'sku_count': len(final), 'annotated_sku_count': len({a['sku'] for a in audits}),
        'strict_audit': deepcopy(strict_audit), 'repair_audit': deepcopy(repair_audit),
        'strict_source_audit': deepcopy(strict_audit),
        'baseline_rows_hash': full_delivery_rows_hash(baseline, HEADERS),
        'final_rows_hash': full_delivery_rows_hash(final, HEADERS),
        'full_rows_hash': full_delivery_rows_hash(final, HEADERS),
        'annotated_delivery_ready': True, 'research_verified': False,
        'source_fact_resolved': False, 'source_or_provenance_modified': False,
        'original_price_notices': deepcopy(price_notes), 'master_quality_audit': deepcopy(master_quality),
    }
    verify_annotation_delivery(source, baseline, final, report)
    return final, report


def verify_annotation_delivery(source, baseline_rows, final_rows, report) -> None:
    """Read-back verifier: only declared cells and their full evidence may differ."""
    if not isinstance(report, dict) or report.get('contract') != CONTRACT:
        _fail('ANNOTATED_REPORT_INVALID')
    plan = report.get('annotation_plan')
    _validate_source(source, baseline_rows, plan, report.get('source_hash'))
    if report.get('plan_digest') != _digest(plan):
        _fail('ANNOTATED_PLAN_DIGEST_MISMATCH')
    if report.get('baseline_rows_hash') != full_delivery_rows_hash(baseline_rows, HEADERS):
        _fail('ANNOTATED_BASELINE_ROWS_CHANGED')
    if report.get('final_rows_hash') != full_delivery_rows_hash(final_rows, HEADERS):
        _fail('ANNOTATED_FINAL_ROWS_CHANGED')
    if report.get('full_rows_hash') != report.get('final_rows_hash'):
        _fail('ANNOTATED_FULL_ROWS_HASH_CHANGED')
    if report.get('research_verified') is not False or report.get('annotated_delivery_ready') is not True:
        _fail('ANNOTATED_RESEARCH_STATUS_FORGED')
    if (report.get('source_fact_resolved') is not False
            or report.get('source_or_provenance_modified') is not False
            or report.get('source_commit_id') != source.source_commit_id
            or report.get('export_date') != source.export_date
            or report.get('sku_count') != len(final_rows)):
        _fail('ANNOTATED_DELIVERY_STATUS_FORGED')
    if report.get('strict_source_audit') != report.get('strict_audit'):
        _fail('ANNOTATED_STRICT_AUDIT_CHANGED')
    fields = report.get('fields')
    if not isinstance(fields, list) or len(fields) != len(plan['fields']) or report.get('fields_count') != len(fields):
        _fail('ANNOTATED_REPORT_FIELDS_MISMATCH')
    expected = deepcopy(baseline_rows)
    mapping = {str(r['编号']): r for r in expected}
    if [(x.get('sku'), x.get('field')) for x in fields] != [(x.get('sku'), x.get('field')) for x in plan['fields']]:
        _fail('ANNOTATED_REPORT_FIELD_ORDER_MISMATCH')
    for audit, entry in zip(fields, plan['fields']):
        rec = next(r for r in source.records if str(r.get('sku') or '') == entry['sku'])
        source_key, target_key, _ = FIELDS[entry['field']]
        meta = coerce_field_provenance(rec.get('zh_field_provenance')).get(entry['field']) or {}
        field_hash = localization_field_source_hash(rec, entry['field'])
        if (entry['source_hash'] != localization_source_hash(rec)
                or entry['source_es'] != rec.get(source_key)
                or entry['current_zh'] != rec.get(target_key)
                or entry['current_target_hash'] != value_hash(rec.get(target_key))
                or entry['field_source_hash'] != field_hash or meta.get('source_hash') != field_hash
                or meta.get('value') != rec.get(target_key)
                or audit.get('freshness_status') != meta.get('freshness_status')
                or audit.get('review_status') != meta.get('review_status')):
            _fail('ANNOTATED_READBACK_SOURCE_OR_PROVENANCE_CHANGED')
        column = FIELDS[entry['field']][2]
        row = mapping[entry['sku']]
        after = QUARANTINE_TEXT if entry['action'] == 'QUARANTINE' else row[column]
        if (audit.get('original_display_value') != row[column] or audit.get('final_value') != after
                or audit.get('after') != after or audit.get('note') != _remarks(entry)
                or audit.get('remarks_addition') != _remarks(entry)):
            _fail('ANNOTATED_REPORT_ACTION_MISMATCH')
        row[column] = after
        row['备注'] = (row['备注'] + '\n' if row.get('备注') else '') + _remarks(entry)
    price_notes = plan.get('original_price_notices', [])
    if report.get('original_price_notices', []) != price_notes:
        _fail('ANNOTATED_PRICE_REPORT_CHANGED')
    if price_notes:
        if price_notes != original_price_notices(source, baseline_rows, report.get('master_quality_audit')):
            _fail('ANNOTATED_PRICE_PLAN_CHANGED')
        for notice in price_notes:
            row = mapping[notice['sku']]
            row['备注'] = (row.get('备注') + '\n' if row.get('备注') else '') + notice['note']
    if full_delivery_rows_hash(expected, HEADERS) != full_delivery_rows_hash(final_rows, HEADERS):
        _fail('ANNOTATED_UNAUTHORIZED_CELL_CHANGE')
