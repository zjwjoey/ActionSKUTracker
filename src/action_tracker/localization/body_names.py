"""Reviewed product/IP names retained verbatim in source-faithful body text.

Mask complete own-field source spans, never their individual ordinary words.
These are display identities, not inferred product facts or global residual
exceptions. Names/categories/specifications do not use this body contract.
"""
import re


REVIEWED_BODY_NAMES = (
    "La Maison Rêve",
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
    # Complete same-field identities/printed wording reviewed against the
    # frozen catalog. Their ordinary components are never standalone tokens.
    "Sonic the Hedgehog",
    "Zuru X-Shot Skins",
    "Tesi Bon Hot Spiced Curlz",
    "Remington Pro-Ceramic Extra",
    "La casa de muñecas de Gabby",
    "El Rey León",
    "Dr. Pepper",
    "Match-It",
    "i-Touch",
    "Collect memories",
    "LR14 / baby",
)


def mask_source_bound_body_names(source: str, target: str) -> str:
    result = str(target or "")
    boundary = r"A-Za-z0-9\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
    for name in sorted(REVIEWED_BODY_NAMES, key=len, reverse=True):
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
                r"Para elegir entre Stitch o Ángel",
                r"Para elegir entre Pokémon, Stitch & Ángel, Spidey y Bluey, entre otros",
            )
            if not any(re.search(rf"(?<![{boundary}]){context}(?![{boundary}])",
                                 str(source or ""), re.I) for context in contexts):
                continue
        pattern = rf"(?<![{boundary}]){re.escape(name)}(?![{boundary}])"
        if re.search(pattern, str(source or ""), re.I):
            result = re.sub(pattern, " ", result, flags=re.I)
    # Source grammar uses plural product-family names while idiomatic Chinese
    # keeps the singular family identifier. Bind each finite alias to this
    # very field, never to another field or to arbitrary English suffixes.
    for plural, singular in (("iPhones", "iPhone"), ("iPads", "iPad")):
        if re.search(rf"(?<![{boundary}]){plural}(?![{boundary}])", str(source or ""), re.I):
            result = re.sub(rf"(?<![{boundary}]){singular}(?![{boundary}])", " ", result, flags=re.I)
    # A colon-delimited iPhone model enumeration supplies the family once.
    # Recover only complete numeric/model spans; do not allow the word Pro
    # globally or exempt ordinary prose following a model list.
    for match in re.finditer(r"\biPhone:\s*([^.;\n]+)", str(source or ""), re.I):
        model = r"\d+(?:\s+pro(?:\s+max)?)?"
        if not re.fullmatch(rf"{model}(?:(?:\s*,\s*|\s+y\s+){model})+", match.group(1), re.I):
            continue
        for name in sorted(re.findall(model, match.group(1), re.I), key=len, reverse=True):
            pattern = rf"(?<![{boundary}])iPhone\s+{re.escape(name)}(?![{boundary}])"
            result = re.sub(pattern, " ", result, flags=re.I)
    return result
