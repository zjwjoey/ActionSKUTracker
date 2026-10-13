"""Read-only source/target-bound semantic roles from approved Registry review."""
import json
import re
from .contracts import SemanticFact
from .hashes import value_hash
from ..services.hashing import localization_source_hash, localization_field_source_hash

FIELDS = dict(zip(('name','cat1','cat2','spec','description','details'),
                  ('name_es','cat1_es','cat2_es','spec_es','desc_es','details_es')))
TARGETS = dict(zip(FIELDS,('name_zh','cat1_zh','cat2_zh','spec_zh','desc_zh','details_zh')))

def read_approved_review_evidence(db):
    result={}
    rows=db.execute("""SELECT s.official_sku,s.source_hash,u.field_name,r.target_text,r.provenance_json
        FROM translation_units u JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
        JOIN translation_revisions r ON r.revision_id=u.current_revision_id
        WHERE r.review_status='APPROVED' AND r.qa_status='PASS'
        AND r.canonical_qa_status IN ('PASS','NOT_REQUIRED') AND u.freshness_status='FRESH'
        AND r.provenance_json LIKE '%semantic_facts_additions%'""").fetchall()
    for sku,source_hash,raw_field,target,payload in rows:
        field=next((k for k,v in FIELDS.items() if raw_field in {k,v}),None)
        if field is None:continue
        try:review=json.loads(payload or '{}').get('review') or {}
        except (ValueError,TypeError):continue
        if (review.get('review_model')!='CODEX' or review.get('decision') not in {'KEEP','CORRECTED'}
            or review.get('source_hash')!=source_hash or review.get('target')!=target):continue
        facts=review.get('semantic_facts_additions') or []
        if facts:
            result[(str(sku),field,str(source_hash),str(target))]={'source_hash':source_hash,'target_hash':value_hash(target),'facts':facts}
    return result

def reviewed_facts_for_field(record,field):
    evidence=(record.get('zh_reviewed_semantic_evidence') or {}).get(field) or {}
    provenance=(record.get('zh_field_provenance') or {}).get(field) or {}
    target=record.get(TARGETS[field])
    if (not evidence or evidence.get('source_hash')!=localization_source_hash(record)
        or evidence.get('target_hash')!=value_hash(str(target or ''))
        or provenance.get('value')!=target or provenance.get('review_status')!='APPROVED'
        or provenance.get('freshness_status')!='CURRENT'
        or provenance.get('source_hash')!=localization_field_source_hash(record,field)):
        return ()
    result=[]
    for raw in evidence.get('facts') or []:
        try:fact=SemanticFact(**raw)
        except (TypeError,ValueError):continue
        if (fact.source_field==FIELDS[field] and fact.evidence
            and re.search(rf"(?<![A-Za-z0-9]){re.escape(fact.source_text)}(?![A-Za-z0-9])",str(record.get(FIELDS[field]) or ''),re.I)):
            result.append(fact)
    return tuple(result)
