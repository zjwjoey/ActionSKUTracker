# Source-faithful body export QA

This change is local on `fix/export60-source-bound-qa-20261026`; it is not a
production activation or a release-gate exception.

The no-brand display policy applies to titles. Export formatting must retain
existing identities in descriptions/details, rather than deleting dictionary
brand tokens and truncating names such as Capri-Sun or Finall Oxi Active Power
Gel Multi-Color. It does not import missing brands from other fields.

The residual detector masks only complete, reviewed commercial/IP spans that
occur in the field's own Spanish source, with Latin word boundaries. Reviewed
punctuated identities include Play-Doh, Sau'cee, Oral-B, A-Force, Nor-Tec,
TP-link, Gen-Zen Pore Refining, Pom-Bär, Coca-Cola, Fisher-Price and
L.O.L. Surprise!. Components such as `cee`, `Tec` or `Surprise` are not global
allowlist entries. Explicit own-field character enumerations disambiguate
Ángel; ordinary angel attributes and cross-field context do not qualify.

`是否香薰` and `是否香氛` are accepted perfume Boolean keys. Negation, polarity,
missing-key and separate `Sin perfume` checks remain in force.

Tests cover complete identities, punctuation, Unicode boundaries, absent
own-field sources, numeric loss, ordinary Spanish beside a brand, and perfume
polarity. Passing these checks is not semantic approval of the rest of a field.
Source anomalies, STALE and unresolved release checks still block release.

Local live-data evidence is kept outside Git in the registered tasks
`export60-stale-review` and `export-brand25-review`. The latter reviews 25
new residual blockers exposed by preserving brands: 16 existing values are
semantically reusable; 8 have separately reviewed minimal corrections. One
additional existing value remains unchanged with a source-conflict note:
3209451 has three bracelets in description/specification but four in details.
This conflict is not resolved by accepting its source-bound brand spelling. Apply
counts and verification outcomes come from the task manifests, not this file.
No runtime dictionary, schema, collection behavior or release exceptions change.
