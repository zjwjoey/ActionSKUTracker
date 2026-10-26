# Source-bound mokoji body identity

SKU 3224847 explicitly names a `mokoji` in its Spanish description. Preserving
that name is required by the source-faithful body policy. The deterministic
residual guard previously rejected this reviewed identity as ordinary Spanish.

Reuse the existing reviewed-body-name mechanism for this complete token. It
requires the token in the field's own source, respects word boundaries, and
affects description/details only. It does not permit ordinary untranslated
prose, import a name from another field, alter name/specification display policies,
approve source conflicts, promote a dictionary term, or waive numeric checks.

The regression uses the real description, six collectible designs, and all
listed kit contents. It also checks cross-field absence, prefixes, surrounding
Spanish, short fields and an incorrect quantity.

Deployment identity is recorded separately in `runtime/deployment/active.json`.
Applying an independently reviewed field still requires its exact source version, QA,
explicit delegated Owner approval, immutable patch and the formal Apply APIs.
