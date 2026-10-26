"""Reviewed product/IP names retained verbatim in source-faithful body text.

Mask complete own-field source spans, never their individual ordinary words.
These are display identities, not inferred product facts or global residual
exceptions. Names/categories/specifications do not use this body contract.
"""
import re


REVIEWED_BODY_NAMES = (
    "Play-Doh Create & Celebrate",
    # Complete own-field identities reviewed in the export-brand25 audit.
    # Punctuation is part of the identity; never allow individual fragments.
    "Gen-Zen Pore Refining",
    "L.O.L. Surprise!",
    "Play-Doh",
    "Sau'cee",
    "Oral-B",
    "A-Force",
    "Nor-Tec",
    "TP-link",
    "Gen-Zen",
    "Pom-Bär",
    "Coca-Cola",
    "Fisher-Price",
    "Fanta",
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
    "Mickey Mouse",
    "Sonic",
    "Kuromi",
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
        # A known longer series in the source must not be downgraded to its
        # short brand merely to silence an incomplete-series residual.
        if name == "Play-Doh" and re.search(
            rf"(?<![{boundary}])Play-Doh Create & Celebrate(?![{boundary}])",
            str(source or ""), re.I,
        ):
            continue
        # Ángel also means an ordinary angel. Only a reviewed own-field
        # character context establishes this as an IP name.
        if name == "Ángel":
            # Reviewed own-field character enumerations also disambiguate the
            # name. Mere Disney presence or a generic angel remains insufficient.
            contexts = (
                r"Stitch\s+y\s+Ángel\s+de\s+Disney",
                r"Para elegir entre Mickey Mouse,\s*Stitch,\s*Ángel\s+Silueta de tu personaje favorito de Disney",
                r"Puedes elegir entre Ángel,\s*Sonic y Kuromi",
            )
            if not any(re.search(rf"(?<![{boundary}]){context}(?![{boundary}])",
                                 str(source or ""), re.I) for context in contexts):
                continue
        pattern = rf"(?<![{boundary}]){re.escape(name)}(?![{boundary}])"
        if re.search(pattern, str(source or ""), re.I):
            result = re.sub(pattern, " ", result, flags=re.I)
    return result
