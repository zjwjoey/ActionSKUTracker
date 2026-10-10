# Localization quality experiment V1

The foundation was merged through PR #8 at main `08fcce5e8e4070b3d0340720308b26a7441920d4`.
Both required operating-system CI jobs passed before and after merge. Local full
tests and CI_SAFE each passed 1,307 tests; 368 isolated real source comparisons
had zero default candidate/context differences. Three source-bound punctuation
patches were applied only to a production copy; Resume applied zero patches,
and all eight protected fact/lifecycle tables retained logical parity.

Experiments use branch `experiment/localization-quality-validation-v1`. No
experimental strategy is deployed, merged into main, or enabled in production.
The source snapshot, dictionary snapshot and disjoint cohorts are immutable
runtime evidence outside Git. Cohorts cover 200/500/1,000 distinct SKUs, equally
split between CURRENT and historical, with category and field-state strata.
Development/holdout splits are 150/50, 350/150 and 0/1,000. The final holdout
requires a frozen candidate commit before evaluating it. Previously examined
phase-one SKUs and historical regression fixtures were excluded.

Only fields with exact independently supported source versions are in the
semantic quality denominator. Retained QA-accepted official snapshots are valid
corroboration even when the PRIMARY fact is marked normalized-only. Missing,
conflicted and uncertain sources are refusal tests, never translation successes.
An unavailable source is not proof of an official empty value.

`scripts/run_localization_quality_experiment.py` is LOCAL_ONLY orchestration of
the existing resolver, native finite provider checkpoint and factual/canonical
QA. It checks existing Chinese or generates missing candidates using the existing
Qwen adapter. Full response/usage sidecars are retained before checkpoint saving;
Resume binds source, dictionary, code and adapter identities. Correct Chinese is
retained. There are no Registry writes, approvals, PRIMARY Apply, Master writes
or dictionary publications. Candidate QA PASS remains pending independent Codex
semantic review. Unknown correctness remains unverified.

Example (explicit isolated paths are required):

```powershell
$env:PYTHONPATH='src'
python scripts/run_localization_quality_experiment.py --manifest <frozen_manifest> --round 1 --split development --output <isolated_output> --allow-provider
```

Reviews must retain original source, candidate, current target, proof, hashes,
QA, field decision, issue type, severity and source-supported correction.
Report actual provider calls/tokens separately from review calls, cache reuse,
failures and confirmed semantic accuracy. Never substitute corrected outputs
for first outputs or mechanically assign semantic KEEP. Module changes require
full regressions. Holdout discoveries remain validation failures and cannot be
tuned away while claiming the same holdout is unseen.

Final delivery requires all three reviewed rounds and a separate release
recommendation. Passing CI or merging the foundation does not complete them.
