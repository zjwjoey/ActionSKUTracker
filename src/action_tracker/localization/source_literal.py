"""Owner-authorized literal display of an unusable official source value.

This does not correct source facts or waive numeric/model/boolean QA. It
recognizes an exact annotation only after checking a finite authorization
artifact and a field hash. Unknown content remains visibly unknown.
"""
import hashlib
import json
import re
from pathlib import Path
from ..services.hashing import localization_field_source_hash
from .normalization.structured_details import parse_structured_details

VERSION='OFFICIAL_SOURCE_LITERAL_ANNOTATION_V1'

def approved_literal_annotations(source, target, field, facts):
    source_field={'details':'details_es','description':'desc_es'}.get(field)
    if source_field is None:return ()
    source_text=str(getattr(source,source_field,'') or '')
    field_hash=localization_field_source_hash({source_field:source_text},field)
    result=[]
    for fact in facts:
        if (fact.semantic_type not in {'DETAIL_KEY','DESCRIPTION_FACT'} or fact.source_field!=source_field
            or fact.coverage_status!=VERSION or fact.fact_source_hash!=field_hash):continue
        try:
            ref=json.loads(fact.evidence);path=Path(ref['authorization_file']);raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=ref['sha256']:continue
            authorization=json.loads(raw)
        except (ValueError,TypeError,KeyError,OSError):continue
        identities=[r for r in authorization.get('fields',[]) if r.get('sku')==source.sku
                      and r.get('field')==field and r.get('field_source_hash')==field_hash
                      and r.get('source_value')==fact.source_text and r.get('marker')==fact.canonical_value]
        if (authorization.get('contract')!=VERSION or not authorization.get('authorization_text')
            or authorization.get('scope')!='TODAY_ON_SALE_ONLY'
            or not authorization.get('authorization_ref') or len(identities)!=1):continue
        marker=fact.canonical_value
        if not marker or target.count(marker)!=1:continue
        if field=='details':
            source_pairs=parse_structured_details(source_text);target_pairs=parse_structured_details(target)
            source_key='Incluye oído' if marker=='来源异常：官网字段Incluye oído='+fact.source_text else 'Sustancia'
            pairs=[p for p in source_pairs if p.key.casefold()==source_key.casefold() and p.value==fact.source_text]
            if len(pairs)!=1 or not marker.startswith('来源异常：官网字段'+source_key+'='+fact.source_text):continue
            if source_key=='Sustancia' and not any(v.strip().casefold() in {'válido','valido'} for v in pairs[0].value.split(',')):continue
            if source_key=='Incluye oído' and fact.source_text.casefold() not in {'sí','si','no'}:continue
            if len(source_pairs)!=len(target_pairs) or marker not in target_pairs[source_pairs.index(pairs[0])].source_text:continue
            # A literal anomaly marker must not coexist with a guessed state.
            if source_key=='Sustancia' and any(re.fullmatch(r'(?:产品)?(?:形态|物态|质地|状态|成分)',p.key)
                   for p in parse_structured_details(target)):continue
        else:
            # The policy supports a literal spelling anomaly, not arbitrary
            # untranslated prose or copying another source field.
            if (fact.source_text!='inoloras' or not re.search(r'\binoloras\b',source_text,re.I)
                or marker!='来源异常：官网原词inoloras'):continue
        result.append({'source_value':fact.source_text,'marker':marker,'field':field,
                       'field_source_hash':field_hash,'authorization_ref':authorization['authorization_ref']})
    return tuple(result)
