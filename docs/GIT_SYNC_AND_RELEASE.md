# Git synchronization and production release

## Branch roles

- `main` contains reviewed stable code.
- `feature/*`, `feat/*` and `fix/*` contain development work.
- `experiment/*` contains experiments; merging unrelated experiments is not a synchronization step.
- `release/*` or `deploy/*` identifies an accepted production candidate.
- The running Production checkout is identified by `runtime/deployment/active.json`, including source checkout, branch, code SHA and data root.

The committed SHA and the working tree are separate identities. An unchanged
SHA does not prove that a dirty production checkout matches the deployed release.

## Before development

1. Fetch `origin --prune`, then inspect branch, HEAD, tracking branch, status and worktrees.
2. Compare with both the matching remote branch and `origin/main`. Being ahead of main does not imply that commits are unpushed.
3. Identify each directory's Git common directory before treating it as another worktree of the same repository.
4. Preserve existing tracked differences, staged changes and untracked files. Record paths and SHA-256; verify recoverability.
5. Work from an isolated branch and checkout. Do not modify a running production checkout.

## Saving and integrating work

- Stage explicit reviewed paths; never sweep runtime, databases, browser profiles, cookies, keys, exports or unknown artifacts into a commit.
- Keep unfinished changes on independent branches with their acceptance limitations documented.
- Keep unpublished dictionary and training artifacts in verified local archives. No training run or dictionary release is implied by a code synchronization task.
- Push development branches and verify the remote HEAD. Do not force-push shared history.
- Integrate from current `origin/main`, identify duplicate implementations and resolve conflicts by behavior and regression evidence.
- Add regression coverage for interactions between fixes, including detail retries without Listing data, original-price absence, and technical-token boundaries.

## Merge gates

The candidate must have a clean committed working tree, static review, full
pytest, CI_SAFE, exact-head Ubuntu and Windows CI, and isolated compatibility
evidence against a recoverable production database copy. Tests must not write
the live PRIMARY, Master or runtime state. Respect GitHub's protected main and
required checks; never use an administrative bypass for a failing gate.

Create a normal PR, merge only after required gates pass, fetch again and verify
the merged main SHA. Record audited heads and publication metadata separately;
reports must not require their own final content to contain their current SHA.

## Production is a separate operation

A merged PR does not update Production. Prepare a distinct candidate directory,
verify dependencies and Playwright availability, replay daily/Resume/export paths
on copies, retain effective production fixes and validate rollback evidence.
Source-quality or translation-data blockers remain separate from code compatibility.

Do not overwrite or switch the production checkout or active deployment manifest
without deployment authorization. For an authorized deployment, wait for active
collection/Apply/Master Sync to finish, protect live state with validated backups,
activate one explicit candidate identity and perform production verification.

## 2026-10-13 synchronization boundary

The audited remote main at task start was
`08fcce5e8e4070b3d0340720308b26a7441920d4`. The active production manifest still
identified `b09bcea89e35e6a69367a6542ff871bab5ba1921`; its local changes were
preserved independently rather than overwritten. This paragraph is a task-start
snapshot, not a claim about the eventual merged head or current deployment.

Detailed inventories, local backup verification, integration results and final
publication evidence are recorded under
`runtime/reports/repository_sync_closure_20261013/` and are excluded from Git.
