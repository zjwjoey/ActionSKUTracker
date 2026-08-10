"""Read-only reconciliation of yesterday's confirmed CURRENT vs file state."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from action_tracker.excel.reader import load_current
from action_tracker import state

master=Path(r'F:\Action_Master\Action_Master.xlsx')
state_dir=ROOT/'runtime'/'state'
current=load_current(master)
known=state.load_known_skus(state_dir)
offline=state.load_offline_skus(state_dir)
current_skus=set(current); known_skus=set(known)
extra=sorted(known_skus-current_skus); missing=sorted(current_skus-known_skus); common=current_skus&known_skus
lines=['# Baseline Reconciliation','',f'- CURRENT_5537 source: `{master}`',f'- CURRENT count: {len(current_skus)}',f'- STATE known_skus count: {len(known_skus)}',f'- Common SKU: {len(common)}',f'- Extra in state: {len(extra)}',f'- Missing from state: {len(missing)}','','## Extra SKU detail','']
for sku in extra:
    r=known[sku]; cm=current.get(sku,{})
    status=r.get('last_status',''); category='VALID_OFFLINE' if status=='OFFLINE' else 'STALE_STATE'
    lines += [f'### {sku}', '', f'- Canonical_ID: {r.get("canonical_id","")}', f'- In confirmed CURRENT: {"yes" if sku in current else "no"}', f'- In known_skus: yes', f'- In offline_skus: {"yes" if sku in offline else "no"}', f'- first_seen: {r.get("first_seen_date","")}', f'- last_seen: {r.get("last_seen_date","")}', f'- last_status: {status}', f'- Classification: {category}', '- Source: runtime/state/known_skus.csv', '- Historical record: retained in known_skus; not evidence of current sale', '- Recommended action: retain unless subsequent source evidence proves duplicate/invalid; do not delete during reconciliation.', '']
lines += ['## Missing SKU detail','']
for sku in missing:
 r=current[sku]; lines += [f'- `{sku}` / `{r.get("canonical_id","")}`: NEEDS_REVIEW — in confirmed CURRENT but absent from known_skus; import/backfill only after confirming state baseline policy.']
lines += ['', '## Conclusion','', '5,537 is the yesterday CURRENT baseline. known_skus is a historical lifecycle register and may legitimately exceed CURRENT. This report does not authorize deletion or overwrite of any SKU.']
out=ROOT/'reports'/'baseline_reconciliation.md';out.parent.mkdir(exist_ok=True);out.write_text('\n'.join(lines),encoding='utf-8')
print(out, len(current_skus),len(known_skus),len(extra),len(missing))
