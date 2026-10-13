"""Run an offline shadow evaluation against the detail holdout set."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--valid', type=Path, required=True)
    p.add_argument('--base-model', required=True)
    p.add_argument('--adapter-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--max-new-tokens', type=int, default=256)
    a = p.parse_args()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.evaluate_qwen3_adapter import _parse_completion, _policy_format_pass
    from action_tracker.localization.formatter import format_spec
    from action_tracker.localization.ai import SourceFacts, validate_ai_response
    rows = [json.loads(x) for x in a.valid.read_text(encoding='utf-8').splitlines() if x.strip()]
    tok = AutoTokenizer.from_pretrained(a.adapter_dir, trust_remote_code=True)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(a.base_model, quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.float16), device_map='auto', trust_remote_code=True)
    model = PeftModel.from_pretrained(model, a.adapter_dir); model.eval()
    results = []
    for idx, row in enumerate(rows, 1):
        msgs = row['messages'][:2]
        prompt_payload = json.loads(msgs[-1]['content'])
        fields = tuple(prompt_payload.get('requested_fields', []))
        expected = json.loads(row['messages'][-1]['content']) if row['messages'][-1]['role'] == 'assistant' else {}
        inp = tok.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True, return_tensors='pt', return_dict=True, enable_thinking=False)
        inp = {k:v.to(model.device) for k,v in inp.items()}
        with torch.inference_mode(): out = model.generate(**inp, max_new_tokens=a.max_new_tokens, do_sample=False, pad_token_id=tok.eos_token_id)
        raw = tok.decode(out[0][inp['input_ids'].shape[1]:], skip_special_tokens=True)
        parsed = _parse_completion(raw) or {}
        got = parsed.get('fields') if isinstance(parsed.get('fields'), dict) else {}
        # Match the inference contract: specification formatting is a
        # deterministic post-processing step, not a model-quality override.
        got = dict(got)
        if isinstance(got.get('spec'), str):
            got['spec'] = format_spec(got['spec'])
        parsed['fields'] = got
        ok, reasons = _policy_format_pass(parsed, fields)
        source_record = dict(prompt_payload.get('source_facts') or {})
        source_record.update({
            'sku': prompt_payload.get('sku', row.get('metadata', {}).get('sku', '')),
            'name_es': source_record.get('name_es_raw', ''),
            'spec_es': source_record.get('spec_es_raw', ''),
        })
        source_facts = SourceFacts.from_record(source_record)
        validator_pass, validator_reasons = validate_ai_response(parsed, source_facts, fields)
        exp_fields = expected.get('fields') if isinstance(expected.get('fields'), dict) else {}
        results.append({'index': idx, 'sku': row.get('metadata',{}).get('sku'), 'field': row.get('metadata',{}).get('field'), 'source': prompt_payload.get('source_text',''), 'expected': exp_fields, 'predicted': got, 'json_object': bool(parsed), 'policy_format_pass': ok, 'validator_pass': validator_pass, 'reasons': reasons, 'validator_reasons': list(validator_reasons)})
        if idx % 10 == 0: print(f'{idx}/{len(rows)}')
    report = {'schema_version':1, 'valid_count':len(rows), 'results':results, 'policy_format_pass_rate': sum(r['policy_format_pass'] for r in results)/len(results) if results else 0, 'validator_pass_rate': sum(r['validator_pass'] for r in results)/len(results) if results else 0, 'production_apply':False, 'dictionary_write':False, 'primary_write':False}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'valid_count':len(rows), 'policy_format_pass_rate':report['policy_format_pass_rate'], 'validator_pass_rate':report['validator_pass_rate'], 'output':str(a.output)}, ensure_ascii=False))
    return 0
if __name__ == '__main__': raise SystemExit(main())
