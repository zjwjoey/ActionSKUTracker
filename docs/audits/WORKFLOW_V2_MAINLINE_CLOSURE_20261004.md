# Workflow V2 mainline closure audit — 2026-10-04

## Scope

This branch is a clean selective generic backport from
`feature/workflow-v2-auto-translate-export`. It is intended for a pull request
to `main`; it has not been merged into `main` and it does not contain the
production-only deployment profile, preflight evidence, production paths, or
deployment runbooks.

| Item | Value |
|---|---|
| Base feature snapshot | `2cac2b7e60e01d6468af21ec3eb9caafab6777ac` |
| Candidate code head | `9f238da16302bf28147a9af67327bdb7201b08dc` |
| Main used for merge simulation | `b47e4c56a0274fdddceff347e2571d77c1476014` |
| Merge simulation | `git merge-tree --write-tree origin/main HEAD` — no conflicts |
| Local full suite | `754 passed` |
| Local CI-safe allowlist | `754 passed` |
| Exact-head CI | run `37208054071`, Ubuntu and Windows successful |

## Backported generic behavior

- Explicit profile and environment fallback handling with deep merge,
  fail-closed defaults, and non-secret effective configuration evidence.
- One workflow business date and run identity propagated through extraction;
  extraction date mismatches block fact commit and resume rejects a changed
  effective configuration hash.
- Source-bound localization freshness: existing Chinese text is preserved,
  only changed fields become stale, and new SKU placeholders remain pending.
- Phase 1 state machine and Qwen translation contract tests, including export
  behavior when a legacy formal run is absent.

## Explicit exclusions

The following remain deployment-branch-only and were not backported:

- `config/workflow_v2_production_profile.yaml`
- `scripts/workflow_v2_production_preflight.py`
- production deployment/rollback runbooks and audit evidence
- production storage paths, manifests, and real provider credentials
- any change to `main`

## Decision

`READY_FOR_MAIN_MERGE = YES`, subject to the repository's normal GitHub main
branch protection and reviewer approval. No merge or production mutation was
performed by this audit.

