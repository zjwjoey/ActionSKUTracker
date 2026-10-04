# Workflow V2 migration plan

1. Run the fixture workflow with `FakeTranslationProvider` in CI.
2. Run shadow comparison against the established `run_daily()` chain for
   Presence, lifecycle, facts and price events.
3. Add a production extraction adapter that returns `ExtractionResult` and
   passes its `CommitBundle` to the existing writer.
4. Enable a local canary with a temporary database and a small SKU set.
5. Enable automatic translation and policy approval only after the canary and
   bilingual export audits pass.
6. Switch the user-facing `data-update` alias after the parallel validation
   period; keep `daily-run` and `production-run` for one compatibility cycle.

No migration step enables real Qwen, real PRIMARY apply, or final export by
default.
