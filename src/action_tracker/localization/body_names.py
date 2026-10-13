"""Reviewed product/IP names retained verbatim in source-faithful body text.

Mask complete own-field source spans, never their individual ordinary words.
These are display identities, not inferred product facts or global residual
exceptions. Names/categories/specifications do not use this body contract.
"""
import re


REVIEWED_BODY_NAMES = (
    "Finall Oxi Active Power Gel Multi-Color",
    "Accelerate Energy Gel",
    "El Grinch",
)


def mask_source_bound_body_names(source: str, target: str) -> str:
    result = str(target or "")
    for name in REVIEWED_BODY_NAMES:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"
        if re.search(pattern, str(source or ""), re.I):
            result = re.sub(pattern, " ", result, flags=re.I)
    return result
