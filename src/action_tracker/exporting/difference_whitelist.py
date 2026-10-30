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
        raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict) or raw.get('contract') != CONTRACT or not isinstance(raw.get('entries'), list):
            raise DifferenceWhitelistError('DIFFERENCE_WHITELIST_CONTRACT_INVALID')
        return cls(raw['entries'])

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
