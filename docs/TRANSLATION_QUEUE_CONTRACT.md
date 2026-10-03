# Translation Queue Contract

Queue IDs are stable over SKU, six-field source hash and requested fields. Source changes create a new work item; blocked source is excluded. Queue states are `PENDING`, `CLAIMED`, `RETRY`, `COMPLETED`, `FAILED` and `BLOCKED`.

Retries are bounded and resumable. Only network/timeout/HTTP 429/5xx and
transient SQLite I/O are retryable. HTTP 400/401/403, malformed/empty or
unexpected-language responses are terminal `FAILED`; protected-token,
source/data and deterministic QA blockers are terminal `BLOCKED`; unknown
program errors are `FAILED`. Terminal errors are never silently re-enqueued.

Every actual provider attempt writes a success or failure row to
`translation_provider_calls`. Successful revisions retain the linked
`provider_call_id` and structured request/response provenance. Provider
failures never fail the product commit. Candidates are never formal export
input, and `COMPLETED` means only that a revision was produced—not that it
was approved or applied to PRIMARY.
