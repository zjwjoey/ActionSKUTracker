"""Finalize reviewed gold data and split field-level data by SKU without leakage."""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

# When executed as ``python scripts/...py`` Python puts ``scripts`` (rather
# than the repository root) on ``sys.path``.  Add the root explicitly so the
# canonical production hash implementation is used in both CLI and tests.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from action_tracker.services.hashing import localization_source_hash

NUM = re.compile(r"\d+(?:[.,]\d+)?")
HTML_TAG = re.compile(r"<[^>]+>")
NULL_PREFIX = re.compile(r"^\s*null\.", re.IGNORECASE)
UI_COPY = re.compile(r"añadir a tus favoritos|加入收藏", re.IGNORECASE)
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")

def nums(s): return sorted(x.replace(",", ".") for x in NUM.findall(s or ""))
def obj(row, role): return json.loads(next(m["content"] for m in row["messages"] if m["role"] == role))
def bucket(key):
    n = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100
    return "test" if n < 10 else ("validation" if n < 20 else "train")


def norm(value): return re.sub(r"\s+", " ", str(value or "").strip().lower())


def unsafe_reason(value):
    text = str(value or "")
    if HTML_TAG.search(text): return "HTML"
    if NULL_PREFIX.search(text): return "NULL_PREFIX"
    if UI_COPY.search(text): return "UI_COPY"
    return None


def unsafe_fields(source, target=None):
    """Return source/target contamination reasons for one reviewed SKU."""
    found = []
    for field in FIELDS:
        reason = unsafe_reason((source or {}).get(field, ""))
        if reason: found.append(f"source:{field}:{reason}")
        if target is not None:
            reason = unsafe_reason((target or {}).get(field, ""))
            if reason: found.append(f"target:{field}:{reason}")
    return found


def source_hash_for_source(source):
    """Return the provenance hash for a reviewed row's Spanish source payload."""
    source = source or {}
    return localization_source_hash({
        "name_es": source.get("name", ""),
        "cat1_es": source.get("cat1", ""),
        "cat2_es": source.get("cat2", ""),
        "spec_es": source.get("spec", ""),
        "desc_es": source.get("description", ""),
        "details_es": source.get("details", ""),
    })


def source_hash_from_messages(row):
    """Recompute provenance from the serialized user message for auditing."""
    source = json.loads(next(m["content"] for m in row["messages"] if m["role"] == "user"))
    return source_hash_for_source(source)


class UnionFind:
    def __init__(self, values): self.parent = {v: v for v in values}
    def find(self, value):
        if self.parent[value] != value: self.parent[value] = self.find(self.parent[value])
        return self.parent[value]
    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b: self.parent[b] = a


def split_grouped(rows, identity):
    """Split connected duplicate-text/SKU components as indivisible groups."""
    sku_rows = defaultdict(list); skus = set(); by_fp = {}; uf = UnionFind([])
    for row in rows:
        sku, fp = identity(row); sku = str(sku); skus.add(sku); uf.parent.setdefault(sku, sku); sku_rows[sku].append(row)
        if fp in by_fp: uf.union(sku, by_fp[fp])
        else: by_fp[fp] = sku
    components = defaultdict(list)
    for sku, values in sku_rows.items(): components[uf.find(sku)].extend(values)
    result = defaultdict(list)
    for values in components.values():
        result[bucket(min(str(identity(v)[0]) for v in values))].extend(values)
    return result, len(by_fp), len(components)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--date",default="2026-09-08"); args=ap.parse_args()
    root=_ROOT; base=root/"runtime/training/qwen3_8b"/args.date.replace("-","")
    review=[json.loads(x) for x in (base/"qwen_gold_review_1200.jsonl").open(encoding="utf-8") if x.strip()]
    resolution_path = base / "qwen_human_resolutions_8.jsonl"
    resolutions = {
        str((item := json.loads(line))["sku"]): item
        for line in resolution_path.open(encoding="utf-8") if line.strip()
    } if resolution_path.exists() else {}
    candidate_path=base/"qwen_candidates_5000.jsonl"
    candidates={str(json.loads(x)["metadata"]["sku"]): json.loads(x) for x in candidate_path.open(encoding="utf-8") if x.strip()}
    gold_path=base/"qwen_gold_1200.jsonl"; clean_path=base/"qwen_gold_clean.jsonl"; warnings=Counter(); critical=[]; excluded=[]; applied=Counter(); unsafe_excluded=[]
    # A training example is an immutable source/target pair: its metadata hash
    # must describe the six Spanish fields actually present in ``messages``.
    # Do not bind it to a later live database snapshot, which may only contain
    # harmless formatting changes and must not silently rewrite provenance.
    clean_handle=clean_path.open("w",encoding="utf-8")
    with gold_path.open("w",encoding="utf-8") as out:
        for row in review:
            resolution = resolutions.get(str(row["sku"]))
            if resolution and resolution.get("status") == "SOURCE_CONFLICT_EXCLUDED":
                excluded.append({"sku": row["sku"], "reason": resolution.get("reason", "")})
                continue
            if resolution and resolution.get("status") == "HUMAN_CONFIRMED":
                target = resolution["target"]
                reviewer, review_type = "human_resolution", "human_confirmed"
                applied[resolution["status"]] += 1
            else:
                target=row["corrected"] if row["verdict"]=="REVISE" else row["original_target"]
                reviewer, review_type = "deepseek-chat", "model_reviewed_silver"
            bad = unsafe_fields(row.get("source", {}), target)
            if bad:
                unsafe_excluded.append({"sku": row["sku"], "reasons": bad})
                continue
            if set(target)!=set(FIELDS) or any(not str(target.get(f) or "").strip() for f in FIELDS): warnings["invalid_target"]+=1; continue
            flags=[f for f in FIELDS if nums(row["source"].get(f,""))!=nums(target.get(f,""))]
            for f in flags: warnings[f"numeric_flag_{f}"]+=1
            source = row["source"]
            snapshot_source_hash = source_hash_for_source(source)
            rendered=json.dumps({"messages":[
                {"role":"system","content":"将 Action 西语商品六字段忠实标准化为中文；保持数字、单位、数量和品牌/型号，不臆造。"},
                {"role":"user","content":json.dumps(source,ensure_ascii=False,sort_keys=True)},
                {"role":"assistant","content":json.dumps(target,ensure_ascii=False,sort_keys=True)}],
                "metadata":{"sku":row["sku"],"source_hash":snapshot_source_hash,"target_source":"OFFICIAL_FACT","review_verdict":row["verdict"],"reviewer":reviewer,"review_type":review_type}},ensure_ascii=False)
            out.write(rendered+"\n")
            if flags:
                critical.append({"sku":row["sku"],"fields":flags,"verdict":row["verdict"]})
            else: clean_handle.write(rendered+"\n")
    clean_handle.close()
    (base / "qwen_gold_source_conflict_excluded.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in excluded) + ("\n" if excluded else ""), encoding="utf-8")
    (base / "qwen_gold_unsafe_source_excluded.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in unsafe_excluded) + ("\n" if unsafe_excluded else ""), encoding="utf-8")
    (base/"qwen_gold_critical_numeric_review.jsonl").write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in critical)+("\n" if critical else ""),encoding="utf-8")
    clean_rows=[json.loads(x) for x in clean_path.open(encoding="utf-8") if x.strip()]
    gold_source_hash_mismatches = []
    for line in gold_path.open(encoding="utf-8"):
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("metadata", {}).get("source_hash") != source_hash_from_messages(item):
            gold_source_hash_mismatches.append(str(item.get("metadata", {}).get("sku", "")))
    gold_splits, gold_fingerprints, gold_groups = split_grouped(
        clean_rows,
        lambda row: (str(row["metadata"]["sku"]), tuple(norm(m["content"]) for m in row["messages"] if m["role"] in {"user", "assistant"})),
    )
    for name, values in gold_splits.items():
        with (base/f"qwen_gold_clean_{name}.jsonl").open("w",encoding="utf-8") as handle:
            for row in values: handle.write(json.dumps(row,ensure_ascii=False)+"\n")
    # Split all field-level examples by SKU, so fields from one product cannot leak across sets.
    field_path=base/"qwen_field_examples_all.jsonl"; field_rows=[json.loads(x) for x in field_path.open(encoding="utf-8") if x.strip()]
    splits, field_fingerprints, field_groups = split_grouped(
        field_rows,
        lambda row: (str(row["metadata"]["sku"]), ((row["metadata"].get("field",""), str(row["metadata"]["sku"])) if row["metadata"].get("field") in {"cat1", "cat2"} else (row["metadata"].get("field",""), norm(next(m["content"] for m in row["messages"] if m["role"]=="user")), norm(next(m["content"] for m in row["messages"] if m["role"]=="assistant"))))),
    )
    for name, rows in splits.items():
        with (base/f"qwen_field_{name}.jsonl").open("w",encoding="utf-8") as out:
            for row in rows: out.write(json.dumps(row,ensure_ascii=False)+"\n")
    sku_sets={name:{str(r["metadata"]["sku"]) for r in rows} for name,rows in splits.items()}
    overlap=any(sku_sets[a]&sku_sets[b] for a in sku_sets for b in sku_sets if a<b)
    dup=len(field_rows)-len({(str(r["metadata"]["sku"]),r["metadata"]["field"]) for r in field_rows})
    gold_sets={k:{str(r["metadata"]["sku"]) for r in v} for k,v in gold_splits.items()}
    gold_overlap=any(gold_sets[a]&gold_sets[b] for a in gold_sets for b in gold_sets if a<b)
    def cross_overlap(groups, identity):
        indexes=[]
        for values in groups.values(): indexes.append({identity(r)[1] for r in values})
        return sum(len(indexes[i] & indexes[j]) for i in range(len(indexes)) for j in range(i+1,len(indexes)))
    gold_text_overlap=cross_overlap(gold_splits, lambda row: (row["metadata"]["sku"], tuple(norm(m["content"]) for m in row["messages"] if m["role"] in {"user","assistant"})))
    def field_identity(row):
        field = row["metadata"].get("field")
        if field in {"cat1", "cat2"}: fp = (field, str(row["metadata"]["sku"]))
        else: fp = (field, norm(next(m["content"] for m in row["messages"] if m["role"] == "user")), norm(next(m["content"] for m in row["messages"] if m["role"] == "assistant")))
        return row["metadata"]["sku"], fp
    # cat1/cat2 are a closed set by contract, so the same source/target pair
    # is expected in multiple SKU splits. Report that overlap separately and
    # keep free-text leakage as the actual validation gate.
    freeform_splits={name:[r for r in values if r["metadata"].get("field") not in {"cat1", "cat2"}] for name, values in splits.items()}
    field_text_overlap=cross_overlap(freeform_splits, field_identity)
    category_splits={name:[r for r in values if r["metadata"].get("field") in {"cat1", "cat2"}] for name, values in splits.items()}
    def category_pair_identity(row):
        return row["metadata"]["sku"], (row["metadata"].get("field"), norm(next(m["content"] for m in row["messages"] if m["role"]=="user")), norm(next(m["content"] for m in row["messages"] if m["role"]=="assistant")))
    category_pair_overlap=cross_overlap(category_splits, category_pair_identity)
    summary={"gold_input":len(review),"gold_output":sum(1 for _ in gold_path.open(encoding="utf-8")),"gold_clean_output":len(clean_rows),"gold_clean_splits":{k:len(v) for k,v in gold_splits.items()},"gold_sku_overlap":gold_overlap,"gold_text_fingerprint_overlap":gold_text_overlap,"gold_fingerprint_count":gold_fingerprints,"gold_numeric_warning_rows":len(critical),"gold_source_conflict_excluded":len(excluded),"gold_unsafe_source_excluded":len(unsafe_excluded),"gold_source_hash_mismatch_count":len(gold_source_hash_mismatches),"gold_source_hash_mismatch_skus":gold_source_hash_mismatches[:20],"human_resolutions_applied":dict(applied),"verdicts":dict(Counter(r["verdict"] for r in review)),"gold_warnings":dict(warnings),"field_input":len(field_rows),"field_splits":{k:len(v) for k,v in splits.items()},"field_sku_overlap":overlap,"field_text_fingerprint_overlap":field_text_overlap,"field_category_pair_overlap":category_pair_overlap,"field_leakage_policy":"cat1/cat2 are closed-set evaluation; free-text fields must have zero cross-split fingerprint overlap","field_fingerprint_count":field_fingerprints,"field_duplicate_keys":dup,"validation":"PASS" if not gold_overlap and not overlap and not gold_text_overlap and not field_text_overlap and dup==0 and not warnings["invalid_target"] and not gold_source_hash_mismatches else "REVIEW_REQUIRED"}
    (base/"final_dataset_audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__": main()
