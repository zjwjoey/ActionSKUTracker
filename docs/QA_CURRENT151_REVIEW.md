# Source-bound localization QA corrections

These checks preserve facts and flag uncertainty; they do not translate,
approve a field, promote dictionary entries or bypass Apply.

- Recognize a standalone diameter symbol next to a numeric measurement without
  interpreting it as part of a Latin model. Missing or changed units still fail.
- Compare selected charging speed against speed attributes, not charger type
  or charging current. Missing, reversed and duplicate speeds remain blocked.
- Recognize both Perfumado and Sin perfume and preserve their separate polarity.
- Count explicit Spanish shoe-hole alternatives with o/u; unsupported compound
  numerals must not be reduced to a smaller last number.
- Recover only known official flattened detail labels lacking a colon. Preserve
  their original fragments, order and duplicate entries. Do not infer unknown
  keys, missing values or damaged boolean meanings.
- Retain reviewed complete body identities only when present in that body's
  own source. Latin Unicode word boundaries exclude partial identities. Ángel
  requires the explicit same-field Stitch y Ángel de Disney character phrase;
  ordinary angels and adjacent Latin letters are not exempt.

The no-brand name policy, source hashes, numeric/model/negation checks,
source-conflict handling, delegated approval and immutable Apply are unchanged.

Offline positive and negative regressions are listed in tests/ci_safe_tests.txt.
Production activation is separately audited and recorded by active.json. A clean
code release does not resolve outstanding source-data or publication blockers.
