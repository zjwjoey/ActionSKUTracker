from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import SourceFacts
from .policy import FIXED_CAT1, has_ordinary_spanish
from .protection.tokens import ProtectedTokenError, protect_text, restore_text
from .normalization.structured_details import parse_structured_details


FACT_QA_POLICY_VERSION = "FACT_QA_V2"
EMPTY_SOURCE_LOCALIZATION_CONTRACT_VERSION = "EMPTY_SOURCE_LOCALIZATION_CONTRACT_V1"
NAME_IDENTITY_FACT_PRESERVATION_VERSION = "NAME_IDENTITY_FACT_PRESERVATION_V1"


_STRICT_UNIT_RE = re.compile(r"(?<![A-Za-z0-9\u00c0-\u024f])\d+(?:[.,]\d+)?\s*(km²|cm²|mm²|m²|mAh|Ah|Wh|kWh|mW|kW|Hz|V|W|dB|kcal|°C|℃|cm|mm|km|m|kg|g|mg|mcg|μg|ml|cl|dl|l|L|%)(?![A-Za-z0-9\u00c0-\u024f])", re.I)
_CHINESE_UNIT_RE = re.compile(r"(?<![0-9])\d+(?:[.,]\d+)?\s*(毫安时|安时|瓦时|千瓦时|毫瓦|千瓦|赫兹|伏特|瓦|分贝|千卡|千卡路里|摄氏度|平方千米|平方米|平方厘米|平方毫米|厘米|毫米|千米|米|千克|公斤|克|毫克|微克|毫升|厘升|分升|升|百分比)(?![0-9])")
_UNIT_ALIASES = {
    "毫安时": "mah", "安时": "ah", "瓦时": "wh", "千瓦时": "kwh", "毫瓦": "mw", "千瓦": "kw",
    "赫兹": "hz", "伏特": "v", "瓦": "w", "分贝": "db", "千卡": "kcal", "千卡路里": "kcal", "摄氏度": "°c", "厘米": "cm", "毫米": "mm",
    "千米": "km", "米": "m", "千克": "kg", "公斤": "kg", "克": "g", "毫克": "mg", "微克": "μg",
    "毫升": "ml", "厘升": "cl", "分升": "dl", "升": "l", "百分比": "%",
    "平方米": "m²", "平方厘米": "cm²", "平方千米": "km²", "平方毫米": "mm²",
}


def _units(value: str) -> set[str]:
    units = {_normalize_unit(unit) for unit in _STRICT_UNIT_RE.findall(value)}
    units.update(_UNIT_ALIASES.get(unit, unit) for unit in _CHINESE_UNIT_RE.findall(value))
    return units
_STRICT_TOKEN_TYPES = {"URL", "SKU", "EAN", "MODEL", "TECH", "CERTIFICATION", "CAPACITY", "BATTERY_CAPACITY"}

# Deterministic semantic facts use one canonical target, while natural
# Chinese allows a small set of equivalent renderings.  These aliases are
# intentionally conservative and only cover terms already present in the
# seeded semantic map; they prevent the Guard from rejecting valid outputs
# such as ``paño -> 抹布`` and ``madera -> 木制``.
_SEMANTIC_TARGET_ALIASES = {
    "marcadores grandes": ("记号笔", "马克笔"),
    "marcadores acrílicos": ("丙烯马克笔", "丙烯记号笔"),
    "marcadores dobles de pizarra blanca": ("白板笔", "白板记号笔"),
    "marcadores de punta fina y pincel": ("记号笔", "马克笔"),
    "brocha para polvos": ("散粉刷", "蜜粉刷", "定妆粉刷"),
    "recortacejas": ("修眉器", "眉毛修剪器"),
    "ampollas de aceite": ("安瓶", "安瓿"),
    "gomas": ("橡皮筋", "橡胶圈", "松紧带"),
    "goma": ("橡胶",),
    "calcetines": ("袜子", "短袜", "长袜", "低帮袜", "运动袜"),
    "detergente": ("洗洁精", "洗涤剂", "清洁剂", "马桶清洁剂"),
    "cartulina": ("彩色手工卡纸", "卡纸", "手工卡纸"),
    "paño": ("清洁布", "抹布", "擦布", "湿布"),
    "paños": ("清洁布", "抹布", "擦布", "湿布"),
    # ``microfibra`` is rendered in the existing catalog as either
    # ``超细纤维`` or the shorter ``微纤维``; both preserve the material fact.
    "microfibra": ("超细纤维", "微纤维"),
    "microfibras": ("超细纤维", "微纤维"),
    "madera": ("木质", "木材", "木制", "木头", "木盖", "木屑", "芒果木"),
    "bambú": ("竹制", "竹材", "竹子", "竹签", "竹筷"),
}

# A small, explicit allowlist for semantic translations that are rendered as
# uppercase tokens in Chinese.  ``bricolaje`` is routinely standardized as
# ``DIY``; the protection scanner classifies ``DIY`` as a TECH token, even
# though it is not an invented product model.  Keep this exception narrow and
# source-bound instead of weakening the protected-token guard globally.
_TRANSLATED_STRICT_TOKEN_ALLOWLIST = {
    "bricolaje": {"DIY"},
    # Real historical specs 3221778/3221803 spell the LED plural ``ledes``.
    # This equivalence is permitted only in this field's own source text.
    "ledes": {"LED"},
    "LEDs": {"LED"},
    "USB C": {"USB-C"},
    "IA": {"AI"},
}

# Some short technical acronyms are official source terminology rather than
# model identifiers.  They may be translated when the source-bound meaning is
# explicit; otherwise the protected-token guard would mistake a correct
# translation such as ``GLP -> 液化石油气`` for a dropped token.
_TRANSLATED_TECH_TOKEN_ALIASES = {
    "ia": ("AI", "人工智能"),
    "glp": ("液化石油气",),
    "lpg": ("液化石油气",),
    # Source technical abbreviations that are legitimately localized in the
    # Chinese display. These are source-bound equivalences, not a global
    # waiver for arbitrary Latin tokens.
    "ph": ("pH", "pH值", "酸碱度"),
    "hd": ("高清", "全高清"),
    "tv": ("电视", "电视机"),
    "wc": ("马桶", "卫生间", "厕所"),
    "gsm": ("克/平方米", "克/㎡", "克每平方米", "克重"),
    "bpa": ("双酚A", "双酚 a"),
    # The protected-token scanner intentionally works on ASCII.  Spanish
    # all-caps labels containing accents can consequently expose a complete
    # word (PUFF/SET/LUX/CLAVIJA) as a TECH token.  These are source-bound
    # lexical translations, never identifiers that must be copied verbatim.
    "puff": ("蒲团", "软凳", "坐垫"),
    "clavija": ("插头", "插孔"),
    "set": ("套装",),
    "lux": ("勒克斯",),
}

# Certain uppercase spans are brands rather than product identifiers. The
# Chinese display contract omits them, so their absence is not a protected
# token failure when the source-bound brand is HP.
_OMITTABLE_DISPLAY_BRAND_TECH = {"hp"}

# Source-bound display tokens which may legitimately remain in Chinese copy.
# The static allowlist above covers common abbreviations, but Action source
# facts also contain product brands, mixed-case interfaces and model-like
# spans (for example ``Alison & Mae``, ``PlayStation`` and ``daN``).  These
# must be allowed only when the exact token is present in both source and
# target; ordinary Spanish words remain blocked.
_SOURCE_BOUND_SPANISH_STOPWORDS = {
    "a", "al", "como", "con", "de", "del", "desde", "el", "en", "entre",
    "esta", "este", "la", "las", "lo", "los", "más", "no", "o", "para",
    "por", "que", "se", "sin", "su", "sus", "un", "una", "y",
    "color", "colores", "material", "incluye", "número", "numero",
    "cantidad", "contenido", "tipo", "tamaño", "tamano", "varios", "varias",
    "diferentes", "negro", "blanco", "rojo", "azul", "verde", "unidades",
}
_SOURCE_BOUND_SHORT_TECH = {"mbps", "gbps", "kbps", "mhz", "khz", "ghz", "hfe", "mah", "kwh", "wh", "mah"}
_SOURCE_BOUND_EXACT_TECH = {
    "usb", "usb-a", "usb-c", "micro-usb", "micro-sd", "hdmi", "led", "mdf",
    "fsc", "bci", "tcx", "a4", "b5", "wifi", "magsafe", "playstation", "k-pop", "power-fast",
    "sds-plus", "transflash", "eprel", "torx",
    # Exact same-field commercial/game spans; the residual scanner splits
    # hyphens into words, so their full source-bound spelling is required.
    "re-load", "skip-bo", "uno-flip", "pro-max", "t-rex", "gsm", "jawbreaker", "i-scrub", "olus",
}


def _source_bound_display_tokens(source_text: str, target: str) -> set[str]:
    """Return conservative source-bound brand/technical spans.

    A token is accepted only when it is copied from the same official source
    field into the target and has a model/brand shape.  Sentence-leading
    capitalisation alone is not sufficient, which prevents ``Para`` or
    ``Material`` from becoming a false allowlist entry.
    """
    source = str(source_text or "")
    rendered = str(target or "")
    if not source or not rendered:
        return set()
    target_fold = rendered.casefold()
    allowed: set[str] = set()
    for raw_token in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ&+./-]*", source):
        # A terminal full stop belongs to prose, rather than a model or
        # display token (for example ``So Slime.``).  Preserve internal dots
        # used by versioned model identifiers such as ``2.0``.
        token = raw_token.rstrip(".")
        if not token:
            continue
        if token.casefold() not in target_fold:
            continue
        folded = token.casefold()
        if folded in _SOURCE_BOUND_SPANISH_STOPWORDS:
            continue
        target_match = re.search(
            rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
            rendered,
            flags=re.IGNORECASE,
        )
        model_shape = (
            any(char.isdigit() for char in token)
            or ("-" not in token and any(char.isupper() for char in token[1:]) and any(char.islower() for char in token))
            or (
                folded in _SOURCE_BOUND_SHORT_TECH
                and target_match is not None
                and any(char.isupper() for char in target_match.group(0))
            )
            or folded in _SOURCE_BOUND_EXACT_TECH
        )
        if model_shape:
            allowed.add(token)
            # Compound interface/technology tokens such as
            # ``Micro-SD/TransFlash`` are tokenized into slash-separated
            # pieces by the residual detector. Keep those pieces source-bound
            # as well, without broadening the allowlist globally.
            separator_pattern = r"[\s/\-]+" if folded in _SOURCE_BOUND_EXACT_TECH else r"[\s/]+"
            allowed.update(piece for piece in re.split(separator_pattern, token) if len(piece) > 1)
            allowed.update(piece for piece in re.split(r"[-/+]|(?<=\D)(?=\d)|(?<=\d)(?=\D)", token) if len(piece) > 1)
            continue
        # A title-cased token is a possible brand only when it is not the
        # first word after sentence punctuation.  This keeps ordinary Spanish
        # sentence starts fail-closed while allowing ``de Alison & Mae``.
        if token[:1].isupper():
            for match in re.finditer(re.escape(token), source):
                before = source[:match.start()].rstrip()
                if before and before[-1] not in ".!?\n":
                    allowed.add(token)
                    break
    # Action renders the same interface both as ``micro USB`` and
    # ``Micro-USB``.  Permit the individual words only when that complete,
    # source-bound interface appears in both values; generic ``micro`` is
    # still ordinary Spanish and remains audited everywhere else.
    if re.search(r"(?<![A-Za-z0-9])micro[-\s]?usb(?![A-Za-z0-9])", source, flags=re.I) and re.search(r"(?<![A-Za-z0-9])micro[-\s]?usb(?![A-Za-z0-9])", rendered, flags=re.I):
        allowed.update({"micro", "usb"})
    if re.search(r"(?<![A-Za-z0-9])micro[-\s]?sd/trans[-\s]?flash(?![A-Za-z0-9])", source, flags=re.I) and re.search(r"(?<![A-Za-z0-9])micro[-\s]?sd/trans[-\s]?flash(?![A-Za-z0-9])", rendered, flags=re.I):
        allowed.update({"micro", "sd", "trans", "flash", "transflash"})
    for token in _SOURCE_BOUND_SHORT_TECH:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", source, flags=re.I) and re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", rendered, flags=re.I):
            allowed.add(token)
    # A source-bound web address is a factual reference, not Spanish prose.
    # Permit only its domain labels, and only when the same domain is present
    # in the rendered field.
    for domain in re.findall(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", source, flags=re.I):
        if domain.casefold() in target_fold:
            allowed.update(part for part in domain.split(".") if part)
            if "www." + domain.casefold() in target_fold:
                allowed.add("www")
    # Multiword commercial spans must occur complete in this same field.
    # Interior prose words (of/the/mini) are not general residual exceptions.
    for phrase in ("Snacks of the World", "Stretcherz Stretch Squad mini", "Play-Doh Create & Celebrate"):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])"
        if re.search(pattern, source, re.I) and re.search(pattern, rendered, re.I):
            allowed.update(re.findall(r"[A-Za-z]+", phrase))
    return allowed


def _source_bound_cross_field_tokens(name_source: str, target: str) -> set[str]:
    """Allow only brand/model-shaped spans repeated from the product name."""
    source = str(name_source or "")
    rendered = str(target or "")
    if not source or not rendered:
        return set()
    allowed: set[str] = set()
    # Multiword brands such as ``Alison & Mae`` are kept as one source-bound
    # display span; do not generalise this to arbitrary title-cased words.
    for phrase in re.findall(
        r"(?<![A-Za-z0-9])([A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9-]*(?:\s*&\s*[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9-]*)+)",
        source,
    ):
        if phrase.casefold() in rendered.casefold():
            allowed.update(piece for piece in re.split(r"\s*&\s*", phrase) if piece)
    for token in _source_bound_display_tokens(source, rendered):
        if (
            any(char.isdigit() for char in token)
            or any(char in token for char in "-&+./")
            or (any(char.isupper() for char in token[1:]) and any(char.islower() for char in token))
        ):
            allowed.add(token)
    return allowed

# The protection tokenizer intentionally remains conservative and recognizes
# uppercase spans as TECH. Spanish source exports also contain uppercase field
# values (not identifiers), e.g. ``CALCULADORA`` or ``TABURETE``. Keep this
# exemption explicit and source-bound so real model, certification, and
# interface tokens remain fail-closed.
_ORDINARY_SPANISH_UPPERCASE_WORDS = {
    "BA", "BAÑO", "CALENTADOR", "CALCULADORA", "CHAQUETAS", "CÁMPING",
    "DE", "EL", "LA", "LE", "EDRED", "MANOS", "MICO", "MPING", "NO", "PERCHERO", "QU", "QUÍMICO",
    "SOMBREROS", "TABURETE", "APARATO", "CALEFACCI", "PERSONAL",
}

_ORDINARY_SPANISH_UPPERCASE_FRAGMENTS = {
    # Accented Spanish words are split by the conservative Latin-token regex
    # around the accented character (BAÑO -> BA, CÁMPING -> MPING, ...).
    "BA": r"\bbaño\b",
    "MPING": r"cámping\b",
    "QU": r"\bquímico\b",
    "MICO": r"químico\b",
    "EDRED": r"\bedred(?:ón|on)\b",
}

# Latin spans that are allowed to remain in Chinese display only when the
# same source fact contains the token. Brands are deliberately absent: the
# display policy removes brands/IP, while series/certifications/technology
# remain useful product identifiers.
_ALLOWED_LATIN_DISPLAY_TOKENS = {
    "dura-beam", "dura beam", "bloxx", "style", "choco", "trio",
    "do & dry", "essentials", "power activ", "nex",
    "fsc", "bci", "pefc", "tüv", "hdmi", "usb", "usb-c", "xl", "xxl",
    "omega", "dvd", "ph", "pH", "polo",
}

_SOURCE_FIELD_BY_TARGET = {
    "name": "name_es",
    "cat1": "cat1_es",
    "cat2": "cat2_es",
    "spec": "spec_es",
    "description": "desc_es",
    "details": "details_es",
}

# Semantic facts are checked in their source-field scope by default.  A
# future planner may add a reviewed, explicit cross-field relocation contract;
# until such a contract exists, a generic ``placement`` value is not enough
# to make (for example) a details material fact required in the name.
_EXPLICIT_CROSS_FIELD_RELOCATIONS: frozenset[tuple[str, str, str]] = frozenset()


def _source_term_present(source_text: str, source_term: str) -> bool:
    """Match a semantic term as a word/phrase, never as a substring.

    This keeps ``goma`` (material: rubber) from matching ``gomas``
    (product type: elastic bands), while retaining accents and multi-word
    phrases used by the Spanish source facts.
    """
    if not source_text or not source_term:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(source_term.strip())}(?!\w)", source_text, flags=re.IGNORECASE))


def _fact_applies_to_field(fact: Any, field_name: str) -> bool:
    source_field = str(getattr(fact, "source_field", "") or "")
    target_source_field = _SOURCE_FIELD_BY_TARGET.get(field_name, "")
    if source_field == target_source_field:
        return True
    placement = str(getattr(fact, "placement", "") or "")
    semantic_type = str(getattr(fact, "semantic_type", "") or "")
    return (semantic_type, source_field, placement) in _EXPLICIT_CROSS_FIELD_RELOCATIONS


def _omittable_display_term(term: Mapping[str, Any], semantic_facts: tuple[Any, ...], field_name: str) -> bool:
    """Return whether a reviewed term is deliberately absent from display text.

    The Chinese projection has a no-brand/IP display contract.  A selected
    terminology hint for one of those source spans remains useful to the MT
    provider, but it must not subsequently turn the policy-required removal
    into a ``TERMINOLOGY_VIOLATION``.  Restrict the waiver to an explicit
    resolver marker or a field-scoped BRAND/IP semantic fact; ordinary terms
    retain the normal terminology gate.
    """
    if bool(term.get("display_omittable")):
        return True
    source_term = str(term.get("source") or term.get("source_term") or "").strip().casefold()
    if not source_term:
        return False
    return any(
        str(getattr(fact, "semantic_type", "") or "") in {"BRAND", "IP_CHARACTER"}
        and _fact_applies_to_field(fact, field_name)
        and str(getattr(fact, "source_text", "") or "").strip().casefold() == source_term
        for fact in semantic_facts
    )


def _is_allowed_translated_strict_token(source_text: str, token: str, target: str = "", actual_count: int = 1) -> bool:
    token_key = str(token or "").casefold()
    # The Chinese lexical rendering of karaoke contains the uppercase
    # letters OK. It is not a new model identifier. Require the complete
    # phrase and account for every OK token; standalone/extra OK stays blocked.
    if token_key == "ok":
        return (_source_term_present(source_text, "karaoke") and
                len(re.findall(r"卡拉\s*OK(?![A-Za-z0-9])", target, re.I)) == actual_count)
    return any(
        token_key in {candidate.casefold() for candidate in targets}
        and _source_term_present(source_text, source_term)
        for source_term, targets in _TRANSLATED_STRICT_TOKEN_ALLOWLIST.items()
    )


def _is_allowed_translated_tech_token(source_text: str, source_token: str, target: str) -> bool:
    if str(source_token or "").casefold() == "uv":
        # Generic UV may be localized, but UVA/UVB/UVC/model suffixes may not.
        pattern = r"(?<![A-Za-z0-9_-])UV(?![A-Za-z0-9_-])"
        expected = len(re.findall(pattern, source_text, re.I))
        normalized = re.sub(r"UV\s*[（(]\s*紫外线\s*[）)]|紫外线\s*[（(]\s*UV\s*[）)]", "UV", target, flags=re.I)
        rendered = len(re.findall(pattern, normalized, re.I)) + normalized.count("紫外线")
        return expected > 0 and rendered == expected
    # A flavour phrase is not a device/model identifier. Keep this confined
    # to the complete phrase in the same source field, never BBQ model codes.
    if str(source_token or "").casefold() == "bbq":
        return bool(re.search(r"\bBBQ\s+style\b", source_text, re.I) and "烧烤风味" in target)
    aliases = _TRANSLATED_TECH_TOKEN_ALIASES.get(str(source_token or "").casefold(), ())
    return _source_term_present(source_text, source_token) and any(
        alias.casefold() in str(target or "").casefold() for alias in aliases
    )


def _is_omittable_display_brand_tech(source_text: str, source_token: str, target: str, *, semantic_facts=(), field_name="") -> bool:
    token = str(source_token or "").casefold()
    if _has_casefold_token(target, source_token):
        return False
    if token in _OMITTABLE_DISPLAY_BRAND_TECH and _source_term_present(source_text, source_token):
        return True
    # A source-scoped, trusted BRAND fact resolves an uppercase brand such
    # as DAY. Unknown acronyms remain technical identifiers, never brands
    # inferred solely from their spelling.
    return field_name == 'name' and any(
        str(getattr(fact,'semantic_type','')) == 'BRAND'
        and _fact_applies_to_field(fact,field_name)
        and str(getattr(fact,'source_text','')).casefold() == token
        and _source_term_present(source_text,source_token)
        for fact in semantic_facts
    )


def _is_ordinary_spanish_uppercase_token(source_text: str, source_token: str) -> bool:
    """Return true for an ordinary Spanish all-caps value, never an identifier.

    The conservative token scanner does not recognise accented Latin letters.
    It can therefore split an official all-caps label such as ``ELÉCTRICA``
    into apparent technical tokens (``EL`` and ``CTRICA``).  A Chinese
    translation must not retain those source-language fragments merely to
    satisfy token preservation.  The lexical fallback remains deliberately
    narrow: it accepts only a Spanish-looking, letter-only span or a span
    touching an accented source word.  Codes such as USB, HSS, TCX and XL do
    not match and remain protected.
    """
    token = str(source_token or "").strip()
    source = str(source_text or "")
    upper = token.upper()
    # ``_TOKEN_RE`` is ASCII-oriented and splits accented words into a
    # prefix/suffix pair: ``COLCHÓN`` -> ``COLCH`` and ``ÓN``.  Detect the
    # prefix before applying the acronym-shape heuristic; short fragments
    # such as COLCH, MICR, PORT and MAGN otherwise look like technical IDs.
    if re.search(rf"{re.escape(token)}[ÁÉÍÓÚÜÑ]", source, flags=re.IGNORECASE):
        return True
    if upper in _ORDINARY_SPANISH_UPPERCASE_WORDS:
        if _source_term_present(source, token):
            return True
        pattern = _ORDINARY_SPANISH_UPPERCASE_FRAGMENTS.get(upper)
        if pattern and re.search(pattern, source, flags=re.IGNORECASE):
            return True
        return bool(re.search(rf"[ÁÉÍÓÚÜÑ]{re.escape(token)}|{re.escape(token)}[ÁÉÍÓÚÜÑ]", source, flags=re.IGNORECASE))
    if not re.fullmatch(r"[A-Z]{4,}", token):
        # Two-letter articles and source fragments created around accents are
        # ordinary Spanish only when they touch an accented word.
        return bool(re.search(rf"[ÁÉÍÓÚÜÑ]{re.escape(token)}|{re.escape(token)}[ÁÉÍÓÚÜÑ]", source, flags=re.IGNORECASE))
    if len(re.findall(r"[AEIOU]", upper)) < 2:
        return False
    # A token embedded in an accented source word (PREVENCI+ÓN, PTICO after
    # Ó) is necessarily a Spanish fragment, not a standalone technical ID.
    if re.search(rf"[ÁÉÍÓÚÜÑ]{re.escape(token)}|{re.escape(token)}[ÁÉÍÓÚÜÑ]", source, flags=re.IGNORECASE):
        return True
    return _source_term_present(source, token)


def _has_casefold_token(text: str, token: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(str(token or ''))}(?![A-Za-z0-9])", str(text or ""), flags=re.IGNORECASE))


def _semantic_aliases(source_term: str, source_text: str, canonical: str) -> tuple[str, ...]:
    aliases = list(_SEMANTIC_TARGET_ALIASES.get(source_term.casefold(), (canonical,)))
    # In these complete phrases illumination describes light usage/effects,
    # rather than an additional lamp product. Do not waive generic product
    # nouns or borrow the qualifying phrase from another source field.
    if source_term.casefold() == "iluminación":
        if re.search(r"\biluminación\s+focal\b", source_text, re.I):
            aliases.append("照明")
        if re.search(r"\befectos?\s+de\s+iluminación\b", source_text, re.I):
            aliases.extend(("灯光效果", "光效"))
        if re.search(r"\biluminación\s+ambiental\b", source_text, re.I):
            aliases.extend(("氛围照明", "环境照明"))
        if re.search(r"\biluminación\s+led\b", source_text, re.I):
            aliases.extend(("LED照明", "LED灯光"))
        if re.search(r"\biluminación\s+(?:de|con)\s+(?:hilo|cable)\s+de\s+cobre\b", source_text, re.I):
            aliases.extend(("铜线灯串", "灯串"))
        if re.search(r"\bmodos?\s+de\s+iluminación\b", source_text, re.I):
            aliases.extend(("照明模式", "灯光模式"))
    # Capsules are not always medicines. Recognize detergent capsules only
    # from a complete phrase in this field; other fields cannot supply it.
    detergent_capsules = bool(re.search(
        r"\b(?:detergentes?\s+en\s+cápsulas|cápsulas\s+de\s+(?:lavado|detergente))\b",
        source_text, re.I))
    if source_term.casefold() in {"cápsulas", "detergente"} and detergent_capsules:
        aliases.append("洗涤凝珠")
        # Laundry-specific Chinese needs an explicit laundry marker, and
        # dishwashing text must not receive this narrower interpretation.
        if (re.search(r"\b(?:color|ropa|colada)\b", source_text, re.I)
                and not re.search(r"\b(?:lavavajillas|vajilla|platos)\b", source_text, re.I)):
            aliases.append("洗衣凝珠")
    # ``paño húmedo`` is a wet wipe, not a generic cleaning cloth.  Keep this
    # context-bound so ordinary ``paño`` facts do not accept ``湿巾``.
    if source_term.casefold() in {"paño", "paños"} and "húmed" in str(source_text or "").casefold():
        aliases.append("湿巾")
    if source_term.casefold() in {"paño", "paños"}:
        if re.search(r"\bpaños?\s+(?:para|de)\s+secar\b", source_text, re.I):
            aliases.append("擦干布")
        if re.search(r"\bpaños?\s+(?:para|de)\s+pulir\b", source_text, re.I):
            aliases.append("抛光布")
    if source_term.casefold() == 'calcetines':
        if re.search(r'\b(?:de|para)\s+beb[eé]s?\b',source_text,re.I):aliases.append('婴儿袜')
        for marker,alias in [('invisibles','隐形袜'),('rizo','毛圈袜')]:
            if _source_term_present(source_text,marker):aliases.append(alias)
    if source_term.casefold() == 'gomas' and re.search(r'\bgomas\s+(?:de|del)\s+pelo\b',source_text,re.I):
        aliases.append('发圈')
    if source_term.casefold() == 'bambú' and re.search(r'\bcestas?\s+de\s+bambú\b',source_text,re.I):
        aliases.append('竹篮')
    if source_term.casefold() == 'madera' and re.search(r'\bmadera\s+de\s+teca\b',source_text,re.I):
        aliases.append('柚木')
    return tuple(dict.fromkeys(aliases))


@dataclass(frozen=True)
class QAFinding:
    rule_id: str
    severity: str
    field_name: str
    evidence: Mapping[str, Any]
    sku: str = ""
    source_hash: str = ""
    source: str = ""
    target: str = ""
    message: str = ""
    repairable: bool = False
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _canonical_numeric_token(token: str) -> str:
    """Normalize Spanish/Chinese display-equivalent numeric spellings.

    Spanish source commonly uses ``3.680`` for 3680 and ``9,5`` for 9.5,
    while Chinese display uses ``3680`` and ``9.5``.  A separator followed by
    exactly three digits is treated as a thousands separator; other single
    separators are treated as decimal points.  This is deliberately limited
    to numeric token comparison and never rewrites source or target text.
    """
    raw = str(token or "").strip()
    if re.fullmatch(r"\d+[.,]\d{3}", raw):
        return raw.replace(".", "").replace(",", "")
    return raw.replace(",", ".")


def _numbers(value: str) -> Counter[str]:
    text = str(value or "")
    # Action source occasionally uses an apostrophe as a decimal separator
    # (``9'5x13 cm``).  Normalize only the digit-to-digit form; apostrophes in
    # ordinary text remain untouched.
    text = re.sub(r"(?<=\d)'(?=\d)", ".", text)
    # A space between a leading group and exactly three trailing digits is a
    # thousands separator in Action source (``23 500``), not two independent
    # numeric facts.  Collapse it before extracting values so its normalized
    # Chinese form ``23500`` compares to one source fact.
    text = re.sub(r"(?<![A-Za-z0-9])(\d{1,3})\s(?=\d{3}(?!\d))", r"\1", text)
    # A comma-delimited shoe-size list is a list, not one decimal number
    # (``39,40,41,42``).  Split only when there are at least three short
    # numeric components, leaving normal decimals such as ``9,5`` intact.
    text = re.sub(
        r"(?<!\d)(\d{1,2}(?:,\d{1,2}){2,})(?!\d)",
        lambda match: match.group(1).replace(",", " "),
        text,
    )
    numbers = Counter(_canonical_numeric_token(item) for item in re.findall(r"\d+(?:[.,]\d+)?", text))
    # The historical outlet-strip title explicitly names four sockets. Count
    # only the complete own-field noun phrase, not a generic multiplier,
    # brand fragment or a quantity borrowed from another field.
    quadruple_outlets = re.findall(r"\bregleta\s+de\s+enchufes\s+cu[áa]druple\b", text, re.I)
    if quadruple_outlets:
        numbers["4"] += len(quadruple_outlets)
    # Spelled-out quantities are still own-field source facts. Restrict this
    # to complete cardinal + counted-noun phrases, not brand/game names or
    # pronouns. Unsupported compound numbers must not become their last digit.
    spanish_cardinals = {"dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
                         "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10"}
    counted_nouns = r"(?:unidades|piezas|pares|rollos|dispositivos|puertos|pestañas|altavoces|bolsillos|modos|horas|pendientes|cajas|colores)"
    for match in re.finditer(rf"\b({'|'.join(spanish_cardinals)})\s+(?:pequeñ[oa]s\s+)?{counted_nouns}\b", text, re.I):
        prefix = text[:match.start()]
        # A conjunction after a counted noun starts another quantity, e.g.
        # dos horas y tres modos. Only a preceding numeric cardinal makes
        # this an unsupported compound suffix (treinta y cinco rollos).
        if re.search(r"\b(?:veinte|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|doscientos|trescientos|cuatrocientos|quinientos|seiscientos|setecientos|ochocientos|novecientos|mil)\s*(?:(?:y|e)\s*)?$", prefix, re.I):
            continue
        numbers[spanish_cardinals[match.group(1).casefold()]] += 1
    # Chinese display text commonly renders source numerals as characters,
    # e.g. ``3 en 1`` -> ``三合一``.  Count those simple digit forms as the
    # same facts without weakening the Arabic-number checks.
    chinese_digits = {"零": "0", "〇": "0", "一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
    connectors = set("合到至折×xX/+-")
    # Keep classifiers/measurement units that reliably signal a numeric fact.
    # ``种`` and ``天`` are intentionally excluded: natural Chinese phrases
    # such as ``一种``/``一天`` occur in ordinary descriptions even when the
    # Spanish source contains no number.  ``块`` is included for phrases such
    # as ``三块面板``.
    quantity_units = set("个件只片颗粒张页套人组支条把盒包瓶罐袋卷双位端口环块层伏瓦毫升升克公斤厘米毫米米小时款")
    digit_chars = set(chinese_digits) | set("0123456789")
    for index, char in enumerate(text):
        if char not in chinese_digits:
            continue
        previous = text[index - 1] if index else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        # Require numeric context so lexical words such as ``五金`` do not
        # become a fabricated number while ``三合一`` and ``两件`` do.
        connector_context = False
        if previous in connectors:
            # A slash is also a product-list separator (``内裤/三角裤``),
            # not necessarily a numeric operator.  Count it only when the
            # other side of the slash is itself numeric.
            connector_context = previous != "/" or (index >= 2 and text[index - 2] in digit_chars)
        if following in connectors:
            connector_context = connector_context or following != "/" or (index + 2 < len(text) and text[index + 2] in digit_chars)
        if connector_context or following in quantity_units or previous in quantity_units or (following == "种" and chinese_digits[char] != "1"):
            numbers[chinese_digits[char]] += 1
    return numbers


def _arabic_numbers(value: str) -> Counter[str]:
    """Return only explicit Arabic-digit numbers from a value."""
    text = re.sub(r"(?<![A-Za-z0-9])(\d{1,3})\s(?=\d{3}(?!\d))", r"\1", str(value or ""))
    text = re.sub(
        r"(?<!\d)(\d{1,2}(?:,\d{1,2}){2,})(?!\d)",
        lambda match: match.group(1).replace(",", " "),
        text,
    )
    return Counter(_canonical_numeric_token(item) for item in re.findall(r"\d+(?:[.,]\d+)?", text))


def _normalize_unit(unit: str) -> str:
    """Normalize display-equivalent units for QA comparison only."""
    return str(unit or "").casefold().replace("℃", "°c")


def _semantic_numeric_equivalents(source_text: str, target: str) -> Counter[str]:
    """Return source numeric facts rendered as scoped Chinese phrases.

    These are intentionally phrase-scoped, rather than a global Arabic-to-
    Chinese digit waiver.  The same number remains a required fact unless a
    reviewed source phrase proves the semantic rendering (for example
    ``4 estaciones`` → ``四季``).
    """
    source = str(source_text or "").casefold()
    target_text = str(target or "")
    equivalents: Counter[str] = Counter()
    phrase_rules = (
        (r"\b(\d+)\s+estaciones?\b", {"4": "四季", "3": "三季", "2": "两季"}),
        (r"\b(\d+)\s+hojas?\b", {"3": ("三层", "三张", "三页", "三刀头")} ),
        (r"\b(\d+)\s+capas?\b", {"3": ("三层", "三层纸", "三层餐巾")} ),
        (r"\b(\d+)\s+en\s+1\b", {"3": ("三合一", "三效合一"), "2": "二合一", "4": "四合一"}),
        (r"\b(\d+)\s+personas?\b", {"1": ("单人", "一人"), "2": ("双人", "两人")}),
        (r"\b(\d+)\s+(?:tonos|colores)\b", {"2": "双色"}),
        (r"\b(1)\s+(?:tamaño|size)\b", {"1": ("均码", "均一尺码", "单一尺码")}),
        (r"\bn\.\s*[ºo]?\s*(\d+)\b", {"1": "一号", "2": "二号", "3": "三号"}),
    )
    for pattern, mapping in phrase_rules:
        for match in re.finditer(pattern, source):
            number = match.group(1)
            aliases = mapping.get(number, ())
            if isinstance(aliases, str):
                aliases = (aliases,)
            if any(alias in target_text for alias in aliases):
                equivalents[number] += 1
    if _zero_percent_is_semantically_rendered(source, target_text):
        equivalents["0"] += len(re.findall(r"(?<!\d)0(?:[.,]0)?\s*%", source))
    return equivalents


def _brand_embedded_numeric_values(source_text: str) -> Counter[str]:
    """Return digits embedded in brand-shaped title spans."""
    values: Counter[str] = Counter()
    for token in re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9&+./-]*\d[A-Za-z0-9&+./-]*(?![A-Za-z0-9])",
        str(source_text or ""),
    ):
        if not any(char.islower() for char in token):
            continue
        prefix = re.match(r"[A-Za-z]+", token)
        if prefix and prefix.group(0).casefold() in {
            "a", "b", "cr", "f", "h", "ip", "k", "lr", "ps", "r", "t", "usb",
        }:
            continue
        if token.casefold().startswith(("series", "modelo", "model")):
            continue
        for number in re.findall(r"\d+(?:[.,]\d+)?", token):
            values[_canonical_numeric_token(number)] += 1
    return values


def _zero_percent_is_semantically_rendered(source_text: str, target: str) -> bool:
    return bool(
        re.search(r"(?<!\d)0(?:[.,]0)?\s*%", str(source_text or ""), flags=re.I)
        and re.search(r"(?:不含|无|零|0\s*[%％])", str(target or ""))
    )


def _semantic_numeric_extras(source_text: str, target: str) -> Counter[str]:
    """Return target numbers explained by a scoped source range expansion."""
    source = str(source_text or "")
    target_numbers = _numbers(target)
    extras: Counter[str] = Counter()
    # A shoe-size range such as ``Talla de calzado: 39-42`` may be rendered
    # as ``39,40,41,42``. Only allow interior values when the source explicitly
    # identifies a footwear-size context and the complete expansion is present.
    if re.search(r"(?i)(?:talla|números?|calzado|zapato)", source):
        for match in re.finditer(r"(?<!\d)(\d{2})\s*-\s*(\d{2})(?!\d)", source):
            start, end = (int(match.group(1)), int(match.group(2)))
            if end <= start or end - start > 10:
                continue
            values = [str(value) for value in range(start, end + 1)]
            if all(target_numbers.get(value, 0) >= 1 for value in values):
                for value in values[1:-1]:
                    extras[value] += 1
    return extras


def _allowed_display_latin_tokens(source_text: str, target: str) -> set[str]:
    source_lower = str(source_text or "").casefold()
    target_lower = str(target or "").casefold()
    allowed: set[str] = set()
    for token in _ALLOWED_LATIN_DISPLAY_TOKENS:
        if token.casefold() in target_lower and token.casefold() in source_lower:
            allowed.add(token)
            if any(ch in token for ch in " -&'"):
                allowed.update(part for part in re.split(r"[\s\-&']+", token.casefold()) if part)
    # ``Dura-Beam`` is sometimes normalized to a space by the provider.
    # The same series is emitted by Action with either a hyphen or a space.
    # It is not a Spanish-language residual when the source contains either
    # spelling and the candidate preserves that display token.
    if ("dura-beam" in source_lower or "dura beam" in source_lower) and "dura beam" in target_lower:
        allowed.update({"dura-beam", "dura beam"})
    allowed.update(_source_bound_display_tokens(source_text, target))
    return allowed


def _detail_source_value_findings(source_text, target):
    # Historical Action text contains this invalid physical-state value.
    # Preserve the official source; do not infer gel/liquid or an efficacy claim.
    return [QAFinding("SOURCE_DETAILS_TYPED_VALUE_INVALID", "BLOCKER", "details",
        {"source_key": p.key, "source_value": p.value}, source=source_text, target=target,
        message="physical-state source value requires source review", blocking=True)
        for p in parse_structured_details(source_text)
        if p.key.strip().casefold() == "sustancia"
        and p.value.strip().casefold() in {"válido", "valido"}]


def _detail_charging_speed_findings(source_text, target):
    """Check a selected speed, not the alternatives in its attribute label."""
    source_items = [p for p in parse_structured_details(source_text)
        if re.fullmatch(r"cargador\s+r[aá]pido\s*/\s*lento", p.key.strip(), re.I)
        and p.value.strip().casefold() in {"rápido", "rapido", "lento"}]
    target_items = [p for p in parse_structured_details(target)
        if "充电" in p.key or "充电器" in p.key or re.search(r"快充|慢充", p.key)]
    findings = []
    if source_items and len(source_items) != len(target_items):
        return [QAFinding("DETAIL_CHARGING_SPEED_MISSING", "BLOCKER", "details", {},
            source=source_text, target=target, message="selected charging speed is missing", blocking=True)]
    for src, dst in zip(source_items, target_items):
        expected_fast = src.value.strip().casefold() in {"rápido", "rapido"}
        fast = bool(re.fullmatch(r"快速(?:充电(?:器)?)?|快充(?:充电器)?", dst.value.strip()))
        slow = bool(re.fullmatch(r"慢速(?:充电(?:器)?)?|慢充(?:充电器)?|缓慢(?:充电)?", dst.value.strip()))
        if fast != expected_fast or slow == expected_fast:
            findings.append(QAFinding("DETAIL_CHARGING_SPEED_CHANGED", "BLOCKER", "details",
                {"source_key": src.key, "source_value": src.value,
                 "target_key": dst.key, "target_value": dst.value},
                source=source_text, target=target, message="selected charging speed changed or ambiguous", blocking=True))
    return findings


def _detail_boolean_findings(source_text, target):
    """Compare presence truth, including explicit negative source labels."""
    attributes = ((r"\balcohol\b", r"酒精"), (r"\bsilicona\b", r"硅(?:酮|胶)?"),
        (r"\bgluten\b", r"麸质"), (r"\blactosa\b", r"乳糖"),
        (r"\bperfume\b", r"香(?:料|精|型|味)|(?:无|有)香"),
        (r"\bjab[oó]n\b", r"皂"), (r"\baz[uú]car(?:es)?\b", r"(?<!乳)糖"),
        (r"^aclarado$", r"冲洗|免洗"))
    bools = {"si": True, "sí": True, "yes": True, "true": True, "是": True,
             "no": False, "false": False, "否": False}
    source_pairs = parse_structured_details(source_text)
    target_pairs = parse_structured_details(target)
    findings = []
    for source_pattern, target_pattern in attributes:
        source_items = [p for p in source_pairs if re.search(source_pattern, p.key, re.I) and p.value.strip().casefold() in bools]
        target_items = [p for p in target_pairs if re.search(target_pattern, p.key) and p.value.strip().casefold() in bools]
        if source_items and len(source_items) != len(target_items):
            findings.append(QAFinding("DETAIL_BOOLEAN_FIELD_MISSING", "BLOCKER", "details", {"source_attribute": source_pattern}, source=source_text, target=target, blocking=True))
            continue
        for src, dst in zip(source_items, target_items):
            source_negative = bool(re.match(r"^(?:sin\b|libre de\b|no contiene\b|no incluye\b)", src.key.strip(), re.I))
            target_negative = bool(re.search(r"无|不含|未添加|不添加|零", dst.key))
            if source_pattern == r"^aclarado$" and "免洗" in dst.key:
                target_negative = True
            source_present = bools[src.value.strip().casefold()] != source_negative
            target_present = bools[dst.value.strip().casefold()] != target_negative
            if source_present != target_present:
                findings.append(QAFinding("DETAIL_BOOLEAN_POLARITY_CHANGED", "BLOCKER", "details",
                    {"source_key": src.key, "source_value": src.value, "target_key": dst.key, "target_value": dst.value},
                    source=source_text, target=target, message="boolean fact polarity changed", blocking=True))
    return findings


def _detail_care_findings(source_text, target):
    source_pairs = parse_structured_details(source_text)
    target_pairs = parse_structured_details(target)
    no_iron = any(re.search(r"instrucciones\s+de\s+planchado", p.key, re.I)
        and re.fullmatch(r"sin planchado|no planchar", p.value.strip(), re.I) for p in source_pairs)
    if no_iron and any("熨烫" in p.key and re.search(r"无需|不用|免熨|不必", p.value) for p in target_pairs):
        return [QAFinding("CARE_INSTRUCTION_CHANGED", "BLOCKER", "details", {},
            source=source_text, target=target, message="care instruction changed into an optional/easy-care claim", blocking=True)]
    return []


def audit_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...], *, terminology: tuple[Mapping[str, Any], ...] = (), semantic_facts: tuple[Any, ...] = ()) -> tuple[QAFinding, ...]:
    findings: list[QAFinding] = []
    for field_name in requested_fields:
        target = fields.get(field_name)
        source_name = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, "")
        source_text = str(getattr(source, source_name, "") or "")
        if not isinstance(target, str) or not target.strip():
            if not source_text.strip():
                # Empty official source is not a missing translation.  The
                # field is NOT_REQUIRED unless an explicit relocation policy
                # says otherwise; never synthesize content from other fields.
                continue
            findings.append(QAFinding("EMPTY_REQUIRED_FIELD", "BLOCKER", field_name, {}, source=source_text, message="required target field is empty", blocking=True))
            continue
        if not source_text.strip():
            findings.append(QAFinding("EMPTY_SOURCE_TARGET_NONEMPTY", "BLOCKER", field_name, {"target": target}, source=source_text, target=target, message="target content exists without official source", blocking=True))
            continue
        if field_name == "details":
            findings.extend(_detail_source_value_findings(source_text, target))
            findings.extend(_detail_charging_speed_findings(source_text, target))
            findings.extend(_detail_boolean_findings(source_text, target))
            findings.extend(_detail_care_findings(source_text, target))
            if (re.search(r"\bpincel(?:es)?\b", str(getattr(source, "name_es", "") or ""), re.I)
                    and re.search(r"material\s+cabello", source_text, re.I) and "发丝" in target):
                findings.append(QAFinding("DETAIL_SUBJECT_CHANGED", "BLOCKER", field_name,
                    {"source_subject": "brush bristles", "target_subject": "human hair"}, source=source_text, target=target, blocking=True))
        source_nonsterile = bool(re.search(r"\bno\s+est[eé]ril(?:es)?\b", source_text, re.I))
        target_nonsterile = bool(re.search(r"非无菌|非灭菌|未灭菌|未经灭菌|不是无菌|未进行灭菌", target))
        if source_nonsterile and not target_nonsterile:
            findings.append(QAFinding("STERILITY_STATUS_CHANGED" if "无菌" in target else "STERILITY_STATUS_DROPPED",
                "BLOCKER", field_name, {"source_status": "NON_STERILE"}, source=source_text, target=target, blocking=True))
        elif not source_nonsterile and re.search(r"\best[eé]ril(?:es)?\b", source_text, re.I) and target_nonsterile:
            findings.append(QAFinding("STERILITY_STATUS_CHANGED", "BLOCKER", field_name,
                {"source_status": "STERILE"}, source=source_text, target=target, blocking=True))
        if (re.search(r"\bmicrofibras?\b", " ".join(str(getattr(source, key, "") or "") for key in ("name_es", "spec_es", "desc_es", "details_es")), re.I)
                and re.search(r"鸡毛掸|羽毛掸", target)):
            findings.append(QAFinding("MATERIAL_CONFLICT_WITH_SOURCE", "BLOCKER", field_name,
                {"source_material": "microfibra", "target_material": "feather"}, source=source_text, target=target, blocking=True))
        if "null" in target.casefold() or "undefined" in target.casefold():
            findings.append(QAFinding("NULL_UNDEFINED_RESIDUAL", "BLOCKER", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        allowed_display_tokens = _allowed_display_latin_tokens(source_text, target)
        # Brands and interfaces may be repeated in a description/detail even
        # when their official source is the product name.  Keep this explicit
        # and shape-bound; ordinary Spanish from the name is never allowed.
        allowed_display_tokens.update(
            _source_bound_cross_field_tokens(getattr(source, "name_es", ""), target)
        )
        if has_ordinary_spanish(target, allowed_tokens=allowed_display_tokens):
            findings.append(QAFinding("SPANISH_RESIDUAL", "ERROR", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        # Technical/numeric guards cannot detect an omitted ordinary product
        # noun.  Reuse deterministic semantic facts when the source term is
        # present in this field and has a reviewed canonical Chinese value.
        # Brand/IP facts are intentionally excluded because the display policy
        # requires those spans to be omitted from Chinese names.
        covered_types = {"PRODUCT_TYPE", "FUNCTION", "MATERIAL", "COMPATIBILITY", "CARE", "NUTRITION", "VARIANT"}
        for fact in semantic_facts:
            fact_type = str(getattr(fact, "semantic_type", "") or "")
            source_term = str(getattr(fact, "source_text", "") or "").strip()
            canonical = str(getattr(fact, "canonical_value", "") or getattr(fact, "value", "") or "").strip()
            if fact_type not in covered_types or not source_term or not canonical or canonical.casefold() == source_term.casefold():
                continue
            # Do not propagate a SKU-level fact to every requested target
            # field.  Facts are field-scoped; only an explicit planner
            # relocation contract can override the source-field mapping.
            if not _fact_applies_to_field(fact, field_name):
                continue
            aliases = _semantic_aliases(source_term, source_text, canonical)
            if _source_term_present(source_text, source_term) and not any(alias.casefold() in target.casefold() for alias in aliases):
                findings.append(QAFinding(
                    "SEMANTIC_FACT_DROPPED", "ERROR", field_name,
                    {
                        "semantic_type": fact_type,
                        "source_term": source_term,
                        "expected_target": canonical,
                        "source_field": str(getattr(fact, "source_field", "") or ""),
                        "target_scope": field_name,
                        "placement": str(getattr(fact, "placement", "") or ""),
                    },
                    source=source_text, target=target,
                    message="semantic product fact is not represented in target",
                    blocking=True,
                ))
        source_numbers, target_numbers = _numbers(source_text), _numbers(target)
        # Ordinary translations cannot import quantities from other fields.
        # Preserve the established canonical-spec relocation contract only
        # for spec; independent source numbers avoid joined thousands groups.
        allowed_source_numbers = source_numbers.copy()
        if field_name == "spec":
            allowed_source_numbers = Counter()
            for attr in ("name_es", "spec_es", "desc_es", "details_es", "cat1_es", "cat2_es"):
                allowed_source_numbers.update(_numbers(str(getattr(source, attr, "") or "")))
        dropped = source_numbers - target_numbers
        dropped -= _semantic_numeric_equivalents(source_text, target)
        if field_name == "name":
            # No-brand display removes digits embedded in brand spans such as
            # ``Lab31``/``Cool2Party``. Do not waive standalone quantities or
            # compact technical models here.
            brand_numeric_waivers = _brand_embedded_numeric_values(source_text)
            verified_brand_numbers: Counter[str] = Counter()
            seen_brands = set()
            for fact in semantic_facts:
                brand=str(getattr(fact,'source_text','') or '')
                if (str(getattr(fact,'semantic_type',''))=='BRAND'
                    and _fact_applies_to_field(fact,field_name)
                    and _source_term_present(source_text,brand)
                    and not _has_casefold_token(target,brand)
                    and brand.casefold() not in seen_brands):
                    # Count only this verified omitted brand span. A second
                    # standalone 9 in "9th Avenue, 9 unidades" stays protected.
                    verified_brand_numbers.update(_numbers(brand))
                    seen_brands.add(brand.casefold())
            # Heuristic and verified roles may refer to the same Lab31 span;
            # union their counts rather than waive it twice.
            dropped -= brand_numeric_waivers | verified_brand_numbers
        # Brand/series tokens can contain digits that are not product facts.
        # Under the no-brand display policy, ``7Up`` may be removed from the
        # Chinese name; its ``7`` must not become a NUMERIC_DROPPED blocker.
        if field_name == "name" and re.search(r"(?<![A-Za-z0-9])7up(?![A-Za-z0-9])", source_text, re.I):
            dropped["7"] -= 1
            if dropped["7"] <= 0:
                del dropped["7"]
        # Canonical spec may repeat an official relocated number (40cm plus
        # 40×40cm). Other fields require their own source evidence.
        # Chinese classifiers such as ``一条``/``一天`` are often introduced
        # by a faithful translation of Spanish articles (``una``/``uno``),
        # not by an added numeric fact.  Treat explicit Arabic digits as
        # additions unconditionally; only count a Chinese numeral as an
        # addition when that numeric value is already present in the official
        # allowed source scope and is repeated beyond its source count.
        target_arabic = _arabic_numbers(target)
        duplicated = Counter({value: count for value, count in target_arabic.items() if value not in allowed_source_numbers})
        duplicated -= _semantic_numeric_extras(source_text, target)
        # This complete functional phrase is an explicit quantity, unlike
        # an ordinary Chinese article such as 一条. It needs its own-field
        # source phrase even if another field happens to contain number 3.
        three_effect_claims = len(re.findall(r"三效合一", target))
        three_in_one_source = len(re.findall(r"\b3\s+en\s+1\b", source_text, re.I))
        if three_effect_claims > three_in_one_source:
            duplicated["3"] += three_effect_claims - three_in_one_source
        for value, count in target_numbers.items():
            if value in target_arabic or value not in allowed_source_numbers:
                continue
            extra = count - allowed_source_numbers.get(value, 0)
            if extra > 0:
                duplicated[value] += extra
        if dropped:
            findings.append(QAFinding("NUMERIC_DROPPED", "BLOCKER", field_name, {"source": dict(source_numbers), "target": dict(target_numbers), "missing": dict(dropped)}, source=source_text, target=target, message="numeric fact dropped", blocking=True))
        if duplicated:
            findings.append(QAFinding("NUMERIC_ADDED", "BLOCKER", field_name, {"source": dict(source_numbers), "target": dict(target_numbers), "extra": dict(duplicated)}, source=source_text, target=target, message="numeric fact added", blocking=True))
        protected = protect_text(source_text)
        target_protected = protect_text(target)
        def strict_key(kind, value):
            # Quantified technical tokens already use whitespace equivalence
            # below for preservation; their changed/added comparison must
            # use the same rule. Models and certification IDs stay literal.
            return re.sub(r"\s+", "", value).casefold() if kind in {"CAPACITY", "BATTERY_CAPACITY"} else value.casefold()
        source_strict = Counter((kind, strict_key(kind, value)) for kind, value in zip(protected.token_types, protected.tokens) if kind in _STRICT_TOKEN_TYPES)
        target_strict = Counter((kind, strict_key(kind, value)) for kind, value in zip(target_protected.token_types, target_protected.tokens) if kind in _STRICT_TOKEN_TYPES)
        for (kind, value), expected_count in source_strict.items():
            actual_count = target_strict.get((kind, value), 0)
            if actual_count < expected_count:
                if kind == "TECH" and _is_ordinary_spanish_uppercase_token(source_text, value):
                    continue
                if kind == "TECH" and _is_omittable_display_brand_tech(source_text, value, target, semantic_facts=semantic_facts, field_name=field_name):
                    continue
                if kind == "TECH" and _is_allowed_translated_tech_token(source_text, value, target):
                    continue
                if kind == "TECH" and _has_casefold_token(target, value):
                    continue
                same_kind = sum(count for (other_kind, _), count in target_strict.items() if other_kind == kind)
                if kind == "MODEL" and same_kind == 0:
                    findings.append(QAFinding("MODEL_DROPPED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
                elif same_kind >= expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_CHANGED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
        allow_fixed_category_tokens = field_name == "cat1" and target in FIXED_CAT1
        for (kind, value), actual_count in target_strict.items():
            if actual_count > source_strict.get((kind, value), 0) and not allow_fixed_category_tokens:
                if _is_allowed_translated_strict_token(source_text, value, target, actual_count):
                    continue
                if kind == "TECH" and _has_casefold_token(source_text, value):
                    continue
                rule = "MODEL_CHANGED" if kind == "MODEL" and not source_strict.get((kind, value), 0) else "PROTECTED_TOKEN_ADDED"
                findings.append(QAFinding(rule, "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": source_strict.get((kind, value), 0), "actual": actual_count}, source=source_text, target=target, blocking=True))
        for value, kind in zip(protected.tokens, protected.token_types):
            if kind in {"URL", "SKU", "EAN", "MODEL", "TECH", "CERTIFICATION", "CAPACITY", "BATTERY_CAPACITY", "POWER", "VOLTAGE"}:
                if kind == "TECH" and _is_ordinary_spanish_uppercase_token(source_text, value):
                    continue
                if kind == "TECH" and _is_omittable_display_brand_tech(source_text, value, target, semantic_facts=semantic_facts, field_name=field_name):
                    continue
                if kind == "TECH":
                    expected_count = source_text.casefold().count(value.casefold())
                    actual_count = target.casefold().count(value.casefold())
                else:
                    expected_count, actual_count = source_text.count(value), target.count(value)
                if kind in {"CAPACITY", "BATTERY_CAPACITY", "POWER", "VOLTAGE"}:
                    compact_value = re.sub(r"\s+", "", value).casefold()
                    compact_source = re.sub(r"\s+", "", source_text).casefold()
                    compact_target = re.sub(r"\s+", "", target).casefold()
                    expected_count = compact_source.count(compact_value)
                    actual_count = compact_target.count(compact_value)
                if actual_count < expected_count:
                    if kind == "TECH" and _is_allowed_translated_tech_token(source_text, value, target):
                        continue
                    if kind == "TECH" and _is_omittable_display_brand_tech(source_text, value, target, semantic_facts=semantic_facts, field_name=field_name):
                        continue
                    findings.append(QAFinding("PROTECTED_TOKEN_MISSING", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
                elif actual_count > expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_DUPLICATED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
        unit_source = source_text
        if field_name == "name":
            for fact in semantic_facts:
                brand = str(getattr(fact, "source_text", "") or "")
                if (str(getattr(fact, "semantic_type", "")) == "BRAND"
                        and _fact_applies_to_field(fact, field_name) and brand
                        and not _has_casefold_token(target, brand)):
                    # A trusted, field-bound 3M brand is not three metres.
                    # Match exact spelling, so a separate 3 m/3m stays a unit.
                    unit_source = re.sub(rf"(?<!\w){re.escape(brand)}(?!\w)", " ", unit_source)
        source_units = _units(unit_source)
        target_units = _units(target)
        for unit in source_units:
            if unit == "%" and _zero_percent_is_semantically_rendered(source_text, target):
                continue
            if unit.casefold() not in target_units:
                findings.append(QAFinding("UNIT_DROPPED", "BLOCKER", field_name, {"unit": unit}, source=source_text, target=target, message="technical unit dropped", blocking=True))
        for term in terminology:
            source_term = str(term.get("source") or term.get("source_term") or "")
            target_term = str(term.get("target") or term.get("target_term") or "")
            if (source_term and target_term and source_term.casefold() in source_text.casefold()
                    and target_term not in target
                    and not _omittable_display_term(term, semantic_facts, field_name)):
                findings.append(QAFinding("TERMINOLOGY_VIOLATION", "ERROR", field_name, {"source": source_term, "target": target_term}, source=source_text, target=target, blocking=True))
            forbidden = str(term.get("forbidden_target") or "")
            if forbidden and forbidden in target:
                findings.append(QAFinding("FORBIDDEN_TERM", "ERROR", field_name, {"term": forbidden}, source=source_text, target=target, blocking=True))
        if field_name == "cat1" and target not in FIXED_CAT1:
            findings.append(QAFinding("CATEGORY_INVALID", "BLOCKER", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        if "<" in target and ">" in target:
            findings.append(QAFinding("HTML_RESIDUAL", "ERROR", field_name, {}, source=source_text, target=target, repairable=True, blocking=True))
    return tuple(findings)


def guard_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...], *, terminology: tuple[Mapping[str, Any], ...] = (), semantic_facts: tuple[Any, ...] = (), context: Any = None, production: bool = False) -> dict[str, Any]:
    findings = audit_translation(source, fields, requested_fields, terminology=terminology, semantic_facts=semantic_facts)
    result = {"status": "PASS" if not findings else "FAIL", "fact_status": "PASS" if not findings else "FAIL", "findings": [finding.as_dict() for finding in findings], "canonical_status": "NOT_RUN", "canonical_qa_status": "NOT_RUN", "overall_ready": not findings}
    if context is not None:
        from .canonical_qa import canonical_guard
        canonical = canonical_guard(context, fields, production=production)
        result["canonical_status"] = canonical.get("status", "FAIL")
        result["canonical_qa_status"] = canonical.get("status", "FAIL")
        result["canonical_findings"] = canonical.get("findings", [])
        result["canonical"] = canonical
        result["overall_ready"] = not findings and canonical.get("status") in {"PASS", "NOT_REQUIRED"}
    return result
