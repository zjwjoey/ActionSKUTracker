# Architecture V2 Acceptance Record

This document is updated after the feature branch audit. Acceptance requires:
SQLite integrity/FK and lifecycle parity PASS; deterministic extraction,
selection semantics and artifact SKU exactness; full regression and exact-head
CI PASS; localhost-only Workspace; and no HIGH/MEDIUM findings.

Scheduler observation is operational evidence, not a reason to alter product
facts. The final report must state real database counts, query samples and
artifact hashes rather than relying on historical SHA values.
