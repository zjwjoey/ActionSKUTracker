"""Offline safety checks for model-authored Chinese translations.

The guard is intentionally a *rejector*, not an auto-corrector.  It verifies
that a model did not change immutable numeric facts or move facts between
fields.  Callers can route rejected rows to the existing review queue; the
guard never writes Master, the dictionary, or a model cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections import Counter
from typing import Iterable, Mapping


NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_CJK = re.compile(r"[\u3400-\u9fff]")

FIXED_CAT1 = frozenset({
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容", "家居布置",
    "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具", "兴趣手作", "园艺户外", "运动用品",
})

# Canonical units are deliberately conservative and measurement-bound.  Short
# symbols and Chinese unit characters are counted only when attached to a
# number; otherwise ordinary words such as ``mango(s)``, ``巧克力`` and ``安装``
# would be false units.  Conversions (1 L -> 1000 ml) are intentionally not
# attempted by this reject-only guard.
_MEASURE_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
_SPANISH_NUMBER_WORD = r"(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(rf"{_MEASURE_NUMBER}\s*(?:%|por\s+ciento|百分之)", re.IGNORECASE)),
    ("gsm", re.compile(
        rf"{_MEASURE_NUMBER}\s*(?:gsm|gr?\s*/\s*m(?:[²2]|平方(?:米)?)|克\s*/\s*平方米)",
        re.IGNORECASE,
    )),
    ("kg", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kg|kilogramos?|千克|公斤)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("g", re.compile(rf"{_MEASURE_NUMBER}\s*(?:g|gramos?|克)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("ml", re.compile(rf"{_MEASURE_NUMBER}\s*(?:ml|mililitros?|毫升)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("cl", re.compile(rf"{_MEASURE_NUMBER}\s*(?:cl|centilitros?|厘升)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("l", re.compile(rf"{_MEASURE_NUMBER}\s*(?:l|litros?|升)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("km", re.compile(rf"{_MEASURE_NUMBER}\s*(?:km|kil[oó]metros?|千米|公里)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("mm2", re.compile(
        rf"{_MEASURE_NUMBER}[ \t]*(?:mm[ \t]*[²2]|mil[ií]metros?[ \t]+cuadrados?|平方毫米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])",
        re.IGNORECASE,
    )),
    ("kwh", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kwh|千瓦时)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("mm", re.compile(rf"{_MEASURE_NUMBER}\s*(?:mm|mil[ií]metros?|毫米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("cm", re.compile(rf"{_MEASURE_NUMBER}\s*(?:cm|cent[ií]metros?|厘米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("m", re.compile(rf"{_MEASURE_NUMBER}\s*(?:m(?:[²2])?|metros?|平方米|米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("v", re.compile(rf"{_MEASURE_NUMBER}\s*(?:v|voltios?|伏特|伏)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("w", re.compile(rf"{_MEASURE_NUMBER}\s*(?:w|vatios?|瓦特|瓦)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("a", re.compile(rf"{_MEASURE_NUMBER}\s*(?:A|[Aa]mperios?|安培|安)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])")),
    ("mah", re.compile(rf"{_MEASURE_NUMBER}\s*(?:ma?h|毫安时)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("lm", re.compile(rf"{_MEASURE_NUMBER}\s*(?:lm|l[uú]menes?|lumen|流明)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("celsius", re.compile(rf"{_MEASURE_NUMBER}\s*(?:℃|[°º]\s*c|grados?\s+celsius|摄氏度)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("kcal", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kcal|kilocalor[ií]as?|千卡)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("kj", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kj|kilojulios?|千焦)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("hour", re.compile(rf"{_MEASURE_NUMBER}\s*(?:h|horas?|小时)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("minute", re.compile(rf"{_MEASURE_NUMBER}\s*(?:min|minutos?|分钟)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("second", re.compile(rf"{_MEASURE_NUMBER}\s*(?:s|segundos?|秒)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("serving", re.compile(rf"{_MEASURE_NUMBER}\s*(?:raciones?|份)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
)
_SOURCE_GRAMMAGE_KEY_VALUE = re.compile(
    rf"gramos\s+por\s+m(?:[²2])\s*\)?\s*[:：]\s*{_MEASURE_NUMBER}\s*g\b",
    re.IGNORECASE,
)

_SPANISH_WORD_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("m", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+metros?\b", re.IGNORECASE)),
    ("percent", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+por\s+ciento\b", re.IGNORECASE)),
    ("kg", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+kilogramos?\b", re.IGNORECASE)),
    ("g", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+gramos?\b", re.IGNORECASE)),
    ("l", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+litros?\b", re.IGNORECASE)),
)
SPANISH_WORD_TEMPORAL_QUANTITY = re.compile(
    rf"\b(?P<num>{_SPANISH_NUMBER_WORD})\s+(?:mes(?:es)?|a(?:ñ|n)os?|semanas?|d[ií]as?)\b",
    re.IGNORECASE,
)

# Product codes and standards are immutable facts, not translation style.
# Avoid matching ordinary title-case words or measurement symbols here.
_TECH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"USB(?:[\s-]?[A-Z])?|LEDs?|PEFC|FSC|HDMI|NFC|RFID|ENC|"
    r"XXXL|XXL|XL|ALL[\s-]?IN[\s-]?(?:ONE|1)|\d+[\s-]*(?:IN|EN)[\s-]*\d+|"
    r"WI[\s-]?FI|BLUETOOTH|AAA|AA|PD|QC(?:\d+(?:\.\d+)?)?|"
    r"IP\d{2,3}|[A-Z]{1,5}[/-]?[A-Z]*\d+[A-Z0-9./-]*"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# Compatibility platforms must survive no-brand title rendering. These are
# compared as technical facts even when a display policy removes the adjacent
# commercial brand (for example ``Nintendo Switch`` -> ``Switch``). Bare
# ``Switch`` is intentionally case-sensitive to avoid treating the ordinary
# English verb/noun ``switch`` as a console platform.
_PLATFORM_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NINTENDO_SWITCH_OLED", re.compile(r"(?<![A-Za-z0-9])(?:Nintendo\s+)?Switch\s+OLED(?![A-Za-z0-9])", re.IGNORECASE)),
    ("NINTENDO_SWITCH_LITE", re.compile(r"(?<![A-Za-z0-9])(?:Nintendo\s+)?Switch\s+Lite(?![A-Za-z0-9])", re.IGNORECASE)),
    ("NINTENDO_SWITCH", re.compile(
        r"(?<![A-Za-z0-9])Nintendo\s+Switch(?![A-Za-z0-9])(?!\s+(?:Lite|OLED))|"
        r"(?<![A-Za-z0-9])Switch(?![A-Za-z0-9])(?!\s+(?:Lite|OLED))"
    )),
    ("PLAYSTATION_5", re.compile(r"(?<![A-Za-z0-9])(?:PlayStation\s*5|PS5)(?![A-Za-z0-9])", re.IGNORECASE)),
    ("PLAYSTATION_4", re.compile(r"(?<![A-Za-z0-9])(?:PlayStation\s*4|PS4)(?![A-Za-z0-9])", re.IGNORECASE)),
    ("PLAYSTATION_3", re.compile(r"(?<![A-Za-z0-9])(?:PlayStation\s*3|PS3)(?![A-Za-z0-9])", re.IGNORECASE)),
    ("PLAYSTATION", re.compile(r"(?<![A-Za-z0-9])PlayStation(?![A-Za-z0-9])(?!\s*[345])", re.IGNORECASE)),
    ("XBOX_SERIES_X", re.compile(r"(?<![A-Za-z0-9])Xbox\s+Series\s+X(?![A-Za-z0-9])", re.IGNORECASE)),
    ("XBOX_SERIES_S", re.compile(r"(?<![A-Za-z0-9])Xbox\s+Series\s+S(?![A-Za-z0-9])", re.IGNORECASE)),
    ("XBOX_ONE", re.compile(r"(?<![A-Za-z0-9])Xbox\s+One(?![A-Za-z0-9])", re.IGNORECASE)),
    ("XBOX", re.compile(r"(?<![A-Za-z0-9])Xbox(?![A-Za-z0-9])(?!\s+(?:Series\s+[XS]|One))", re.IGNORECASE)),
    ("WINDOWS", re.compile(r"(?<![A-Za-z0-9])Windows(?:\s+\d{1,2})?(?![A-Za-z0-9])", re.IGNORECASE)),
    ("IOS", re.compile(r"(?<![A-Za-z0-9])iOS(?![A-Za-z0-9])")),
    ("ANDROID", re.compile(r"(?<![A-Za-z0-9])Android(?![A-Za-z0-9])|安卓", re.IGNORECASE)),
    ("MACOS", re.compile(r"(?<![A-Za-z0-9])macOS(?![A-Za-z0-9])|(?<![A-Za-z0-9])Mac\s+OS(?![A-Za-z0-9])", re.IGNORECASE)),
    ("CHROMEOS", re.compile(r"(?<![A-Za-z0-9])Chrome\s*OS(?![A-Za-z0-9])", re.IGNORECASE)),
)
_TECH_EXCLUDED = frozenset({
    "G", "KG", "ML", "CL", "L", "MM", "CM", "M", "KM", "V", "W", "A", "MAH", "LM",
    # These are measurement spellings already checked by unit_tokens(), not
    # model/standard identifiers (e.g. kWh/1000h or g/m²).
    "KWH/1000H", "G/M2", "GR/M2",
})
_CHINESE_NUMBER = {"零": "0", "〇": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}

# Sustainability marks are research facts. Compare them by recognized source
# and Chinese aliases so a faithful Chinese rendering does not need to retain
# the English phrase, while omission or unsupported addition is still flagged.
_CERTIFICATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Do not use ``\b`` around Latin marks here: in Python's Unicode regex
    # semantics, adjacent Han characters are word characters too, so common
    # faithful forms such as ``采用FSC®认证`` would be missed.  Restrict the
    # boundary to ASCII alphanumerics instead, which still prevents matching
    # longer identifiers such as ``FSCX`` while allowing Chinese suffixes.
    ("FSC", re.compile(r"(?<![A-Z0-9])FSC(?:\s*®)?(?![A-Z0-9])|森林管理委员会", re.IGNORECASE)),
    ("PEFC", re.compile(r"(?<![A-Z0-9])PEFC(?![A-Z0-9])|森林认证认可计划", re.IGNORECASE)),
    ("BCI", re.compile(r"(?<![A-Z0-9])BCI(?![A-Z0-9])|Better\s+Cotton(?:\s+Initiative)?|良好棉花(?:倡议)?", re.IGNORECASE)),
    # ``comercio justo`` / ``公平贸易`` can describe a generic trade practice,
    # not the Fairtrade certification.  Count the mark itself (even when
    # adjacent to Han text) or an explicit Chinese certification phrase only.
    ("FAIRTRADE", re.compile(r"(?<![A-Z0-9])Fairtrade(?![A-Z0-9])|公平贸易认证", re.IGNORECASE)),
    ("GOTS", re.compile(r"\bGOTS\b|全球有机纺织品标准", re.IGNORECASE)),
    ("RAINFOREST_ALLIANCE", re.compile(r"Rainforest\s+Alliance|雨林联盟", re.IGNORECASE)),
)
_INTERNAL_REVIEW_NOTE = re.compile(
    r"原文未注明(?:数量|规格|型号)?|描述专项复核修复|字段边界修复|分类归一化说明|"
    r"(?:翻译|描述|规格|分类)修复记录|QA(?:_LOG|说明|备注)|审核过程说明",
    re.IGNORECASE,
)
_SOURCE_NEGATION = re.compile(
    r"\b(?:sin|nunca|ning[uú]n[oa]?|tampoco|nada|inodoras?|inolor(?:o|a|os|as))\b|\blibres?\s+de\b|"
    r"\bexcl\.(?=\s)|\b0\s*%\s*(?:de\s+)?alcohol\b|"
    r"\bexcepto\b|"
    r"\belimina(?:r)?\s+(?:la\s+)?necesidad\s+de\b|"
    r"\b(?:te|se)\s+ahorras?\s+utilizar\b|"
    r"\bse\s+acab(?:ó|aron)\b|"
    r"\bno\b(?!\s*(?:solo\b|frost|[.#:]?\s*\d))|"
    r"\b(?:inborrable|innecesari[oa]s?)\b|\bya\s+no\b",
    re.IGNORECASE,
)
_TARGET_NEGATION = re.compile(
    r"多余毛发|不需要的毛发|不想要的毛发|不过于刺眼|不刺眼|不过亮|不弯腰|不弄湿双手|不间断|无肩带|"
    r"痘痘.{0,6}(?:消失|不见|没有)|(?:消失|不见).{0,6}痘痘|抑制.{0,4}异味|减少.{0,4}异味|避免.{0,4}异味|去除.{0,4}异味|"
    r"没有|勿|绝不|再也不|不会再|从此不|永不|不必再|不需要再|无需再|不用再|"
    r"无需担心|不必担心|不用担心|"
    r"(?:减少.{0,12}(?:一次性)?塑料袋(?:的)?使用|不油腻|不易滴落|不滴落|不易粘(?:锅|附着)?|不易.{0,8}(?:从手中滑落|下滑)|不希望外露|不想外露|不.{0,12}留(?:下)?白痕)|"
    r"不(?:含|带|可|能|是|使|使用|保证|遮挡|建议|适用|推荐|耐|支持|提供|包括|属于|含有|添加|加|额外添加|影响|受限制|限|占|(?:易)?结块|(?:易)?染色|留污渍|(?:易)?弄脏|混(?:在一起|合)|"
    r"露出|突出|透明|反光|起皱|起泡|变色|褪色|凌乱|留|会|再|掉|产生|应|需要|易掉|易滑落|易下滑|易黄变|易变黄|易变色|易褪色|易干|易留|易松散|易擦除|易扬尘|易晕染|飞溅|必|滑动)|"
    r"未(?:含|带|配备|提供|使用|达到|经过|分级|分类)|"
    r"非(?:一次性|可重复使用|防水|食品级|无菌|电动|虚构|小说|织|纺)|"
    r"无(?:泡|气泡|胶痕|胶渍|残留|指纹|薄荷|噪音|杂音|恼人噪音|袋|闭合|特定场合|划痕|酸|硅油|溶剂|金属|钢圈|紧束带|调节带|添加成分|PVC|人工添加剂|微塑料|尘袋|尘|盐|毛|发|老茧|硬茧|后跟|头带|头梁|气味|异味|明显气味|味|香|香型|香精|(?:额外)?(?:信息|酒精|糖|乳糖|麸质|谷物|BPA|香料|添加剂|防腐剂|磷酸盐|松紧|须|需|纺|缝|接缝|木浆|木杆|(?:水)?痕))|"
    r"(?:缺乏.{0,4}活力|设计简洁.{0,10}突出照片|简洁设计.{0,10}突出照片)|"
    r"零热量|"
    r"未经(?:加工|烘烤|烘焙)|未(?:加工|鞣制|烘烤)|免(?:加热|热)|"
    r"(?:防止非接触式盗刷|防止.{0,8}(?:碎发|头发|发丝).{0,6}(?:掉|落|垂).{0,8}(?:脸|面)|并非完全防污)|"
    r"防(?:漏|漏气|串味|勾丝|脱线|焦)|"
    r"不用|"
    r"否(?=[：:;；|｜，,。.!?\s]|$)",
    re.IGNORECASE,
)

_SPANISH_NONPOLARITY_IDIOMS = re.compile(
    r"\bsin\s+(?:esfuerzo|complicaciones?|problemas?|preocupaciones?|estr[eé]s|fin|dificultad(?:es)?|importar|duda|parar)\b|"
    r"\bsin\s+embargo\b|\ba\s+lo\s+mejor\s+no\b|\bno\s+te\s+preocupes\b|"
    r"\bno\s+te\s+olvides\s+de\b|\bno\s+lo\s+olvides\b|"
    r"\bno\s+tienes\s+por\s+qu[eé]\s+aburrirte\b|\bno\s+importa\s+qu[eé]\b|"
    r"\bel\s+techo\s+tampoco\s+se\s+escapar[aá]\s+de\s+la\s+limpieza\b|"
    r"\b(?:brilla|brillar|brille|luce|lucir)\s+como\s+nunca\b|"
    r"\bnunca\s+(?:ha|hab[ií]a)\s+sido\s+tan\s+f[aá]cil\b|\bno\s+(?:es|son)\s+solo\b|"
    r"\ben\s+nada\s+de\s+tiempo\b|\bnada\s+m[aá]s\s+(?:llegar|entrar|salir)\b|"
    r"\bnada\s+menos\s+que\b|"
    r"\bno\s+tiene\s+nada\s+que\s+envidiar\b|\bno\s+hay\s+nada\s+mejor\s+que\b|"
    r"\bno\s+pasa\s+nada\b|\bno\s+querr[aá]s\s+(?:utilizar\s+)?otr[oa]s?\b|"
    r"\bno\s+olvides\s+compartir(?:los|las)?\b|\bnunca\s+perder[aá]s\s+nada\b|"
    r"\bnunca\s+se\s+tienen\s+suficientes\b|"
    r"\bno\s+pueden?\s+faltar\b|\bno\s+deber[ií]a\s+faltar\b|\bno\s+te\s+costar[aá]\s+nada\b|"
    r"\bno\s+te\s+quedes\s+sin\s+asientos\b|"
    r"\bno\s+hay\s+problema\b|"
    r"\bsin\s+asfaltar\b|\bmetales?\s+no\s+ferrosos?\b|\bno\s+worries\b|"
    r"\bno\s+lo\s+(?:utilices|uses)\b|"
    r"\bno\s+te\s+(?:(?:lo|la|los|las)\s+)?pierdas\b|\bno\s+sea\s+tu\s+tarea\s+favorita\b|"
    r"\bno\s+te\s+apetece\s+lavar\s+(?:los?\s+)?platos?(?:\s+ni\s+cubiertos?)?\b|"
    r"\bno\s+quieres?\s+lavar\s+(?:los?\s+)?platos?\b|"
    r"\bno\s+sabes\s+qu[eé]\s+hacer\s+con\s+(?:tu\s+)?cabello\b|"
    r"\bno\s+sabes\s+qu[eé]\s+hacer\s+con\s+una\s+mancha\b|"
    r"\bno\s+(?:ya\s+)?podr[aá]s\s+parar\s+de\s+sonre[ií]r\b|"
    r"\bno\s+te\s+importar[aá]\s+salir\s+de\s+la\s+cama\b|"
    r"\bno\s+dudes?\s+en\b|\bno\s+querr[aá]s\s+renunciar\s+a\s+[eé]l\b|"
    r"\bno\s+te\s+aburrir[aá]s\s+nunca\b|\bno\s+querr[aá]s\s+quit[aá]rtelos\s+nunca\b|"
    r"\bno\s+te\s+gustar[ií]a\s+hacer\b|\bno\s+es\s+genial\s+(?:hacer|que)\b|"
    r"\bquien\s+no\s+lo\s+querr[ií]a\b|\bno\s+te\s+lo\s+imaginas\b|"
    r"\bno\s+tiene\s+nada\s+que\s+(?:envidiar|enviar)\s+(?:a|de)\b|"
    r"\bno\s+es\s+gloria\s+del\s+pasado\b|"
    r"\bpor\s+si\s+no\s+te\s+apetece\s+estar\s+rellen[aá]ndola\s+constantemente\b|"
    r"\bprocura\s+no\s+comerte\s+todas\s+las\s+galletas\s+de\s+una\s+sentada\b|"
    r"\bno\s+son\s+im[aá]genes\s+impresionantes,?\s+sino\s+im[aá]genes\s+de\s+p[ií]xeles\b|"
    r"\bno\s+sabes?\s+qu[eé]\s+regalar\b|"
    r"\bno\s+te\s+pueden?\s+faltar\s+estas\s+pr[aá]cticas\s+bolsas\s+organizadoras\b|"
    r"\bno\s+tardar[aá]s?\s+nada\s+en\s+secar\s+la\s+vajilla\s+limpia\b|"
    r"\bno\s+crees?\s+que\s+esta\s+sombra\s+(?:tambi[eé]n\s+)?te\s+quedar[ií]a\s+de\s+maravilla\b|"
    r"\bya\s+no\s+tendr[aá]s\s+que\s+preocuparte\s+por\s+las\s+manchas\s+en\s+tu\s+alfombra\b|"
    r"\bya\s+no\s+tendr[aá]s\s+que\s+preocuparte\s+por\s+el\s+orden\b|"
    r"\bdisfruta\s+sin\s+l[ií]mite\s+con\s+estas\s+pompas\s+de\s+jab[oó]n\b|"
    r"\bpodr[aá]s\s+volver\s+a\s+imprimir\s+sin\s+l[ií]mites\b|"
    r"\bte\s+ayudar[aá]\s+sin\s+l[ií]mite\b|"
    r"\bla\s+forma\s+ideal\s+de\s+mimar\s+tu\s+piel\s+sin\s+tener\s+que\s+salir\s+de\s+casa\b|"
    # Rhetorical preference/choice questions are not product-property claims.
    r"¿\s*no\s+te\s+gust(?:a|an)\b[^?]*\?|"
    r"¿\s*no\s+sabes?\s+lo\s+que\s+quieres?\s+hacer\b[^?]*\?|"
    r"\bsin\s+m[aá]s\b|"
    r"¿\s*no\s+puedes?\b[^?]*\?",
    re.IGNORECASE,
)
_CHINESE_NONPOLARITY_IDIOMS = re.compile(
    r"必不可少|不可或缺|无所不能|无需(?:进行)?(?:任何)?复杂操作|"
    r"是不是很(?:酷|棒|有趣|漂亮|方便|实用|可爱|不错)|"
    r"没有什么比.{0,16}更(?:好|棒|合适|经典)|没有问题|没问题|少不了|不使用时|无论|无忧|别担心|"
    r"别忘(?:了|记)|不要忘记|切勿忘记|不再无聊|不必感到无聊|不用再感到无聊"
)
_SOURCE_ZERO_ALCOHOL = re.compile(r"\b0\s*%\s*(?:de\s+)?alcohol\b", re.IGNORECASE)
_SOURCE_NO_CABLE = re.compile(r"\bsin\s+cables?\b", re.IGNORECASE)
_SOURCE_WIRELESS_ADJECTIVE = re.compile(
    r"\binal[aá]mbric[oa]s?\b|\bTrue\s+Wireless\s+Stereo\b", re.IGNORECASE,
)
_TARGET_WIRELESS = re.compile(r"无线|免插线|无需插线")
_TARGET_NO_ALCOHOL = re.compile(
    r"无酒精|不含酒精(?!\s*[：:]\s*否)|酒精含量\s*(?:为)?\s*0(?:[.%％]|\b)",
)
_TARGET_ZERO_ALCOHOL_PERCENT = re.compile(
    r"(?:0\s*[%％]\s*(?:酒精|乙醇)|(?:酒精|乙醇)\s*0\s*[%％]|"
    r"(?:无酒精|不含酒精).{0,8}0\s*[%％])",
)
_TARGET_ENDED_CONDITION = re.compile(
    r"告别|(?:异味|气味|臭味|不舒适|不适|寒冷|脚冷).{0,8}消失|"
    r"消失.{0,8}(?:异味|气味|臭味|不舒适|不适|寒冷|脚冷)",
)
_SOURCE_STAYS_IN_PLACE = re.compile(r"\bse\s+mantien(?:e|en)\s+(?:bien\s+)?en\s+su\s+(?:sitio|lugar)\b", re.IGNORECASE)
_TARGET_STAYS_IN_PLACE = re.compile(r"不易滑落|不易移位|不易走位|保持(?:稳固)?在位")
_SOURCE_NO_CLUTTER = re.compile(r"\bsin\s+desorden\b", re.IGNORECASE)
_TARGET_NO_CLUTTER = re.compile(r"不凌乱|整洁有序|干净整洁")
_SOURCE_FOLDABLE = re.compile(r"\bplegable\b", re.IGNORECASE)
_TARGET_FOLDABLE_WHEN_STORED = re.compile(r"不用时.{0,8}折叠|不使用时.{0,8}折叠|可折叠收起")
_SOURCE_SELF_HEALING_MAT = re.compile(r"\balfombrilla\s+para\s+recortar\s+autocicatrizante\b", re.IGNORECASE)
_TARGET_SELF_HEALING_MAT = re.compile(r"(?:不易|不会|难以)留下.{0,8}(?:刀痕|切痕)")
_SOURCE_NO_SLEEVE = re.compile(r"\bsin\s+mangas\b", re.IGNORECASE)
_TARGET_NO_SLEEVE = re.compile(r"无袖")
_SOURCE_NO_STRAPS = re.compile(r"\bsin\s+tirantes\b", re.IGNORECASE)
_TARGET_NO_STRAPS = re.compile(r"无肩带|不带肩带")
_SOURCE_HERMETIC = re.compile(r"\bherm[eé]tic\w*\s+al\s+aire\s+y\s+(?:a\s+)?los\s+olores\b", re.IGNORECASE)
_TARGET_HERMETIC = re.compile(r"防漏气|防串味|密封防异味|气密防异味")
_SOURCE_NO_BURN = re.compile(r"\bno\s+se\s+queme\b", re.IGNORECASE)
_TARGET_NO_BURN = re.compile(r"防焦|防止.{0,4}烧焦|不易烧焦")
_SOURCE_ANTI_RUNS = re.compile(r"\b(?:anticarreras|evitar\s+puntos\s+y\s+carreras)\b", re.IGNORECASE)
_TARGET_ANTI_RUNS = re.compile(r"防勾丝|防脱线|防止.{0,6}(?:勾丝|脱线)")
_SOURCE_ANTI_BREAKAGE = re.compile(r"\b(?:antirrotura|evitan\s+la\s+rotura\s+de\s+(?:las?\s+)?medias)\b", re.IGNORECASE)
_SOURCE_INOLORO_YES = re.compile(r"\binoloro\s*:\s*s[ií]\b", re.IGNORECASE)
_SOURCE_NO_SPACE = re.compile(r"\bsin\s+que\s+ocup\w*\s+(?:mucho\s+)?espacio\b", re.IGNORECASE)
_TARGET_NO_SPACE = re.compile(r"不占空间|节省.{0,4}空间|省空间")
_SOURCE_NO_INCLUDED_BATTERIES = re.compile(r"\bpilas?\s+(?:AA|AAA)\s+no\s+incluidas?\b", re.IGNORECASE)
_TARGET_BATTERIES_NOT_INCLUDED = re.compile(r"需另(?:配|购)(?:\s*(?:AA|AAA))?\s*电池|电池(?:需|需要)另(?:配|购)|另配(?:\s*(?:AA|AAA))?\s*电池")
_SOURCE_WITH_OR_WITHOUT_PEDAL = re.compile(r"\bcon\s+o\s+sin\s+pedal\b", re.IGNORECASE)
_TARGET_PEDAL_VARIANTS = re.compile(r"脚踏桶.{0,8}普通垃圾桶|普通垃圾桶.{0,8}脚踏桶|有无脚踏板")
_SOURCE_NEVER_TOO_COLD_OR_HOT = re.compile(r"\bnunca\s+tendr[aá]s\s+los\s+pies\s+demasiado\s+fr[ií]os\s+ni\s+demasiado\s+calientes\b", re.IGNORECASE)
_TARGET_TEMPERATURE_REGULATION = re.compile(r"温度调节|保持(?:双脚)?(?:温暖|舒适)|不冷不热")
_SOURCE_NO_SLIP = re.compile(r"\bno\s+(?:se\s+)?resbal\w*\b", re.IGNORECASE)
_SOURCE_NO_SLIP_ANYTHING = re.compile(r"\bnada\s+se\s+te\s+resbalar[aá]\s+de\s+las\s+manos\b", re.IGNORECASE)
_SOURCE_NO_SLIDE = re.compile(r"\bno\s+se\s+desliz\w*\b", re.IGNORECASE)
_SOURCE_NO_FADING = re.compile(r"\bno\s+se\s+decolor\w*\b", re.IGNORECASE)
_TARGET_NO_FADING = re.compile(r"不(?:易)?褪色|不易变色|不变色")
_TARGET_SLIP_PREVENTION = re.compile(r"防滑|防止.{0,8}(?:滑落|滑动)|减少.{0,8}滑落|不易(?:.{0,8}从手中)?滑落|不滑动")
_SOURCE_NO_STICK = re.compile(r"\b(?:no\s+(?:se\s+)?(?:peg\w*|adhi\w*)|nada\s+se\s+peg\w*)\b", re.IGNORECASE)
_SOURCE_LESS_ADHERENCE = re.compile(r"\bse\s+adhiere\s+menos\s+a\s+la\s+piel\b", re.IGNORECASE)
_TARGET_LESS_ADHERENCE = re.compile(r"不易粘附皮肤|不太粘附皮肤|较少粘附皮肤")
_TARGET_NONSTICK = re.compile(r"不(?:易)?(?:粘|黏)(?:锅|胶|连|附着)?")
_SOURCE_NO_MARKS = re.compile(r"\bsin\s+dejar\s+marcas?\b", re.IGNORECASE)
_TARGET_NO_MARKS = re.compile(r"不易留下.{0,4}痕|不易勒痕|不留勒痕|无(?:水)?痕")
_SOURCE_NO_YELLOWING = re.compile(r"\bno\s+amarillea\b", re.IGNORECASE)
_TARGET_NO_YELLOWING = re.compile(r"不易黄变|不黄变")
_SOURCE_NO_DRYING = re.compile(r"\bno\s+se\s+seca\b", re.IGNORECASE)
_TARGET_NO_DRYING = re.compile(r"不易干(?:裂|涸)?")
_SOURCE_NO_PRINT = re.compile(r"\bsin\s+estampado\b", re.IGNORECASE)
_TARGET_PLAIN_COLOR = re.compile(r"纯色|素色|无印花|不带印花")
_SOURCE_NO_LUMPS = re.compile(r"\b(?:sin\s+grumos|no\s+se\s+apelmaz\w*)\b", re.IGNORECASE)
_TARGET_NO_LUMPS = re.compile(r"不(?:易)?结块|(?:无|去除|筛除|滤除).{0,14}结块")
_SOURCE_NO_STAINING = re.compile(r"\bno\s+manch\w*\b", re.IGNORECASE)
_TARGET_NO_STAINING = re.compile(r"不(?:易)?染色|不(?:留|留下)污渍|不易弄脏")
_SOURCE_NO_SHIFT = re.compile(r"\bya\s+no\s+se\s+desplazar\w*\b", re.IGNORECASE)
_TARGET_NO_SHIFT = re.compile(r"防止.{0,8}(?:滑动|移动|位移)|避免.{0,8}(?:滑动|移动|位移)")
_SOURCE_NO_SEAMS = re.compile(r"\bsin\s+costuras?\b", re.IGNORECASE)
_TARGET_NO_SEAMS = re.compile(r"无缝|无接缝")
_SOURCE_NO_SCENT = re.compile(r"\b(?:sin\s+perfume|inodor[oa]s?)\b", re.IGNORECASE)
_TARGET_NO_SCENT = re.compile(r"无香(?:味|型|精)?|无味|无异味|无气味")
_SOURCE_NO_ARRIVAL = re.compile(r"\bno\s+pueden?\s+llegar\s+(?:a\s+)?los\s+palillos\s+ordinarios\b", re.IGNORECASE)
_TARGET_NO_ARRIVAL = re.compile(r"普通牙签.{0,8}(?:难以|无法|够不到).{0,4}(?:触及|到达|清洁)|(?:难以|无法)清洁.{0,8}普通牙签")
_SOURCE_NO_START = re.compile(r"\bno\s+arranca\b", re.IGNORECASE)
_TARGET_NO_START = re.compile(r"无法启动|不能启动|无法正常启动")
_SOURCE_NO_DIRTY_HANDS = re.compile(r"\bsin\s+tener\s+que\s+ensuciarse\s+las\s+manos\b", re.IGNORECASE)
_TARGET_NO_DIRTY_HANDS = re.compile(r"不易弄脏(?:双手|手)|无需弄脏(?:双手|手)|不弄脏(?:双手|手)")
_SOURCE_NO_IRRITATION = re.compile(r"\bsin\s+irritaciones?\b", re.IGNORECASE)
_TARGET_NO_IRRITATION = re.compile(
    r"(?:减少|避免|防止)(?:[\u3400-\u9fff]{0,8})刺激|不易刺激|无刺激"
)
_SOURCE_NO_PUDDLES = re.compile(r"\bno\s+tengas\s+charcos\b", re.IGNORECASE)
_TARGET_NO_PUDDLES = re.compile(r"(?:减少|避免|防止).{0,8}(?:积水|水洼|水渍)")
_SOURCE_AVOID_WET_SURFACE = re.compile(r"\bevitar?\s+que\s+(?:la\s+)?encimera\s+se\s+moje\b", re.IGNORECASE)
_TARGET_AVOID_WET_SURFACE = re.compile(r"防止台面.{0,8}(?:浸湿|被水弄湿|变湿)")
_SOURCE_NO_DUST = re.compile(r"\bno\s+cojan\s+polvo\b", re.IGNORECASE)
_TARGET_NO_DUST = re.compile(r"防尘|防止.{0,6}(?:落尘|灰尘)|避免.{0,6}灰尘")
_SOURCE_NO_HUMIDITY_PASS = re.compile(r"\bno\s+deja\s+pasar\s+la\s+humedad\b", re.IGNORECASE)
_TARGET_HUMIDITY_BARRIER = re.compile(r"防潮")
_SOURCE_NO_SINGLE_DROP = re.compile(r"\bno\s+deja\s+pasar\s+ni\s+una\s+gota\b", re.IGNORECASE)
_TARGET_NO_SINGLE_DROP = re.compile(r"防水|防漏")
_SOURCE_LEAK_PREVENTION = re.compile(
    r"\b(?:antigoteo|antifugas?|antiderrames?|no\s+gotea|sin\s+fugas?|"
    r"protecci[oó]n\s+contra\s+(?:las?\s+)?fugas?|"
    r"(?:protecci[oó]n|dise[nñ]o|cierre|tapa|tap[oó]n)\s+a\s+prueba\s+de\s+(?:fugas?|derrames?))\b",
    re.IGNORECASE,
)
_TARGET_LEAK_PREVENTION = re.compile(r"防漏|不漏液|不易漏液|不滴漏")
_SOURCE_NO_AIR_WATER_EFFECT = re.compile(
    r"\bno\s+se\s+ver[aá]\s+afectad[oa]\s+por\s+el\s+aire\s+y\s+el\s+agua\b",
    re.IGNORECASE,
)
_TARGET_NO_AIR_WATER_EFFECT = re.compile(r"隔绝空气和水分|阻隔空气和水分|防止空气和水分影响")
_SOURCE_NO_WORRY = re.compile(
    r"\bno\s+(?:te\s+preocupes|tengas\s+que\s+preocuparte|tendr[aá]s\s+que\s+preocuparte)\b|"
    r"\bsin\s+preocupaciones\b",
    re.IGNORECASE,
)
_TARGET_NO_WORRY = re.compile(r"无忧|无需担心|别担心|不必担心|不用担心")
_SOURCE_NO_HAIR_FALL = re.compile(
    r"\bno\s+quieres?\s+que\s+te\s+caigan\s+mechones\s+en\s+la\s+cara\b",
    re.IGNORECASE,
)
_TARGET_NO_HAIR_FALL = re.compile(r"防止.{0,8}(?:碎发|头发|发丝).{0,6}(?:掉|落|垂).{0,8}(?:脸|面)")
_SOURCE_NO_MUCH_NOISE = re.compile(r"\bno\s+hace\s+mucho\s+ruido\b", re.IGNORECASE)
_TARGET_LOW_NOISE = re.compile(r"低噪音|噪音较低|声音较小|声音不大")
_SOURCE_INVISIBLE_WRITING = re.compile(
    r"\bsin\s+que\s+nadie\s+pueda\s+ver(?:lo|los|la|las)\b", re.IGNORECASE,
)
_TARGET_INVISIBLE_WRITING = re.compile(r"隐形墨水|不可见(?:墨水|文字)|看不见(?:的)?(?:墨水|文字)")
_SOURCE_NO_ODOR_PICKUP = re.compile(
    r"\bno\s+coj[ae]\s+olores?\s+indeseados?\b", re.IGNORECASE,
)
_TARGET_NO_ODOR_PICKUP = re.compile(r"(?:避免|防止|不易|不会).{0,8}(?:沾染|吸附|产生|留下)?(?:异味|气味|味道)")
_SOURCE_NO_HAIR_DAMAGE = re.compile(r"\bno\s+da[nñ](?:a|an)\s+(?:el\s+)?cabello\b", re.IGNORECASE)
_TARGET_NO_HAIR_DAMAGE = re.compile(r"不易?损伤头发|不伤(?:害)?头发|不损伤(?:头发|发丝)")
_SOURCE_NO_SKIN_DRYING = re.compile(
    r"\b(?:sin\s+resecar(?:la|las|lo|los)?|no\s+reseca(?:r)?\s+(?:la\s+)?piel)\b", re.IGNORECASE,
)
_TARGET_NO_SKIN_DRYING = re.compile(r"不(?:会)?使皮肤干燥|不(?:会)?使手部?干燥|不(?:会)?干燥")
_SOURCE_NO_BREAK_ON_DROP = re.compile(
    r"\bno\s+se\s+rompen?\s+en\s+caso\s+de\s+ca[ií]da\b", re.IGNORECASE,
)
_TARGET_NO_BREAK_ON_DROP = re.compile(r"不易破损|不易摔坏|防摔(?:破损)?")
_SOURCE_NO_LIFT_PARCHMENT = re.compile(
    r"\b(?:el\s+)?flujo\s+de\s+aire\s+no\s+(?:lo|la)\s+levant\w*\b", re.IGNORECASE,
)
_TARGET_NO_LIFT_PARCHMENT = re.compile(r"(?:避免|防止).{0,10}(?:烘焙纸|纸张|垫纸).{0,8}(?:被)?(?:气流|风).{0,6}(?:吹起|掀起|带起)")
_SOURCE_NO_HEADPHONE_JACK = re.compile(
    r"\bno\s+tienen?\s+la\s+toma\s+de\s+auriculares\s+de\s+3[,.]5\s*mm\b", re.IGNORECASE,
)
_TARGET_USB_C_ONLY = re.compile(r"仅(?:有)?\s*USB\s*[-–]?\s*C|只有\s*USB\s*[-–]?\s*C", re.IGNORECASE)
_SOURCE_NO_OVERHEATING = re.compile(r"\bno\s+se\s+caliente\s+demasiado\b", re.IGNORECASE)
_TARGET_NO_OVERHEATING = re.compile(r"不易过热|不易过度升温|避免过热|防止过热")
_SOURCE_KEYS_OR_PHONE_NOT_EASILY_LOST = re.compile(
    r"\bno\s+perder[aá]s?\s+(?:tus?\s+)?(?:llaves?|llave)\s+tan\s+f[aá]cilmente\b|"
    r"\bno\s+volver[aá]s?\s+a\s+perder\s+(?:el\s+)?(?:m[oó]vil|tel[eé]fono)\b|"
    r"\bser[aá]s?\s+menos\s+propens[oa]\s+a\s+perder(?:lo|la|los|las)\b",
    re.IGNORECASE,
)
_SOURCE_LOSS_CLAIM = re.compile(r"\b(?:perder|perder[aá]s?|perdi[dt][oa]s?|extraviar\w*|desaparecer\w*)\b", re.IGNORECASE)
_TARGET_REDUCED_KEY_LOSS = re.compile(r"减少.{0,4}(?:丢失|遗失)|降低.{0,4}(?:丢失|遗失)|不易丢失")
_SOURCE_HEATLESS_CURLER = re.compile(r"\b(?:rizador(?:es)?|rulos?)\s+(?:de\s+pelo\s+)?sin\s+calor\b", re.IGNORECASE)
_TARGET_HEATLESS_CURLER = re.compile(r"无热卷发器|免加热卷发器|无需加热.{0,4}卷发")
_SOURCE_INFLATE_WITHOUT_HOURS = re.compile(
    r"\bsin\s+tener\s+que\s+pasarte\s+horas\s+infl[aá]ndolos\s+a\s+mano\b", re.IGNORECASE,
)
_TARGET_QUICK_INFLATION = re.compile(r"(?:快速|迅速|省时).{0,6}充气|充气.{0,6}(?:快速|迅速|省时)")
_SOURCE_NO_BRUSHING_TOO_LONG = re.compile(
    r"\bnunca\s+te\s+los\s+cepillar[aá]s?\s+demasiado\s+tiempo\b", re.IGNORECASE,
)
_TARGET_NO_BRUSHING_TOO_LONG = re.compile(r"避免.{0,6}刷牙.{0,6}(?:过长|太久|过久)|防止.{0,6}刷牙.{0,6}(?:过长|太久|过久)")
_SOURCE_NO_MIXING = re.compile(r"\bsin\s+mezclar(?:los|las|lo|la)\b", re.IGNORECASE)
_TARGET_SEPARATE_FOODS = re.compile(r"分开(?:存放|放置|盛放)|避免.{0,6}混合|不(?:会)?混(?:在一起|合)")
_SOURCE_NO_MOISTURE_ANTIHUMEDAD = re.compile(r"\bsin\s+humedad\s+con\s+este\s+antihumedad\b", re.IGNORECASE)
_TARGET_KEEP_DRY = re.compile(r"保持.{0,4}(?:干燥|干爽)|维持.{0,4}(?:干燥|干爽)")
_SOURCE_NO_MESS = re.compile(r"\bsin\s+ensuciarlo\s+todo\b", re.IGNORECASE)
_TARGET_NO_MESS = re.compile(r"不易弄脏|无需弄脏|不会弄脏|保持.{0,4}干净")
_SOURCE_UNCLASSIFIED = re.compile(r"\bsin\s+clasificaci[oó]n\b", re.IGNORECASE)
_TARGET_UNCLASSIFIED = re.compile(r"未(?:分级|分类)|未作分类")
_SOURCE_NO_WORRY_ABOUT_PAINT = re.compile(
    r"\bsin\s+preocuparte\s+por\s+las\s+manchas\s+de\s+pintura\s+en\s+el\s+suelo\b", re.IGNORECASE,
)
_TARGET_PREVENT_PAINT_STAINS_FLOOR = re.compile(r"防止.{0,8}油漆.{0,8}(?:弄脏|污染).{0,4}(?:地面|地板)|防止.{0,8}(?:地面|地板).{0,8}油漆.{0,8}(?:弄脏|污染)")
_SOURCE_NO_BEND_OR_WET_HANDS = re.compile(
    r"\bno\s+tendr[aá]s?\s+que\s+agacharte\s+ni\s+mojarte\s+las\s+manos\b", re.IGNORECASE,
)
_TARGET_NO_BEND_OR_WET_HANDS = re.compile(r"不弯腰.{0,8}不弄湿双手|无需弯腰.{0,8}(?:无需|不必|不用)弄湿双手")
_SOURCE_PIMPLE_DISAPPEARS = re.compile(
    r"\bgrano\s+m[aá]s\s+peque[nñ]o\s+o\s+incluso\s+sin\s+[eé]l\b", re.IGNORECASE,
)
_TARGET_PIMPLE_DISAPPEARS = re.compile(r"痘痘.{0,6}(?:消失|不见|没有)|(?:消失|不见).{0,6}痘痘")
_SOURCE_NOT_TOO_INTENSE_LIGHT = re.compile(
    r"\bluz\s+brillante,?\s+pero\s+no\s+demasiado\s+intensa\b", re.IGNORECASE,
)
_TARGET_NOT_TOO_INTENSE_LIGHT = re.compile(r"不过于刺眼|不刺眼|不过亮|亮而不刺眼")
_SOURCE_UNWANTED_HAIR = re.compile(r"\bpelo\s+no\s+deseado\b", re.IGNORECASE)
_TARGET_UNWANTED_HAIR = re.compile(r"多余毛发|不需要的毛发|不想要的毛发")
_SOURCE_UNWANTED_ODOR = re.compile(r"\bolores?\s+no\s+deseados?\b", re.IGNORECASE)
_TARGET_UNWANTED_ODOR = re.compile(r"抑制.{0,4}异味|减少.{0,4}异味|避免.{0,4}异味|去除.{0,4}异味")
_SOURCE_ODOR_CONTROL = re.compile(
    r"\b(?:elimin\w*|combat\w*|previen\w*|preven\w*|neutraliz\w*|evit\w*|control\w*)"
    r"(?:\s+de\s+manera\s+(?:r[aá]pida(?:\s+y\s+sencilla)?|sencilla))?\s+(?:(?:los?|las?)\s+)?"
    r"(?:malos?\s+|desagradables?\s+)?olores?\b|"
    r"\bprotege\s+(?:de|contra)\b.{0,40}\bmalos?\s+olores?\b|"
    r"\bcontrol\s+de\s+olores?\b",
    re.IGNORECASE,
)
_TARGET_ODOR_CONTROL = re.compile(r"(?:抑制|减少|避免|去除|消除|防止|预防|中和).{0,10}(?:异味|臭味|恶臭|出汗)")
_SOURCE_NEGATED_ODOR_CONTROL = re.compile(
    r"\bno\s+(?:elimin\w*|neutraliz\w*|preven\w*|evit\w*|control\w*)"
    r"[^.!?\n]{0,90}\b(?:malos?\s+olores?|olores?\s+desagradables?)\b",
    re.IGNORECASE,
)
_SOURCE_STRONG_ODOR_REMOVAL = re.compile(
    r"\b(?:elimin\w*|neutraliz\w*)\b[^.!?\n]{0,90}\b(?:malos?\s+olores?|olores?\s+desagradables?)\b",
    re.IGNORECASE,
)
_SOURCE_ODOR_PREVENTION = re.compile(
    r"\b(?:preven\w*|previen\w*|evit\w*)\b[^.!?\n]{0,90}\b(?:malos?\s+olores?|olores?\s+desagradables?)\b|"
    r"\bprotege\s+(?:de|contra)\b[^.!?\n]{0,60}\bmalos?\s+olores?\b",
    re.IGNORECASE,
)
_TARGET_WEAK_ODOR_REDUCTION = re.compile(r"(?:减少|降低|抑制).{0,8}(?:异味|臭味|恶臭)")
_TARGET_STRONG_ODOR_REMOVAL = re.compile(r"(?:去除|清除|消除|中和).{0,8}(?:异味|臭味|恶臭)")
_TARGET_ODOR_PREVENTION = re.compile(r"(?:防止|预防|避免).{0,8}(?:异味|臭味|恶臭)")
_SOURCE_NO_POCKET = re.compile(
    r"\bsin\s+tener\s+que\s+meter(?:lo|\s+(?:el\s+)?(?:tel[eé]fono|m[oó]vil))\s+en\s+los\s+bolsillos\b",
    re.IGNORECASE,
)
_TARGET_PHONE_FREE_HANDS = re.compile(r"腾出双手|解放双手|免手持")
_SOURCE_NO_DISTRACTION = re.compile(r"\bnada\s+te\s+distraer[aá]\s+de\s+tu\s+entrenamiento\b", re.IGNORECASE)
_TARGET_SOCKS_STAY_UP = re.compile(r"袜子.{0,8}不易.{0,8}下滑|不易在活动中下滑")
_SOURCE_AVOID_UNNECESSARY_PLASTIC_BAGS = re.compile(
    r"\bevitar[aá]s\s+el\s+uso\s+innecesario\s+de\s+bolsas\s+de\s+pl[aá]stico\b",
    re.IGNORECASE,
)
_TARGET_REDUCED_PLASTIC_BAG_USE = re.compile(
    r"减少.{0,12}(?:一次性)?塑料袋(?:的)?使用|减少使用.{0,8}塑料袋|减少塑料袋使用"
)
_SOURCE_GREASE_FREE = re.compile(r"\bsin\s+grasa\b", re.IGNORECASE)
_TARGET_GREASE_FREE = re.compile(r"防油|抗油|不沾油")
_SOURCE_INVISIBLE_WITH_SHOES = re.compile(
    r"\binvisibles?\s+con\s+(?:(?:las?|tus?)\s+)?(?:zapatillas|zapatos)\b|"
    r"\bno\s+se\s+ven?\s+en\s+(?:(?:el|la|los|las)\s+)?(?:zapatilla|zapatillas|zapato|zapatos)\b|"
    r"\bno\s+se\s+ver[aá]n?\s+con\s+(?:(?:tus?|los?|las?)\s+)?(?:zapatillas|zapatos)\b",
    re.IGNORECASE,
)
_TARGET_INVISIBLE_WITH_SHOES = re.compile(r"不易露出|(?:袜口|袜子).{0,4}(?:不外露|不显露)|穿鞋后不显露")
_SOURCE_NOT_PROTRUDE = re.compile(
    r"\bno\s+(?:se\s+)?sobresalen?(?:\s+por\s+encima)?\s+(?:de|del)\s+"
    r"(?:(?:la|el|los|las)\s+)?(?:zapatilla|zapatillas|zapato|zapatos|calzado)\b",
    re.IGNORECASE,
)
_SOURCE_OPAQUE = re.compile(r"\bopac[oa]s?\b", re.IGNORECASE)
_TARGET_OPAQUE = re.compile(r"不透明")

# A narrowly scoped source-bound claim check. A vegan formula does not imply
# sulfate-free, silicone-free, paraben-free, or any other free-from property.
# Keep the first rule limited to the confirmed sulfate hallucination; expand
# only when another source/target pair is supported by corpus evidence.
_SOURCE_SULFATE_FREE = re.compile(
    r"\b(?:sin|libre\s+de|no\s+contiene|no\s+contienen|sin\s+contenido\s+de)\s+"
    r"(?:sulfat\w*|SLS|SLES)\b",
    re.IGNORECASE,
)
_TARGET_SULFATE_FREE = re.compile(
    r"(?:无|不含|不添加|未添加)\s*(?:硫酸盐|SLS|SLES)|"
    r"(?:硫酸盐|SLS|SLES)\s*(?:零添加|free)",
    re.IGNORECASE,
)


def _has_source_negation(value: str) -> bool:
    """Detect factual Spanish negation, excluding common positive idioms/questions."""
    normalized = _SPANISH_NONPOLARITY_IDIOMS.sub(" ", str(value or ""))
    return bool(
        _SOURCE_NEGATION.search(normalized)
        or _SOURCE_INVISIBLE_WITH_SHOES.search(normalized)
    )


def _has_target_negation_for_source(source: str, target: str) -> bool:
    """Recognize a few source-bound functional paraphrases of negation."""

    target_text = str(target or "")
    source_text = str(source or "")
    # "No perderás las llaves tan fácilmente" is a hedged prevention claim;
    # Chinese commonly renders it as "减少钥匙丢失". Do not generalize this
    # weaker paraphrase to an absolute "nunca más" claim (e.g. a suitcase).
    if _TARGET_REDUCED_KEY_LOSS.search(target_text):
        return bool(_SOURCE_KEYS_OR_PHONE_NOT_EASILY_LOST.search(source_text))
    if _SOURCE_OPAQUE.search(source_text):
        target_text = _TARGET_OPAQUE.sub(" ", target_text)
    if _SOURCE_NO_INCLUDED_BATTERIES.search(source_text) and _TARGET_BATTERIES_NOT_INCLUDED.search(target_text):
        return True
    # ``无线`` conveys a cable-free property. It can be supported by either
    # Spanish ``sin cable(s)`` or the positive adjective ``inalámbrico``;
    # never treat it as a universal synonym for a source negation.
    if _TARGET_WIRELESS.search(target_text):
        return True
    if _SOURCE_WITH_OR_WITHOUT_PEDAL.search(source_text) and _TARGET_PEDAL_VARIANTS.search(target_text):
        return True
    if _SOURCE_NEVER_TOO_COLD_OR_HOT.search(source_text) and _TARGET_TEMPERATURE_REGULATION.search(target_text):
        return True
    if _SOURCE_NO_SPACE.search(source_text) and _TARGET_NO_SPACE.search(target_text):
        return True
    if _SOURCE_NO_HUMIDITY_PASS.search(source_text) and _TARGET_HUMIDITY_BARRIER.search(target_text):
        return True
    if _SOURCE_NO_SINGLE_DROP.search(source_text) and _TARGET_NO_SINGLE_DROP.search(target_text):
        return True
    if _SOURCE_LEAK_PREVENTION.search(source_text) and _TARGET_LEAK_PREVENTION.search(target_text):
        return True
    if _SOURCE_NO_AIR_WATER_EFFECT.search(source_text) and _TARGET_NO_AIR_WATER_EFFECT.search(target_text):
        return True
    if _SOURCE_NO_WORRY.search(source_text) and _TARGET_NO_WORRY.search(target_text):
        return True
    if _SOURCE_NO_HAIR_FALL.search(source_text) and _TARGET_NO_HAIR_FALL.search(target_text):
        return True
    if _SOURCE_NO_MUCH_NOISE.search(source_text) and _TARGET_LOW_NOISE.search(target_text):
        return True
    if _SOURCE_INVISIBLE_WRITING.search(source_text) and _TARGET_INVISIBLE_WRITING.search(target_text):
        return True
    if _SOURCE_NO_ODOR_PICKUP.search(source_text) and _TARGET_NO_ODOR_PICKUP.search(target_text):
        return True
    if _SOURCE_NO_HAIR_DAMAGE.search(source_text) and _TARGET_NO_HAIR_DAMAGE.search(target_text):
        return True
    if _SOURCE_NO_SKIN_DRYING.search(source_text) and _TARGET_NO_SKIN_DRYING.search(target_text):
        return True
    if _SOURCE_NO_BREAK_ON_DROP.search(source_text) and _TARGET_NO_BREAK_ON_DROP.search(target_text):
        return True
    if _SOURCE_NO_LIFT_PARCHMENT.search(source_text) and _TARGET_NO_LIFT_PARCHMENT.search(target_text):
        return True
    if _SOURCE_NO_HEADPHONE_JACK.search(source_text) and _TARGET_USB_C_ONLY.search(target_text):
        return True
    if _SOURCE_INFLATE_WITHOUT_HOURS.search(source_text) and _TARGET_QUICK_INFLATION.search(target_text):
        return True
    if _SOURCE_NO_BRUSHING_TOO_LONG.search(source_text) and _TARGET_NO_BRUSHING_TOO_LONG.search(target_text):
        return True
    if _SOURCE_NO_MIXING.search(source_text) and _TARGET_SEPARATE_FOODS.search(target_text):
        return True
    if _SOURCE_NO_MOISTURE_ANTIHUMEDAD.search(source_text) and _TARGET_KEEP_DRY.search(target_text):
        return True
    if _SOURCE_UNCLASSIFIED.search(source_text) and _TARGET_UNCLASSIFIED.search(target_text):
        return True
    if _SOURCE_NO_MESS.search(source_text) and _TARGET_NO_MESS.search(target_text):
        return True
    if _SOURCE_NO_WORRY_ABOUT_PAINT.search(source_text) and _TARGET_PREVENT_PAINT_STAINS_FLOOR.search(target_text):
        return True
    if _SOURCE_NO_BEND_OR_WET_HANDS.search(source_text) and _TARGET_NO_BEND_OR_WET_HANDS.search(target_text):
        return True
    if _SOURCE_PIMPLE_DISAPPEARS.search(source_text) and _TARGET_PIMPLE_DISAPPEARS.search(target_text):
        return True
    if _SOURCE_NOT_TOO_INTENSE_LIGHT.search(source_text) and _TARGET_NOT_TOO_INTENSE_LIGHT.search(target_text):
        return True
    if _SOURCE_UNWANTED_HAIR.search(source_text) and _TARGET_UNWANTED_HAIR.search(target_text):
        return True
    if _SOURCE_UNWANTED_ODOR.search(source_text) and _TARGET_UNWANTED_ODOR.search(target_text):
        return True
    if (
        _SOURCE_ODOR_CONTROL.search(source_text)
        and not _SOURCE_NEGATED_ODOR_CONTROL.search(source_text)
        and _TARGET_ODOR_CONTROL.search(target_text)
    ):
        return True
    if _SOURCE_NO_OVERHEATING.search(source_text) and _TARGET_NO_OVERHEATING.search(target_text):
        return True
    if _SOURCE_HEATLESS_CURLER.search(source_text) and _TARGET_HEATLESS_CURLER.search(target_text):
        return True
    if _SOURCE_NO_POCKET.search(source_text) and _TARGET_PHONE_FREE_HANDS.search(target_text):
        return True
    if _SOURCE_NO_DISTRACTION.search(source_text) and _TARGET_SOCKS_STAY_UP.search(target_text):
        return True
    if (
        _SOURCE_AVOID_UNNECESSARY_PLASTIC_BAGS.search(source_text)
        and _TARGET_REDUCED_PLASTIC_BAG_USE.search(target_text)
    ):
        return True
    if _TARGET_NEGATION.search(target_text):
        return True
    if _SOURCE_ZERO_ALCOHOL.search(source_text) and _TARGET_ZERO_ALCOHOL_PERCENT.search(target_text):
        return True
    if _SOURCE_NO_SLEEVE.search(source_text) and _TARGET_NO_SLEEVE.search(target_text):
        return True
    if _SOURCE_NO_STRAPS.search(source_text) and _TARGET_NO_STRAPS.search(target_text):
        return True
    if re.search(r"\bse\s+acab(?:ó|aron)\b", source_text, re.IGNORECASE) and _TARGET_ENDED_CONDITION.search(target_text):
        return True
    if _SOURCE_NO_SLIP.search(source_text) and _TARGET_SLIP_PREVENTION.search(target_text):
        return True
    if _SOURCE_NO_SLIP_ANYTHING.search(source_text) and _TARGET_SLIP_PREVENTION.search(target_text):
        return True
    if _SOURCE_NO_CLUTTER.search(source_text) and _TARGET_NO_CLUTTER.search(target_text):
        return True
    if _SOURCE_NO_SLIDE.search(source_text) and _TARGET_SLIP_PREVENTION.search(target_text):
        return True
    if _SOURCE_NO_FADING.search(source_text) and _TARGET_NO_FADING.search(target_text):
        return True
    if _SOURCE_NO_SPACE.search(source_text) and _TARGET_NO_SPACE.search(target_text):
        return True
    if _SOURCE_NO_INCLUDED_BATTERIES.search(source_text) and _TARGET_BATTERIES_NOT_INCLUDED.search(target_text):
        return True
    if _SOURCE_WITH_OR_WITHOUT_PEDAL.search(source_text) and _TARGET_PEDAL_VARIANTS.search(target_text):
        return True
    if _SOURCE_NEVER_TOO_COLD_OR_HOT.search(source_text) and _TARGET_TEMPERATURE_REGULATION.search(target_text):
        return True
    if _SOURCE_NO_STICK.search(source_text) and _TARGET_NONSTICK.search(target_text):
        return True
    if _SOURCE_NO_MARKS.search(source_text) and _TARGET_NO_MARKS.search(target_text):
        return True
    if _SOURCE_NO_YELLOWING.search(source_text) and _TARGET_NO_YELLOWING.search(target_text):
        return True
    if _SOURCE_NO_DRYING.search(source_text) and _TARGET_NO_DRYING.search(target_text):
        return True
    if _SOURCE_NO_PRINT.search(source_text) and _TARGET_PLAIN_COLOR.search(target_text):
        return True
    if _SOURCE_NO_LUMPS.search(source_text) and _TARGET_NO_LUMPS.search(target_text):
        return True
    if _SOURCE_NO_SHIFT.search(source_text) and _TARGET_NO_SHIFT.search(target_text):
        return True
    if _SOURCE_NO_SEAMS.search(source_text) and _TARGET_NO_SEAMS.search(target_text):
        return True
    if _SOURCE_NO_SCENT.search(source_text) and _TARGET_NO_SCENT.search(target_text):
        return True
    if _SOURCE_NO_ARRIVAL.search(source_text) and _TARGET_NO_ARRIVAL.search(target_text):
        return True
    if _SOURCE_NO_START.search(source_text) and _TARGET_NO_START.search(target_text):
        return True
    if _SOURCE_NO_DIRTY_HANDS.search(source_text) and _TARGET_NO_DIRTY_HANDS.search(target_text):
        return True
    if _SOURCE_NO_IRRITATION.search(source_text) and _TARGET_NO_IRRITATION.search(target_text):
        return True
    if _SOURCE_NO_PUDDLES.search(source_text) and _TARGET_NO_PUDDLES.search(target_text):
        return True
    if _SOURCE_AVOID_WET_SURFACE.search(source_text) and _TARGET_AVOID_WET_SURFACE.search(target_text):
        return True
    if _SOURCE_NO_DUST.search(source_text) and _TARGET_NO_DUST.search(target_text):
        return True
    if _SOURCE_GREASE_FREE.search(source_text) and _TARGET_GREASE_FREE.search(target_text):
        return True
    if _SOURCE_INVISIBLE_WITH_SHOES.search(source_text) and _TARGET_INVISIBLE_WITH_SHOES.search(target_text):
        return True
    if _SOURCE_NOT_PROTRUDE.search(source_text) and _TARGET_INVISIBLE_WITH_SHOES.search(target_text):
        return True
    return False


def _has_source_supported_negative_paraphrase(source: str, target: str) -> bool:
    """Accept narrow target-side negative phrasing explicitly supported by this field."""

    source_text = str(source or "")
    target_text = str(target or "")
    return bool(
        (_SOURCE_STAYS_IN_PLACE.search(source_text) and _TARGET_STAYS_IN_PLACE.search(target_text))
        or (_SOURCE_FOLDABLE.search(source_text) and _TARGET_FOLDABLE_WHEN_STORED.search(target_text))
        or (_SOURCE_SELF_HEALING_MAT.search(source_text) and _TARGET_SELF_HEALING_MAT.search(target_text))
        or (_SOURCE_HERMETIC.search(source_text) and _TARGET_HERMETIC.search(target_text))
        or (_SOURCE_NO_BURN.search(source_text) and _TARGET_NO_BURN.search(target_text))
        or (_SOURCE_ANTI_RUNS.search(source_text) and _TARGET_ANTI_RUNS.search(target_text))
        or (_SOURCE_ANTI_BREAKAGE.search(source_text) and _TARGET_ANTI_RUNS.search(target_text))
        or (
            _SOURCE_INOLORO_YES.search(source_text)
            and re.search(r"(?:无异味|无味|无香)\s*[：:]\s*是", target_text)
        )
        or (_SOURCE_NO_CLUTTER.search(source_text) and _TARGET_NO_CLUTTER.search(target_text))
        or (_SOURCE_NO_WORRY.search(source_text) and _TARGET_NO_WORRY.search(target_text))
        or (_SOURCE_NO_MUCH_NOISE.search(source_text) and _TARGET_LOW_NOISE.search(target_text))
        or (_SOURCE_INVISIBLE_WRITING.search(source_text) and _TARGET_INVISIBLE_WRITING.search(target_text))
        or (_SOURCE_NO_ODOR_PICKUP.search(source_text) and _TARGET_NO_ODOR_PICKUP.search(target_text))
        or (_SOURCE_NO_HAIR_DAMAGE.search(source_text) and _TARGET_NO_HAIR_DAMAGE.search(target_text))
        or (_SOURCE_NO_SKIN_DRYING.search(source_text) and _TARGET_NO_SKIN_DRYING.search(target_text))
        or (_SOURCE_NO_BREAK_ON_DROP.search(source_text) and _TARGET_NO_BREAK_ON_DROP.search(target_text))
        or (_SOURCE_NO_LIFT_PARCHMENT.search(source_text) and _TARGET_NO_LIFT_PARCHMENT.search(target_text))
        or (_SOURCE_NO_HEADPHONE_JACK.search(source_text) and _TARGET_USB_C_ONLY.search(target_text))
        or (_SOURCE_INFLATE_WITHOUT_HOURS.search(source_text) and _TARGET_QUICK_INFLATION.search(target_text))
        or (_SOURCE_NO_BRUSHING_TOO_LONG.search(source_text) and _TARGET_NO_BRUSHING_TOO_LONG.search(target_text))
        or (_SOURCE_NO_MIXING.search(source_text) and _TARGET_SEPARATE_FOODS.search(target_text))
        or (_SOURCE_NO_MOISTURE_ANTIHUMEDAD.search(source_text) and _TARGET_KEEP_DRY.search(target_text))
        or (_SOURCE_UNCLASSIFIED.search(source_text) and _TARGET_UNCLASSIFIED.search(target_text))
        or (_SOURCE_NO_MESS.search(source_text) and _TARGET_NO_MESS.search(target_text))
        or (_SOURCE_NO_WORRY_ABOUT_PAINT.search(source_text) and _TARGET_PREVENT_PAINT_STAINS_FLOOR.search(target_text))
        or (_SOURCE_NO_BEND_OR_WET_HANDS.search(source_text) and _TARGET_NO_BEND_OR_WET_HANDS.search(target_text))
        or (_SOURCE_PIMPLE_DISAPPEARS.search(source_text) and _TARGET_PIMPLE_DISAPPEARS.search(target_text))
        or (_SOURCE_NOT_TOO_INTENSE_LIGHT.search(source_text) and _TARGET_NOT_TOO_INTENSE_LIGHT.search(target_text))
        or (_SOURCE_UNWANTED_HAIR.search(source_text) and _TARGET_UNWANTED_HAIR.search(target_text))
        or (_SOURCE_UNWANTED_ODOR.search(source_text) and _TARGET_UNWANTED_ODOR.search(target_text))
        or (
            _SOURCE_ODOR_CONTROL.search(source_text)
            and not _SOURCE_NEGATED_ODOR_CONTROL.search(source_text)
            and _TARGET_ODOR_CONTROL.search(target_text)
        )
        or (_SOURCE_NO_STAINING.search(source_text) and _TARGET_NO_STAINING.search(target_text))
        or (_SOURCE_NO_OVERHEATING.search(source_text) and _TARGET_NO_OVERHEATING.search(target_text))
        or (_SOURCE_HEATLESS_CURLER.search(source_text) and _TARGET_HEATLESS_CURLER.search(target_text))
        or (_SOURCE_KEYS_OR_PHONE_NOT_EASILY_LOST.search(source_text) and _TARGET_REDUCED_KEY_LOSS.search(target_text))
        or (_SOURCE_LESS_ADHERENCE.search(source_text) and _TARGET_LESS_ADHERENCE.search(target_text))
    )


def _has_unsupported_negative_attribute(source: str, target: str) -> bool:
    """Reject a specific free-from claim when same-field Spanish lacks it."""

    return bool(
        _TARGET_SULFATE_FREE.search(str(target or ""))
        and not _SOURCE_SULFATE_FREE.search(str(source or ""))
    )

# A bare Spanish article must never generally be treated as a number.  The
# following narrow pattern is only for an explicit singular *quantity* phrase
# (for example ``un bote de espray``).  It can match a faithful Chinese
# container rendering such as ``一罐`` or ``1罐`` without weakening ordinary
# same-field numeric checks.
SPANISH_SINGLE_QUANTITY = re.compile(
    r"\b(?:un|una)\s+(?P<noun>bote|lata|botella|caja|bolsa|tubo|rollo|paquete|pack|pieza|unidad|par|juego|set|frasco|tarro|cápsula|capsula|pastilla|cinta|calcet[ií]n)\b",
    re.IGNORECASE,
)
SPANISH_QUANTITY_TO_CHINESE_MEASURES = {
    "bote": ("罐", "瓶"), "lata": ("罐",), "botella": ("瓶",),
    "caja": ("盒",), "bolsa": ("袋", "包"), "tubo": ("管",),
    "rollo": ("卷",), "paquete": ("包",), "pack": ("包",),
    "pieza": ("件", "个"), "unidad": ("件", "个"), "par": ("双", "对"),
    "juego": ("套",), "set": ("套",), "frasco": ("罐", "瓶"),
    "tarro": ("罐", "瓶"), "cápsula": ("粒", "颗"), "capsula": ("粒", "颗"),
    "pastilla": ("片", "粒"), "cinta": ("条",), "calcetín": ("只", "双"), "calcetin": ("只", "双"),
}
SPANISH_CARDINAL_VALUES = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}
SPANISH_NUMBER_QUANTITY = re.compile(
    rf"\b(?P<num>{_SPANISH_NUMBER_WORD})\s+(?:solo\s+)?(?P<noun>"
    r"piezas?|unidades?|accesorios?|niveles?|metros?|kilogramos?|gramos?|litros?|"
    r"botones?|farolillos?|lámparas?|posiciones?|camisetas?|tipos?|"
    r"animal(?:es)?|muñeca(?:s)?|dispositivo(?:s)?|puntas?|lados?|plazas?|personas?|"
    r"intensidades?|zoo|bolsillos?)\b",
    re.IGNORECASE,
)
SPANISH_PERSON_SIZE_QUANTITY = re.compile(
    rf"\b(?P<num>\d+|{_SPANISH_NUMBER_WORD})\s+(?P<noun>plazas?|personas?)\b",
    re.IGNORECASE,
)
SPANISH_UNIVERSAL_SIZE = re.compile(
    r"\b1\s+tamañ[oa]\b[^.\n]{0,40}\bvale\s+para\s+todos\b",
    re.IGNORECASE,
)
SPANISH_OTHER_QUANTITY = re.compile(r"\botro\s+(?:lateral|bolsillo)\b", re.IGNORECASE)
# A common product-description construction enumerates two variants without
# an Arabic cardinal (``uno ... y el otro ...``). Chinese translations often
# make both referents explicit as ``一条...一条...``. Treat that pair as two
# source-supported singular mentions; this is intentionally narrower than
# accepting arbitrary Chinese articles.
SPANISH_PAIRED_SINGULAR_ITEMS = re.compile(
    r"\b(?:uno|una)\b[^.\n]{0,120}\b(?:y\s+)?(?:el\s+otro|la\s+otra)\b",
    re.IGNORECASE,
)
SPANISH_SPECIAL_NUMBER_QUANTITY = re.compile(
    rf"\b(?P<num>{_SPANISH_NUMBER_WORD})\s+(?:solo\s+juego|cómoda\s+camiseta)\b",
    re.IGNORECASE,
)
CHINESE_NUMBER_QUANTITY = re.compile(
    r"(?P<num>[一二两三四五六七八九十])\s*(?:个|件|套|档|米|千克|克|升|颗|粒|片|盏|只|级|种|罐|瓶|盒|袋|卷|管|包)",
)
CHINESE_CARDINAL_VALUES = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# Chinese translations often render an explicit Spanish digit with a Chinese
# numeral rather than an Arabic digit (``3 en 1`` -> ``三合一``, ``2 unidades``
# -> ``两件`` and ``1 hoja`` -> ``一张``).  Keep this deliberately narrow:
# only numerals next to a quantity/function marker are interpreted, so generic
# prose such as ``一款商品`` or ``一杯饮料`` is not silently treated as a hard
# numeric fact.
CHINESE_NUMERIC_CONTEXT = re.compile(
    r"(?P<num>[零〇一二两三四五六七八九十百千万]+)"
    r"(?=\s*(?:合|倍|包装|装|层|芯|条|瓶|张|件|套|包|只|页|环|端口|位|片|粒|颗|双|对|人|米|克|千克|毫升|升|小时|分钟|秒|度|伏|瓦|毫安时))"
    # Only the functional ``数字合数字`` form is numeric.  A bare
    # ``合+数字`` would misread ordinary prose such as ``适合一顿早餐``.
    r"|(?<=[0-9零〇一二两三四五六七八九十百千万])合(?P<after>[零〇一二两三四五六七八九十百千万]+)"
)


def _chinese_cardinal_value(token: str) -> int | None:
    """Parse the small Chinese cardinal forms used in product quantities."""

    if token in CHINESE_CARDINAL_VALUES:
        return CHINESE_CARDINAL_VALUES[token]
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if token == "十":
        return 10
    if token.startswith("十") and len(token) == 2 and token[1] in digits:
        return 10 + digits[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in digits:
        return digits[token[0]] * 10
    if len(token) == 2 and token[0] in digits and token[1] in digits:
        return digits[token[0]] * 10 + digits[token[1]]
    return None


def chinese_context_numeric_tokens(value: object) -> list[str]:
    """Return numeric values represented by bounded Chinese quantity phrases."""

    text = str(value or "")
    output: list[str] = []
    for match in CHINESE_NUMERIC_CONTEXT.finditer(text):
        token = match.group("num") or match.group("after")
        parsed = _chinese_cardinal_value(token)
        if parsed is not None:
            output.append(str(parsed))
    return output

# These are ordinary Spanish words that should not survive in a Chinese
# field.  Brand/model phrases are removed by ``allowed_brand_phrases`` first;
# this keeps a confirmed brand such as ``La Sonata`` legal while still
# catching a Spanish colour such as ``Antracita``.
SPANISH_TOKENS = {
    "el", "la", "los", "las", "para", "con", "sin", "del", "de", "y", "en",
    "color", "tamaño", "producto", "material", "cantidad", "contenido", "piezas", "juego", "juegos",
    "gramos", "litros", "antracita", "blanco", "blanca", "negro", "negra", "gris",
    "rojo", "roja", "verde", "azul", "amarillo", "amarilla", "rosa", "marrón",
}
ENGLISH_TOKENS = {
    "a", "an", "and", "are", "as", "be", "by", "clean", "contains", "for", "free",
    "from", "in", "of", "on", "or", "package", "paper", "plastics", "product", "the",
    "these", "this", "to", "with", "without", "your",
}
TOKEN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")
_SINGLE_LETTER_TECHNICAL = set("ABCDEFGKLMNOPRSTVWXYZ")


def _is_technical_token(value: str, start: int, end: int, token: str) -> bool:
    """Recognize technical symbols without allowing ordinary English prose."""

    if not token.isupper():
        return False
    if len(token) > 1:
        # Acronyms such as USB/LED/PEFC and standards such as ACEA are valid
        # product facts; they are not English sentences.
        return True
    if token not in _SINGLE_LETTER_TECHNICAL:
        return False
    left = value[start - 1] if start > 0 else ""
    right = value[end] if end < len(value) else ""
    if left.isdigit() or right.isdigit():
        return True
    if "\u3400" <= left <= "\u9fff" or "\u3400" <= right <= "\u9fff":
        return True
    # Vitamin/technical notation may be separated by punctuation, e.g.
    # ``维生素A、D3和C``.  Require nearby CJK context so ``A product`` remains
    # an English residual.
    context = value[max(0, start - 3) : min(len(value), end + 3)]
    return any("\u3400" <= char <= "\u9fff" for char in context)


@dataclass(frozen=True)
class ModelOutputCheck:
    """Result of validating one model output."""

    accepted: bool
    reasons: tuple[str, ...]
    field_reasons: Mapping[str, tuple[str, ...]]


def numeric_tokens(value: object) -> list[str]:
    """Return normalized numeric tokens, retaining duplicate occurrences."""

    # Spanish sources use both comma decimals (``1,5``) and dot thousands
    # separators (``3.680``).  Normalize those locale forms before comparing
    # them with Chinese output, and accept the common OCR apostrophe decimal
    # form (``9'5``) without changing the original fact text.
    text = re.sub(r"(?<=\d)['’](?=\d)", ".", str(value or ""))
    # The trailing 2 in ``mm2`` / ``g/m2`` is a unit exponent, not a second
    # product quantity. Keep the measured value and let unit_tokens() compare
    # the square-unit spelling (e.g. ``16 mm2`` ↔ ``16平方毫米``).
    text = re.sub(
        r"(?<![A-Za-z0-9])([+-]?\d+(?:[.,]\d+)?[ \t]*(?:mm|cm|km|m|gr?[ \t]*/[ \t]*m))2\b",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    tokens: list[str] = []
    for raw in NUMBER.findall(text):
        token = raw.replace(",", ".")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
            token = token.replace(".", "")
        elif "." in token:
            # Decimal formatting may drop insignificant trailing zeroes
            # (e.g. Spanish ``4,0 x 40 mm`` rendered as ``4 × 40 mm``).
            # Compare numeric value, not display precision, while retaining
            # meaningful fractional digits such as 4.05.
            token = token.rstrip("0").rstrip(".")
            if token in {"-0", "+0"}:
                token = "0"
        tokens.append(token)
    return sorted(tokens)


def unit_tokens(value: object) -> list[str]:
    """Return canonical unit tokens, retaining duplicate occurrences."""

    text = str(value or "")
    matches: list[tuple[int, int, str]] = []
    matches.extend(
        (match.start(), match.end(), "gsm")
        for match in _SOURCE_GRAMMAGE_KEY_VALUE.finditer(text)
    )
    for canonical, pattern in _UNIT_PATTERNS:
        matches.extend((match.start(), match.end(), canonical) for match in pattern.finditer(text))
    for canonical, pattern in _SPANISH_WORD_UNIT_PATTERNS:
        matches.extend((match.start(), match.end(), canonical) for match in pattern.finditer(text))
    # Prefer the longest recognized unit span (e.g. gsm over its embedded g)
    # so composite units are not double-counted as a base unit.
    selected: list[tuple[int, int, str]] = []
    for start, end, canonical in sorted(matches, key=lambda item: (-(item[1] - item[0]), item[0], item[2])):
        if any(start < other_end and other_start < end for other_start, other_end, _ in selected):
            continue
        selected.append((start, end, canonical))
    return sorted(canonical for _, _, canonical in selected)


def technical_tokens(value: object) -> list[str]:
    """Return normalized technical/model tokens, retaining duplicates."""

    # These are fixed, unambiguous Chinese renderings of a technical token;
    # translating Bluetooth to 蓝牙 must not be reported as token loss.
    text = str(value or "")
    platform_tokens = [
        canonical for canonical, pattern in _PLATFORM_TOKEN_PATTERNS
        if pattern.search(text)
    ]
    # Function-count phrases can be localized as e.g. ``7合1``. Canonicalize
    # both source and target spellings so the same function fact is compared.
    text = re.sub(
        r"(?<![零〇一二两三四五六七八九十])([零〇一二两三四五六七八九十])\s*合\s*([零〇一二两三四五六七八九十])(?![零〇一二两三四五六七八九十])",
        lambda match: f"{_CHINESE_NUMBER[match.group(1)]}-IN-{_CHINESE_NUMBER[match.group(2)]}",
        text,
    )
    # ``PlayStation 5`` and ``PS5`` are equivalent platform identifiers.
    # Normalize before the generic technical-token scanner while the separate
    # platform ledger still records the compatibility fact.
    text = re.sub(r"\bPlayStation\s*([345])\b", r"PS\1", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!\d)(\d+)\s*合\s*(\d+)(?!\d)", r"\1-IN-\2", text)
    text = re.sub(r"(?<!\d)(\d+)[\s-]*EN[\s-]*(\d+)(?!\d)", r"\1-IN-\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\bALL[\s-]*IN[\s-]*ONE\b", "ALL-IN-1", text, flags=re.IGNORECASE)
    text = re.sub(r"蓝牙", "BLUETOOTH ", text)
    output: list[str] = []
    for match in _TECH_TOKEN.finditer(text):
        token = re.sub(r"[\s-]+", "-", match.group().upper()).rstrip(".")
        if token == "LEDS":
            token = "LED"
        if token == "MM2" and re.search(rf"{_MEASURE_NUMBER}\s*$", text[:match.start()], re.IGNORECASE):
            # ``16 mm2`` is a square-millimetre measurement, not an MM2 model.
            continue
        if token not in _TECH_EXCLUDED:
            output.append(token)
    return sorted([*output, *platform_tokens])


def certification_tokens(
    value: object, *, allow_fairtrade_translation: bool = False,
) -> list[str]:
    """Return recognized sustainability/certification facts in a field.

    Bare ``公平贸易`` is not sufficient evidence of the Fairtrade mark by
    itself.  A caller comparing a target against a source that explicitly
    contains Fairtrade may enable that context-bound translation alias.
    """
    text = str(value or "")
    found = {
        canonical
        for canonical, pattern in _CERTIFICATION_PATTERNS
        if pattern.search(text)
    }
    if allow_fairtrade_translation and re.search(r"公平贸易(?!认证)", text):
        found.add("FAIRTRADE")
    return sorted(found)


def numeric_fact_counters(source: object, prediction: object) -> tuple[Counter[str], Counter[str]]:
    """Return aligned numeric fact counters for one Spanish field and Chinese output.

    Explicit digits are always compared exactly. The only non-digit
    equivalence is a source ``un/una`` followed by a known physical
    container/unit and a matching Chinese ``一/1`` plus the mapped measure.
    A generic article, or ``1`` with a different measure, remains a numeric
    hallucination. This function does not rewrite either value.
    """

    source_text = str(source or "")
    output_text = str(prediction or "")
    expected = Counter(numeric_tokens(source_text))
    actual = Counter(numeric_tokens(output_text))
    for pattern in (SPANISH_NUMBER_QUANTITY, SPANISH_SPECIAL_NUMBER_QUANTITY):
        for match in pattern.finditer(source_text):
            noun = match.groupdict().get("noun", "")
            # In ordinary product prose these singular forms are articles
            # (``una punta fina`` / ``un lado``), not a counted package.
            # Their plural/cardinal forms (``dos puntas`` / ``dos lados``)
            # remain protected as explicit facts.
            if noun.casefold().rstrip("s") in {"punta", "lado", "intensidade"} and match.group("num").casefold() in {"un", "uno", "una"}:
                continue
            # An indefinite singular accessory is an article-like phrase in
            # product prose (``un accesorio``), not a hard count.  Plural and
            # explicit numeric forms remain protected by this guard.
            if noun.casefold() == "accesorio" and match.group("num").casefold() in {"un", "uno", "una"}:
                continue
            expected[str(SPANISH_CARDINAL_VALUES[match.group("num").casefold()])] += 1
    # ``otro bolsillo/lateral`` is an explicit second item in product prose;
    # faithful Chinese often renders it as ``一个...``.
    expected["1"] += sum(1 for _ in SPANISH_OTHER_QUANTITY.finditer(source_text))
    # Likewise, ``uno ... el otro`` explicitly names a pair of variants even
    # though Spanish does not repeat a numeric digit. This only authorizes two
    # singular target mentions for the matched paired construction.
    expected["1"] += 2 * sum(1 for _ in SPANISH_PAIRED_SINGULAR_ITEMS.finditer(source_text))
    for match in CHINESE_NUMBER_QUANTITY.finditer(output_text):
        actual[str(CHINESE_CARDINAL_VALUES[match.group("num")])] += 1
    # ``N支`` is a count only when the same explicit number exists in the
    # source field.  This preserves forms such as ``7 brillos -> 七支`` without
    # treating generic articles like ``一支牙刷`` as a hard quantity.
    for match in re.finditer(r"(?P<num>[一二两三四五六七八九十])\s*支", output_text):
        number = str(CHINESE_CARDINAL_VALUES[match.group("num")])
        if expected[number] > actual[number]:
            actual[number] += 1
    # Reconcile Chinese numeral forms for quantity/function phrases, but only
    # up to the number of explicit source facts.  This prevents a generic
    # Chinese article from becoming an accepted numeric hallucination while
    # allowing faithful forms such as ``三合一`` and ``两条装``.
    quantity_spans = [match.span() for match in CHINESE_NUMBER_QUANTITY.finditer(output_text)]
    for match in CHINESE_NUMERIC_CONTEXT.finditer(output_text):
        if any(match.start() < end and start < match.end() for start, end in quantity_spans):
            continue
        token = match.group("num") or match.group("after")
        parsed = _chinese_cardinal_value(token)
        if parsed is not None:
            actual[str(parsed)] += 1
    # Accept ``N estaciones`` -> ``N季`` only when the season count is
    # explicit in this same source field.  A bare ``cada estación`` is left
    # to semantic review instead of being turned into a hard numeric token.
    for match in re.finditer(
        rf"\b(?P<num>\d+|{_SPANISH_NUMBER_WORD})\s+estaciones?\b", source_text, re.IGNORECASE,
    ):
        raw_number = match.group("num").casefold()
        number = int(raw_number) if raw_number.isdigit() else SPANISH_CARDINAL_VALUES[raw_number]
        chinese_number = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}.get(number)
        if chinese_number and f"{chinese_number}季" in output_text and str(number) not in numeric_tokens(output_text):
            actual[str(number)] += 1
    # Spanish source prose may spell out a temporal quantity (for example
    # ``diez meses``) while Chinese uses Arabic digits (``10个月``). Keep
    # this equivalence bounded to explicit time units; ordinary articles stay
    # outside the numeric ledger.
    for match in SPANISH_WORD_TEMPORAL_QUANTITY.finditer(source_text):
        number = SPANISH_CARDINAL_VALUES[match.group("num").casefold()]
        if str(number) in numeric_tokens(output_text):
            expected[str(number)] += 1
    # Lexical Chinese bed/person counts may encode the number without a
    # numeral character (``1 plaza`` -> ``单人``; ``2 personas`` -> ``双人``).
    # Only accept these equivalents when the same Spanish field states the
    # corresponding plaza/persona quantity.
    for match in SPANISH_PERSON_SIZE_QUANTITY.finditer(source_text):
        raw_number = match.group("num").casefold()
        number = int(raw_number) if raw_number.isdigit() else SPANISH_CARDINAL_VALUES[raw_number]
        lexical_target = {1: "单人", 2: "双人"}.get(number)
        if lexical_target and lexical_target in output_text:
            actual[str(number)] += 1
    if SPANISH_UNIVERSAL_SIZE.search(source_text) and re.search(r"均码|均一码|均一尺码|单一尺码", output_text):
        if actual["1"] == 0:
            actual["1"] += 1
    source_nouns = Counter(match.group("noun").casefold() for match in SPANISH_SINGLE_QUANTITY.finditer(source_text))
    for noun, source_count in source_nouns.items():
        measures = SPANISH_QUANTITY_TO_CHINESE_MEASURES[noun]
        measure_pattern = "|".join(re.escape(measure) for measure in measures)
        arabic = len(re.findall(rf"(?<!\d)1\s*(?:{measure_pattern})", output_text))
        chinese = len(re.findall(rf"一\s*(?:{measure_pattern})", output_text))
        aligned_arabic = min(source_count, arabic)
        aligned_chinese = min(source_count - aligned_arabic, chinese)
        expected["1"] += aligned_arabic + aligned_chinese
        # Both Arabic ``1`` and Chinese ``一`` are already counted by
        # ``numeric_tokens`` / ``CHINESE_NUMBER_QUANTITY`` above.  Do not add
        # the Chinese form a second time here; this mapping only aligns the
        # source article with the existing output count.
    # A source explicitly stating ``0% de alcohol`` is faithfully represented
    # by a same-field ``无酒精/不含酒精`` claim. Consume only the zero tokens
    # belonging to those matched claims; unrelated numbers remain protected.
    if _TARGET_NO_ALCOHOL.search(output_text):
        source_zero_count = len(_SOURCE_ZERO_ALCOHOL.findall(source_text))
        explicit_target_zero_count = len(_TARGET_ZERO_ALCOHOL_PERCENT.findall(output_text))
        mapped_to_phrase_count = max(0, source_zero_count - explicit_target_zero_count)
        expected.subtract(["0"] * mapped_to_phrase_count)
    return expected, actual


def _only_duplicate_numeric_mentions_collapsed(
    expected: Counter[str], actual: Counter[str],
) -> bool:
    """Recognize a faithful translation that states a repeated number once.

    The source may repeat the same number while restating one fact (for
    example, a book's page count in both a bullet and the prose).  Requiring
    the same distinct numeric facts and forbidding any target-side excess
    avoids treating a changed or introduced number as harmless.  Unit and
    technical-token checks remain independent and can still reject a missing
    measurement/model fact.
    """
    if expected == actual or not expected or not actual:
        return False
    expected_positive = {token: count for token, count in expected.items() if count > 0}
    actual_positive = {token: count for token, count in actual.items() if count > 0}
    return (
        set(expected_positive) == set(actual_positive)
        and all(0 < actual_positive[token] <= count for token, count in expected_positive.items())
    )


def _only_duplicate_technical_mentions_collapsed(
    expected: Counter[str], actual: Counter[str],
) -> bool:
    """Accept repeated mentions of identical tokens when every token remains.

    A repeated model/standard name is not a count. All distinct identities must
    remain in the target, and it may not introduce or repeat a token beyond its
    source occurrence count.
    """
    if expected == actual or not expected or not actual:
        return False
    return set(expected) == set(actual) and all(0 < actual[token] <= count for token, count in expected.items())


def _without_brands(value: str, allowed_brand_phrases: Iterable[str]) -> str:
    result = value
    for phrase in sorted((str(item).strip() for item in allowed_brand_phrases if str(item).strip()), key=len, reverse=True):
        result = re.sub(re.escape(phrase), " ", result, flags=re.IGNORECASE)
    return result


def contains_brand_phrase(value: object, phrases: Iterable[str]) -> bool:
    """Detect an exact confirmed brand phrase without matching inside words."""
    text = str(value or "")
    for raw in phrases:
        phrase = str(raw or "").strip()
        if not phrase:
            continue
        pattern = rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _has_spanish_residual(value: str, allowed_brand_phrases: Iterable[str]) -> bool:
    cleaned = _without_brands(value, allowed_brand_phrases)
    return any(token.casefold() in SPANISH_TOKENS for token in TOKEN.findall(cleaned))


def _has_english_residual(value: str, allowed_brand_phrases: Iterable[str]) -> bool:
    cleaned = _without_brands(value, allowed_brand_phrases)
    # Upper-case acronyms (USB/LED/PEFC) and mixed technical model tokens are
    # valid in Chinese exports; ordinary lower-case prose is not.
    for match in TOKEN.finditer(cleaned):
        token = match.group()
        if _is_technical_token(cleaned, match.start(), match.end(), token):
            continue
        if token.casefold() in ENGLISH_TOKENS:
            return True
    return False


def validate_model_output(
    source: Mapping[str, object],
    prediction: Mapping[str, object] | None,
    *,
    expected_fields: Iterable[str] | None = None,
    allowed_brand_phrases: Iterable[str] = (),
) -> ModelOutputCheck:
    """Validate a model result against same-field Spanish source facts.

    A number in ``details`` is never allowed to appear in ``description``:
    comparison is per field, so an extra token in the latter is rejected even
    when that number exists elsewhere in the source object.  The function does
    not fill missing text or rewrite values.
    """

    fields = tuple(expected_fields or source.keys())
    if prediction is None:
        return ModelOutputCheck(False, ("JSON_PARSE",), {})
    if set(prediction) != set(fields) or any(not isinstance(prediction.get(field), str) for field in fields):
        return ModelOutputCheck(False, ("SCHEMA",), {})

    by_field: dict[str, tuple[str, ...]] = {}
    for field in fields:
        reasons: list[str] = []
        source_value = str(source.get(field, "") or "")
        predicted_value = str(prediction.get(field, "") or "")
        if not source_value.strip() and predicted_value.strip():
            # An empty official field is a real NO_SOURCE state.  A value
            # produced from another field is cross-field fact injection, not
            # a harmless completion.
            reasons.append("SOURCE_EMPTY_NONEMPTY")
        elif source_value.strip() and not predicted_value.strip():
            reasons.append("EMPTY_REQUIRED_FIELD")
        expected, actual = numeric_fact_counters(source_value, predicted_value)
        numeric_mentions_collapsed = _only_duplicate_numeric_mentions_collapsed(expected, actual)
        if list((expected - actual).elements()) and not numeric_mentions_collapsed:
            reasons.append("NUMERIC_DROPPED")
        if list((actual - expected).elements()) and not numeric_mentions_collapsed:
            reasons.append("NUMERIC_HALLUCINATED")
        expected_units = Counter(unit_tokens(source_value))
        actual_units = Counter(unit_tokens(predicted_value))
        if _TARGET_NO_ALCOHOL.search(predicted_value):
            source_zero_count = len(_SOURCE_ZERO_ALCOHOL.findall(source_value))
            explicit_target_zero_count = len(_TARGET_ZERO_ALCOHOL_PERCENT.findall(predicted_value))
            mapped_to_phrase_count = max(0, source_zero_count - explicit_target_zero_count)
            expected_units.subtract(
                ["percent"] * mapped_to_phrase_count
            )
        if list((expected_units - actual_units).elements()):
            reasons.append("UNIT_DROPPED")
        if list((actual_units - expected_units).elements()):
            reasons.append("UNIT_HALLUCINATED")
        expected_technical = Counter(technical_tokens(source_value))
        actual_technical = Counter(technical_tokens(predicted_value))
        technical_mentions_collapsed = _only_duplicate_technical_mentions_collapsed(
            expected_technical, actual_technical,
        )
        if list((expected_technical - actual_technical).elements()) and not technical_mentions_collapsed:
            reasons.append("TECH_TOKEN_DROPPED")
        if list((actual_technical - expected_technical).elements()):
            reasons.append("TECH_TOKEN_HALLUCINATED")
        expected_certifications = Counter(certification_tokens(source_value))
        actual_certifications = Counter(certification_tokens(
            predicted_value, allow_fairtrade_translation="FAIRTRADE" in expected_certifications,
        ))
        if list((expected_certifications - actual_certifications).elements()):
            reasons.append("CERTIFICATION_DROPPED")
        if list((actual_certifications - expected_certifications).elements()):
            reasons.append("CERTIFICATION_HALLUCINATED")
        if _INTERNAL_REVIEW_NOTE.search(predicted_value):
            reasons.append("INTERNAL_QA_NOTE_LEAKED")
        source_has_negation = _has_source_negation(source_value)
        # `是否` is an interrogative construction, not itself a negative claim.
        target_polarity_text = predicted_value.replace("是否", "")
        target_polarity_text = _CHINESE_NONPOLARITY_IDIOMS.sub(" ", target_polarity_text)
        target_has_negation = _has_target_negation_for_source(source_value, target_polarity_text)
        if source_has_negation and not target_has_negation:
            reasons.append("NEGATION_DROPPED")
        elif (
            target_has_negation
            and not source_has_negation
            and not _has_source_supported_negative_paraphrase(source_value, predicted_value)
            and not (_TARGET_WIRELESS.search(target_polarity_text) and _SOURCE_WIRELESS_ADJECTIVE.search(source_value))
            and not (_SOURCE_LEAK_PREVENTION.search(source_value) and _TARGET_LEAK_PREVENTION.search(predicted_value))
        ):
            reasons.append("NEGATION_HALLUCINATED")
        if (
            _TARGET_REDUCED_KEY_LOSS.search(predicted_value)
            and not _SOURCE_LOSS_CLAIM.search(source_value)
        ):
            reasons.append("NEGATION_HALLUCINATED")
        if (
            _SOURCE_NEGATED_ODOR_CONTROL.search(source_value)
            and _TARGET_ODOR_CONTROL.search(predicted_value)
            and "NEGATION_HALLUCINATED" not in reasons
        ):
            reasons.append("NEGATION_HALLUCINATED")
        if (
            _TARGET_WEAK_ODOR_REDUCTION.search(predicted_value)
            and not _TARGET_STRONG_ODOR_REMOVAL.search(predicted_value)
            and (
                _SOURCE_STRONG_ODOR_REMOVAL.search(source_value)
                or _SOURCE_ODOR_PREVENTION.search(source_value)
            )
            and not _SOURCE_NEGATED_ODOR_CONTROL.search(source_value)
            and "NEGATION_DROPPED" not in reasons
        ):
            # Don't collapse an explicit eliminate/prevent/neutralize promise
            # into a weaker reduce-odor claim. This remains a review signal;
            # the guard never rewrites the candidate.
            reasons.append("NEGATION_DROPPED")
        if _has_unsupported_negative_attribute(source_value, predicted_value):
            reasons.append("UNSUPPORTED_NEGATIVE_ATTRIBUTE")
        if field == "cat1" and predicted_value and predicted_value not in FIXED_CAT1:
            reasons.append("INVALID_CATEGORY")
        if field == "cat2" and predicted_value and not _CJK.search(predicted_value):
            reasons.append("INVALID_CATEGORY")
        if _has_spanish_residual(predicted_value, allowed_brand_phrases):
            reasons.append("SPANISH_RESIDUAL")
        if _has_english_residual(predicted_value, allowed_brand_phrases):
            reasons.append("ENGLISH_RESIDUAL")
        if field == "name" and contains_brand_phrase(predicted_value, allowed_brand_phrases):
            reasons.append("BRAND_RETAINED")
        if reasons:
            by_field[field] = tuple(reasons)

    reasons = tuple(sorted({reason for field_reasons in by_field.values() for reason in field_reasons}))
    return ModelOutputCheck(not reasons, reasons, by_field)


__all__ = [
    "ModelOutputCheck",
    "certification_tokens",
    "contains_brand_phrase",
    "numeric_fact_counters",
    "numeric_tokens",
    "technical_tokens",
    "unit_tokens",
    "validate_model_output",
]
