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
    "mokoji",
    # Individually reviewed commercial/IP spans in the current own-field
    # source. Longer names precede their components; ordinary prose is never
    # exempted merely because it occurs beside one of these identities.
    "Bar-le-Duc Still",
    "Bar-le-Duc",
    "Capri-Sun",
    "La Patrulla Canina",
    "Hello Kitty",
    "Minnie Mouse",
    "Stitch",
    "Ángel",
    "Disney",
    "Spidey",
    "Pop-it",
)


def mask_source_bound_body_names(source: str, target: str) -> str:
    result = str(target or "")
    boundary = r"A-Za-z0-9\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
    for name in REVIEWED_BODY_NAMES:
        # Ángel also means an ordinary angel. Only the explicit own-field
        # Disney character phrase establishes this as the reviewed IP name.
        if name == "Ángel" and not re.search(
            rf"(?<![{boundary}])Stitch\s+y\s+Ángel\s+de\s+Disney(?![{boundary}])",
            str(source or ""), re.I,
        ):
            continue
        pattern = rf"(?<![{boundary}]){re.escape(name)}(?![{boundary}])"
        if re.search(pattern, str(source or ""), re.I):
            result = re.sub(pattern, " ", result, flags=re.I)
    return result
