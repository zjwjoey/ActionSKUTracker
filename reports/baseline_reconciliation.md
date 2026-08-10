# Baseline Reconciliation

- CURRENT_5537 source: `F:\Action_Master\Action_Master.xlsx`
- CURRENT count: 5537
- STATE known_skus count: 5541
- Common SKU: 5537
- Extra in state: 4
- Missing from state: 0

## Extra SKU detail

### 3006466

- Canonical_ID: ACT3006466
- In confirmed CURRENT: no
- In known_skus: yes
- In offline_skus: no
- first_seen: 2026-08-10
- last_seen: 2026-08-10
- last_status: ACTIVE
- Classification: STALE_STATE
- Source: runtime/state/known_skus.csv
- Historical record: retained in known_skus; not evidence of current sale
- Recommended action: retain unless subsequent source evidence proves duplicate/invalid; do not delete during reconciliation.

### 3205863

- Canonical_ID: ACT3205863
- In confirmed CURRENT: no
- In known_skus: yes
- In offline_skus: no
- first_seen: 2026-08-10
- last_seen: 2026-08-10
- last_status: ACTIVE
- Classification: STALE_STATE
- Source: runtime/state/known_skus.csv
- Historical record: retained in known_skus; not evidence of current sale
- Recommended action: retain unless subsequent source evidence proves duplicate/invalid; do not delete during reconciliation.

### 3217856

- Canonical_ID: ACT3217856
- In confirmed CURRENT: no
- In known_skus: yes
- In offline_skus: no
- first_seen: 2026-08-10
- last_seen: 2026-08-10
- last_status: ACTIVE
- Classification: STALE_STATE
- Source: runtime/state/known_skus.csv
- Historical record: retained in known_skus; not evidence of current sale
- Recommended action: retain unless subsequent source evidence proves duplicate/invalid; do not delete during reconciliation.

### 3217861

- Canonical_ID: ACT3217861
- In confirmed CURRENT: no
- In known_skus: yes
- In offline_skus: no
- first_seen: 2026-08-10
- last_seen: 2026-08-10
- last_status: ACTIVE
- Classification: STALE_STATE
- Source: runtime/state/known_skus.csv
- Historical record: retained in known_skus; not evidence of current sale
- Recommended action: retain unless subsequent source evidence proves duplicate/invalid; do not delete during reconciliation.

## Missing SKU detail


## Conclusion

5,537 is the yesterday CURRENT baseline. known_skus is a historical lifecycle register and may legitimately exceed CURRENT. This report does not authorize deletion or overwrite of any SKU.