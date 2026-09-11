"""DeepSeek review of a deterministic 1,200-SKU gold-label sample."""
from __future__ import annotations

import argparse, csv, hashlib, json, os, time, urllib.request
from collections import defaultdict
from pathlib import Path


def obj(row, role):
    return json.loads(next(m["content"] for m in row["messages"] if m["role"] == role))


SYSTEM = """你是 Action 西班牙站中文训练集的严格金标审校员。逐条对照西语源和当前中文目标，只检查六字段：name、cat1、cat2、spec、description、details。必须忠实，数字、单位、尺寸、数量、否/是不能错；普通西语必须翻译；品牌、系列、型号可保留但不能臆造。分类1只能使用既定15类。若全部正确 verdict=PASS；有可明确修正的错误 verdict=REVISE并给出修正后的六字段；源信息不足或无法判断 verdict=REJECT。只返回 JSON：{"items":[{"sku":"","verdict":"PASS|REVISE|REJECT","corrected":{...六字段...},"reason":""}]}，必须返回全部 SKU。"""


def call(key, batch):
    payload={"model":"deepseek-chat","temperature":0.0,"max_tokens":12000,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":"逐条审校以下记录：\n"+json.dumps({"items":[{"sku":r["metadata"]["sku"],"source":obj(r,"user"),"target":obj(r,"assistant")} for r in batch]},ensure_ascii=False)}]}
    req=urllib.request.Request("https://api.deepseek.com/chat/completions",data=json.dumps(payload,ensure_ascii=False).encode(),headers={"Content-Type":"application/json","Authorization":"Bearer "+key},method="POST")
    with urllib.request.urlopen(req,timeout=180) as resp: body=json.loads(resp.read().decode())
    items=json.loads(body["choices"][0]["message"]["content"])["items"]
    expected={r["metadata"]["sku"] for r in batch}; got={str(x.get("sku")) for x in items}
    if got!=expected: raise ValueError(f"SKU_MISMATCH expected={len(expected)} got={len(got)}")
    return items


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--date",default="2026-09-08"); ap.add_argument("--limit",type=int,default=1200); ap.add_argument("--batch-size",type=int,default=8); args=ap.parse_args()
    root=Path(__file__).resolve().parents[1]; base=root/"runtime/training/qwen3_8b"/args.date.replace("-",""); source=next(base.glob("qwen_candidates_5000.jsonl")); rows=[json.loads(x) for x in source.open(encoding="utf-8")]
    flagged=[r for r in rows if r.get("metadata",{}).get("numeric_consistency")=="REVIEW"]; rest=[r for r in rows if r not in flagged]
    groups=defaultdict(list)
    for r in rest: groups[obj(r,"assistant").get("cat1","")].append(r)
    selected=list(flagged)
    while len(selected)<min(args.limit,len(rows)):
        advanced=False
        for k in sorted(groups):
            if groups[k]: selected.append(groups[k].pop(0)); advanced=True
            if len(selected)>=args.limit: break
        if not advanced: break
    key=os.environ.get("DEEPSEEK_API_KEY")
    if not key: raise RuntimeError("DEEPSEEK_API_KEY_MISSING")
    out=base/"qwen_gold_review_1200.jsonl"; report=base/"gold_review_progress.json"; results=[]
    if out.exists():
        results=[json.loads(x) for x in out.open(encoding="utf-8") if x.strip()]
    done={r["sku"] for r in results}
    pending=[r for r in selected if r["metadata"]["sku"] not in done]
    for off in range(0,len(pending),max(1,args.batch_size)):
        batch=pending[off:off+args.batch_size]
        last=None
        for attempt in range(1,4):
            try: verdicts=call(key,batch); break
            except Exception as exc: last=exc; time.sleep(attempt*2)
        else: raise RuntimeError(f"BATCH_FAILED offset={off}: {last}")
        by={str(v["sku"]):v for v in verdicts}
        with out.open("a",encoding="utf-8") as h:
            for r in batch:
                v=by[r["metadata"]["sku"]]; h.write(json.dumps({"sku":r["metadata"]["sku"],"source":obj(r,"user"),"original_target":obj(r,"assistant"),"verdict":v.get("verdict"),"corrected":v.get("corrected") or {},"reason":v.get("reason","")},ensure_ascii=False)+"\n"); results.append({"sku":r["metadata"]["sku"],"verdict":v.get("verdict")})
        report.write_text(json.dumps({"selected":len(selected),"completed":len(results),"pending":len(selected)-len(results)},ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(f"reviewed {len(results)}/{len(selected)}",flush=True)
    from collections import Counter
    summary={"selected":len(selected),"completed":len(results),"verdicts":dict(Counter(r.get("verdict") for r in results)),"output":str(out)}
    (base/"gold_review_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False))


if __name__ == "__main__": main()
