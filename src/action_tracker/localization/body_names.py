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
    "Bar-le-Duc", "Bar-le-Duc Still",
    "La patrulla canina", "La casa de muñecas de Gabby",
    "The Next Sound by Tiësto", "Rich Bass", "Clear Sound",
    "Japandi", "dry-fit", "Duo Flavours", "wasabi",
    "Windows", "Mac", "Chromebooks", "Chromebook",
    "Fórmula 1", "Formula 1", "Capri-Sun",
    "L.O.L. Surprise!",
    "Play-Doh", "Sau'cee", "Nor-Tec", "TP-link", "Oral-B",
    "Pop-it", "DJ Tiësto",
    "Play-Doh Create & Celebrate",
)


def mask_source_bound_body_names(source: str, target: str) -> str:
    result = str(target or "")
    for name in REVIEWED_BODY_NAMES:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"
        if re.search(pattern, str(source or ""), re.I):
            # A shorter brand cannot hide a truncated reviewed series name.
            if any(len(longer) > len(name) and name.casefold() in longer.casefold()
                   and re.search(rf"(?<![A-Za-z0-9]){re.escape(longer)}(?![A-Za-z0-9])", str(source or ""), re.I)
                   for longer in REVIEWED_BODY_NAMES):
                continue
            result = re.sub(pattern, " ", result, flags=re.I)
    # English platform plurals may be rendered as their singular platform
    # name in Chinese. This is an own-field alias, not an arbitrary suffix.
    if re.search(r'\bChromebooks\b', str(source or ''), re.I):
        result = re.sub(r'(?<![A-Za-z0-9])Chromebook(?![A-Za-z0-9])', ' ', result, flags=re.I)
    return result
