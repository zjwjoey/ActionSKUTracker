# Translation Queue Contract

Queue IDs are stable over SKU, six-field source hash and requested fields. Source changes create a new work item; blocked source is excluded. Retries are bounded and resumable. Provider failures produce FAILED evidence and never fail the product commit. Candidates are never formal export input.
