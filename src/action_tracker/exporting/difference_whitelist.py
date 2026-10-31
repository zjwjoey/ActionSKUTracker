"""Read-only, expiring acknowledgements of exact reviewed field differences.

This registry never supplies a translation or approval. The one QA policy
action is restricted to an exact reviewed brand token in a no-brand title;
it cannot waive numeric, model, source-quality or body-text findings.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .repair_overrides import field_source_hash
from ..localization.qa import _casefold_token_count

CONTRACT = 'EXPORT_DIFFERENCE_WHITELIST_V1'
FIELDS = {'name_zh', 'cat1_zh', 'cat2_zh', 'spec_zh', 'desc_zh', 'details_zh'}
ACTIONS = {'ACK_DIFFERENCE', 'NO_BRAND_TITLE'}
CONFIRMED = {'EQUIVALENT_FORMAT', 'CONFIRMED_POLICY_DIFFERENCE', 'PROGRAM_FALSE_POSITIVE'}
# These tokens are product facts even when a residual allowlist accepts them.
TECHNICAL = {'USB', 'USB-C', 'USB-A', 'AA', 'AAA', 'LED', 'FSC', 'BCI', 'PEFC',
             'MDF', 'UV', 'RC', 'CO2', 'GSM', 'HDMI', 'PD', 'IP', 'WIFI', 'GAN'}


class DifferenceWhitelistError(ValueError):
    pass


def value_hash(value) -> str:
    return hashlib.sha256(str(value or '').strip().encode('utf-8')).hexdigest()


def _contains(text, span):
    return re.search(r'(?<![\w])' + re.escape(span) + r'(?![\w])', str(text), re.I) is not None


class DifferenceWhitelist:
    def __init__(self, entries=()):
        self.entries = {}
        self.tracked_skus = set()
        for raw in entries:
            item = dict(raw)
            sku, field, action = (str(item.get(k) or '').strip() for k in ('sku', 'field', 'action'))
            if not sku.isdigit() or field not in FIELDS or action not in ACTIONS:
                raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_IDENTITY_INVALID')
            if item.get('status') not in {'CONFIRMED', 'PENDING'}:
                raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_STATUS_INVALID')
            key = (sku, field, action)
            if key in self.entries:
                raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_DUPLICATE')
            if any(not re.fullmatch('[a-f0-9]{64}', str(item.get(k) or ''))
                   for k in ('source_hash', 'current_hash', 'reviewed_hash')):
                raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_HASH_REQUIRED')
            self.entries[key] = item
            self.tracked_skus.add(sku)

    @classmethod
    def load(cls, path):
        payload = Path(path).read_bytes()
        raw = json.loads(payload.decode('utf-8-sig'))
        if not isinstance(raw, dict) or raw.get('contract') != CONTRACT or not isinstance(raw.get('entries'), list):
            raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_CONTRACT_INVALID')
        registry = cls(raw['entries'])
        registry.registry_path = str(Path(path).resolve())
        registry.registry_sha256 = hashlib.sha256(payload).hexdigest()
        return registry

    def match(self, record, field, current, reviewed, *, action='ACK_DIFFERENCE'):
        item = self.entries.get((str(record.get('sku') or ''), field, action))
        if not item or item.get('status') != 'CONFIRMED' or item.get('classification') not in CONFIRMED:
            return None
        if (item['source_hash'] != field_source_hash(record, field) or
                item['current_hash'] != value_hash(current) or item['reviewed_hash'] != value_hash(reviewed)):
            return None
        if not all(item.get(k) for k in ('reason', 'evidence', 'reviewed_by', 'reviewed_at', 'expires_at')):
            return None
        try:
            reviewed_at = datetime.fromisoformat(item['reviewed_at'].replace('Z', '+00:00'))
            expiry = datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if not reviewed_at.tzinfo or not expiry.tzinfo or not reviewed_at <= now < expiry:
                return None
            if expiry - reviewed_at > timedelta(days=90):
                return None
        except (ValueError, TypeError):
            return None
        return item

    def title_brand_match(self, record, current, code, evidence):
        if code not in {'PROTECTED_TOKEN_MISSING', 'PROTECTED_TOKEN_CHANGED'} or evidence.get('token_type') != 'TECH':
            return None
        item = self.match(record, 'name_zh', current, current, action='NO_BRAND_TITLE')
        if not item or item.get('classification') != 'PROGRAM_FALSE_POSITIVE':
            return None
        token = str(evidence.get('value') or '')
        span = str(item.get('brand_span') or '')
        # No catch-all exemption for a SKU, arbitrary issue code, unit or model.
        if (not re.fullmatch('[A-Za-z]{2,12}', token) or token.upper() in TECHNICAL or
                token.casefold() not in {str(t).casefold() for t in item.get('brand_tokens', [])} or
                not span or not _contains(record.get('name_es') or '', span) or not _contains(span, token)):
            return None
        # The acknowledgement covers one exact brand occurrence. Identical
        # model/code tokens elsewhere still need their full source count.
        remaining = re.sub(r'(?<![\w])' + re.escape(span) + r'(?![\w])', ' ',
                           str(record.get('name_es') or ''), count=1, flags=re.I)
        if _casefold_token_count(str(current), token) < _casefold_token_count(remaining, token):
            return None
        return item


def whitelist_from_config(cfg):
    options = cfg.get('export_difference_whitelist') or {}
    enabled = options.get('enabled', False)
    if not isinstance(enabled, bool):
        raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_ENABLED_BOOLEAN_REQUIRED')
    if not enabled:
        return DifferenceWhitelist()
    if not options.get('path'):
        raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_PATH_REQUIRED')
    path = Path(options['path'])
    if not path.is_absolute():
        path = Path(cfg['project_root']) / path
    return DifferenceWhitelist.load(path)


def _tracking_entries(registry):
    """Only explicitly registered unresolved conflicts, never legacy ACKs."""
    result = []
    for item in registry.entries.values():
        flag = item.get('tracking_only', False)
        if not isinstance(flag, bool):
            raise DifferenceWhitelistError('CONFLICT_TRACKING_BOOLEAN_REQUIRED')
        if not flag:
            continue
        if (item['status'] != 'PENDING' or item['action'] != 'ACK_DIFFERENCE'
                or item.get('automatic_apply') is not False
                or item.get('does_not_waive_qa_or_approval') is not True):
            raise DifferenceWhitelistError('CONFLICT_TRACKING_MUST_REMAIN_PENDING_NO_APPLY')
        result.append(item)
    return result


def conflict_tracking_from_config(cfg):
    options = (cfg.get('localization') or {}).get('source_conflict_tracking', {})
    if not isinstance(options, dict) or not isinstance(options.get('enabled', False), bool):
        raise DifferenceWhitelistError('CONFLICT_TRACKING_ENABLED_BOOLEAN_REQUIRED')
    if not options.get('enabled', False):
        return None
    if not options.get('path'):
        raise DifferenceWhitelistError('CONFLICT_TRACKING_PATH_REQUIRED')
    path = Path(options['path'])
    if not path.is_absolute():
        path = Path(cfg['project_root']) / path
    registry = DifferenceWhitelist.load(path)
    _tracking_entries(registry)
    return registry


def pending_conflict_report(registry, records, *, now=None):
    """Observe exact field bindings without updating registry, QA or approvals."""
    from ..localization.contracts import LOCALIZATION_FIELD_CONTRACT
    now = now or datetime.now(timezone.utc)
    if not now.tzinfo:
        raise DifferenceWhitelistError('CONFLICT_TRACKING_TIMEZONE_REQUIRED')
    current = {}
    for record in records:
        sku = str(record.get('sku') or record.get('official_sku') or '')
        if not sku or sku in current:
            raise DifferenceWhitelistError('CONFLICT_TRACKING_CURRENT_IDENTITY_INVALID')
        current[sku] = record
    observations, blocked = [], {}
    for item in _tracking_entries(registry):
        sku, field = item['sku'], item['field']
        source = LOCALIZATION_FIELD_CONTRACT[field]['source']
        record = current.get(sku)
        changes = []
        observed_source = observed_current = None
        if record is None:
            changes.append('NOT_IN_CHECK_SCOPE')
        else:
            if source not in record or record[source] is None:
                changes.append('SOURCE_VALUE_UNAVAILABLE')
            else:
                observed_source = field_source_hash(record, field)
                if observed_source != item['source_hash']:
                    changes.append('SOURCE_HASH_CHANGED')
            if field not in record:
                changes.append('CURRENT_ZH_UNAVAILABLE')
            else:
                observed_current = value_hash(record[field])
                if observed_current != item['current_hash']:
                    changes.append('CURRENT_ZH_HASH_CHANGED')
        try:
            reviewed = datetime.fromisoformat(str(item['reviewed_at']).replace('Z', '+00:00'))
            expiry = datetime.fromisoformat(str(item['expires_at']).replace('Z', '+00:00'))
            if (not reviewed.tzinfo or not expiry.tzinfo or reviewed > now
                    or expiry <= reviewed or expiry - reviewed > timedelta(days=90)
                    or not all(item.get(k) for k in ('id', 'reason', 'evidence', 'reviewed_by'))):
                raise ValueError('invalid metadata')
            if now >= expiry:
                changes.append('REGISTRATION_EXPIRED')
        except (KeyError, TypeError, ValueError):
            changes.append('REGISTRATION_METADATA_INVALID')
        blocked.setdefault(sku, []).append(field)
        observations.append({'entry_id': item.get('id'), 'sku': sku, 'field': field,
            'status': 'PENDING', 'check_state': 'RECHECK_REQUIRED' if changes else 'KNOWN_CONFLICT_UNCHANGED',
            'requires_recheck': bool(changes), 'changes': changes, 'reason': item.get('reason', ''),
            'evidence': item.get('evidence', []), 'expected_source_hash': item['source_hash'],
            'observed_source_hash': observed_source, 'expected_current_hash': item['current_hash'],
            'observed_current_hash': observed_current, 'automatic_apply': False,
            'does_not_waive_qa_or_approval': True})
    attention = sum(x['requires_recheck'] for x in observations)
    return {'contract': 'SOURCE_CONFLICT_TRACKING_REPORT_V1', 'generated_at': now.isoformat(),
        'status': 'RECHECK_REQUIRED' if attention else 'TRACKED_PENDING' if observations else 'NO_TRACKED_CONFLICTS',
        'registry': getattr(registry, 'registry_path', None),
        'registry_sha256': getattr(registry, 'registry_sha256', None),
        'tracked_field_count': len(observations), 'tracked_sku_count': len(blocked),
        'known_unchanged_count': sum(not x['requires_recheck'] for x in observations),
        'recheck_required_count': attention,
        'blocked_fields_by_sku': {s: sorted(fs) for s, fs in blocked.items()}, 'entries': observations,
        'read_only': True, 'production_apply': False, 'whole_sku_bypass': False}


def check_current_conflicts(registry, database, *, now=None):
    """Read CURRENT directly with mode=ro; never initialize a production DB."""
    import sqlite3
    path = Path(database).resolve()
    if not path.is_file():
        raise DifferenceWhitelistError('CONFLICT_TRACKING_DATABASE_MISSING')
    skus = sorted({x['sku'] for x in _tracking_entries(registry)})
    records = []
    if skus:
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            sql = """SELECT p.official_sku sku,coalesce(es.name,p.name_es) name_es,
                es.cat1 cat1_es,es.cat2 cat2_es,es.spec spec_es,es.description desc_es,es.details details_es,
                coalesce(zh.name,p.name_zh) name_zh,zh.cat1 cat1_zh,zh.cat2 cat2_zh,
                zh.spec spec_zh,zh.description desc_zh,zh.details details_zh
                FROM products p
                LEFT JOIN product_localizations es ON es.official_sku=p.official_sku AND es.language='es'
                LEFT JOIN product_localizations zh ON zh.official_sku=p.official_sku AND zh.language='zh'
                WHERE p.status='CURRENT' AND p.official_sku IN (""" + ','.join('?' for _ in skus) + ')'
            records = [dict(r) for r in db.execute(sql, skus)]
        finally:
            db.close()
    report = pending_conflict_report(registry, records, now=now)
    report['database'] = str(path)
    return report
