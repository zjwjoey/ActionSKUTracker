# Laundry powder QA disambiguation

This local fix addresses the source-backed 3227276 name regression. Its Spanish
name explicitly says `Detergente en polvo`; the same SKU describes clothes
washing and identifies `Lavadora` as the appliance/use. The generic detergent
semantic fact previously required dishwashing-liquid terminology and rejected
the faithful Chinese name `虎百合与莲花香洗衣粉`.

QA now requires a laundry-powder name only when the requested source field
contains the complete powder-detergent phrase and product prose or typed usage
attributes explicitly establish laundry use. A broad mixed category does not
provide that evidence. Powder form cannot be borrowed from another field.

The rule stays inactive for conflicting dish/floor/carpet use, negated laundry
claims, containers/dispensers and mixed detergent forms. In the clear powder
case, liquid/capsule/dishwashing identities, negated powder claims and omitted
powder identity still fail. Numeric, model, unit and all other guards remain
in force. Existing capsule and general detergent rules are retained.

This is a deterministic QA check, not a translation, source correction, SKU
allowlist, dictionary promotion or approval bypass. Production changes still
require semantic review, delegated approval, immutable patches and Apply.

`tests/test_laundry_powder_qa.py` contains the real source and negative controls;
it is offline and CI_SAFE. Code on the development branch is not an activated
production release. The active deployment manifest is unchanged; rollout is a
separate authorized release operation.
