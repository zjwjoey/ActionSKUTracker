"""CI_SAFE: frozen real-source boundary regressions, no production IO."""
import pytest

from action_tracker.localization.body_names import mask_source_bound_body_names
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.normalization.structured_details import parse_structured_details


@pytest.mark.parametrize('identity', [
    'Sonic the Hedgehog', 'Zuru X-Shot Skins', 'Tesi Bon Hot Spiced Curlz',
    'Remington Pro-Ceramic Extra', 'La casa de muñecas de Gabby',
    'El Rey León', 'Dr. Pepper', 'Match-It', 'i-Touch', 'Collect memories',
    'LR14 / baby',
])
def test_complete_own_field_identity_mask(identity):
    source=f'Producto de {identity}.'
    assert mask_source_bound_body_names(source,identity).strip()==''
    assert mask_source_bound_body_names('Producto',identity)==identity
    assert mask_source_bound_body_names('ñ'+identity,identity)==identity
    assert mask_source_bound_body_names(identity,'ñ'+identity)=='ñ'+identity
    assert 'sin perfume' in mask_source_bound_body_names(source,identity+' sin perfume')
    q=guard_translation(SourceFacts(sku='x',desc_es=source+' 2 unidades'),
                        {'description':identity+'产品3件'},('description',))
    assert 'NUMERIC_DROPPED' in {x['rule_id'] for x in q['findings']}
    q=guard_translation(SourceFacts(sku='x',name_es=identity,desc_es='Producto'),
                        {'description':identity},('description',))
    # Some individual camel-case model tokens already have independent shape
    # exemptions; accented/prose-shaped complete identities remain own-field.
    if identity in {'El Rey León','La casa de muñecas de Gabby','Collect memories'}:
        assert 'SPANISH_RESIDUAL' in {x['rule_id'] for x in q['findings']}


@pytest.mark.parametrize('source',[
    'Hecho de poliéster resistente\nPara elegir entre Stitch o Ángel',
    'Para elegir entre Pokémon, Stitch & Ángel, Spidey y Bluey, entre otros',
])
def test_angel_character_enum_is_complete(source):
    assert mask_source_bound_body_names(source,'Ángel').strip()==''
    for wrong in [source.replace('Para elegir','ñPara elegir'),source.replace('Ángel','un ángel')]:
        assert mask_source_bound_body_names(wrong,'Ángel')=='Ángel'
    assert mask_source_bound_body_names('Stitch y un ángel de Navidad','Ángel')=='Ángel'


def test_model_list_keeps_exact_own_field_models_without_whitelisting_pro():
    source='Diferentes fundas para varios modelos de iPhone: 12, 12 pro, 13 y 13 pro'
    target='iPhone 12、iPhone 12 Pro、iPhone 13、iPhone 13 Pro'
    assert not mask_source_bound_body_names(source,target).replace('、','').strip()
    assert mask_source_bound_body_names(source,'Pro')=='Pro'
    assert mask_source_bound_body_names(source,'iPhone 14 Pro')=='iPhone 14 Pro'
    assert mask_source_bound_body_names('iPhone: 12, varias y 13 pro','iPhone 13 Pro')=='iPhone 13 Pro'
    assert mask_source_bound_body_names(source,target+' con material').endswith(' con material')


def test_device_plural_aliases_are_finite_and_own_field():
    assert mask_source_bound_body_names('dispositivos iPhones, iPads y AirPods','iPhone、iPad').replace('、','').strip()==''
    assert mask_source_bound_body_names('dispositivos móviles','iPhone、iPad')=='iPhone、iPad'
    assert mask_source_bound_body_names('dispositivos iPhones','iPad')=='iPad'


@pytest.mark.parametrize('identity,fragment',[
    ('Sonic the Hedgehog','the Hedgehog'),
    ('Tesi Bon Hot Spiced Curlz','Hot'),
    ('Remington Pro-Ceramic Extra','Extra'),
    ('La casa de muñecas de Gabby','casa'),
    ('Collect memories','memories'),
    ('LR14 / baby','baby'),
])
def test_new_identity_fragments_are_not_independent_residual_exceptions(identity,fragment):
    assert mask_source_bound_body_names(identity,fragment)==fragment


def test_frozen_3217806_all_pairs_order_and_duplicate_length_are_preserved():
    source=('Color: Multicolor, Blanco; Color suave: Blanco cálido; Código de batería AA; '
            'Destinado a: Interior; Incluye fuente de luz Sí; Longitud del cable: 195 cm; '
            'Longitud del cable: 195; Número de pilas necesarias: 3; Pilas incluidas No; '
            'Tema Navidad; Tipo de alimentación Batería; Tipo de decoración de temporada Decoración de temporada; '
            'Tipo de fuente de luz Lámpara LED; Tipo de temporada Invierno; Tipos de fuentes de luz 40; Número del artículo: 3217806')
    pairs=parse_structured_details(source)
    assert len(pairs)==16
    assert [x.key for x in pairs]==['Color','Color suave','Código de batería','Destinado a','Incluye fuente de luz',
        'Longitud del cable','Longitud del cable','Número de pilas necesarias','Pilas incluidas','Tema','Tipo de alimentación',
        'Tipo de decoración de temporada','Tipo de fuente de luz','Tipo de temporada','Tipos de fuentes de luz','Número del artículo']
    assert [x.value for x in pairs if x.key=='Longitud del cable']==['195 cm','195']
    assert pairs[-2].source_text=='Tipos de fuentes de luz 40'


def test_frozen_3223393_all_source_care_pairs_preserved():
    source=('Especificaciones; Color: Marrón, Gris topo; Material: Poliéster; Instrucciones de lavado No lavar; '
            'Instrucciones de planchado Sin planchado; Instrucciones de secado No secar en tambor de secado; '
            'Tipo de cojines Cojines cuadrados; Número del artículo: 3223393')
    pairs=parse_structured_details(source)
    assert [(x.key,x.value) for x in pairs]==[
        ('Color','Marrón, Gris topo'),('Material','Poliéster'),('Instrucciones de lavado','No lavar'),
        ('Instrucciones de planchado','Sin planchado'),('Instrucciones de secado','No secar en tambor de secado'),
        ('Tipo de cojines','Cojines cuadrados'),('Número del artículo','3223393')]
    assert pairs[2].source_text=='Instrucciones de lavado No lavar'
    # Unknown/damaged keys are not guessed and standalone headers are not pairs.
    assert not parse_structured_details('Especificaciones; Instruccione lavado No lavar; Tipo de cojín dañado Sí')


def test_frozen_3218408_furniture_cover_source_pairs_are_lossless():
    source=('Color: Diseño; Material: Plástico; Antideslizante No; Apto para tipo de mueble Mesa; '
            'Extraíble Sí; Forma: Rectangular; Instrucciones de lavado No lavar; '
            'Instrucciones de planchado Sin planchado; Instrucciones de secado No secar en tambor de secado; '
            'Tipo de cubierta para muebles Mantel (no desechable); Uso de doble cara No; Número del artículo: 3218408')
    expected=[('Color','Diseño'),('Material','Plástico'),('Antideslizante','No'),('Apto para tipo de mueble','Mesa'),
              ('Extraíble','Sí'),('Forma','Rectangular'),('Instrucciones de lavado','No lavar'),
              ('Instrucciones de planchado','Sin planchado'),('Instrucciones de secado','No secar en tambor de secado'),
              ('Tipo de cubierta para muebles','Mantel (no desechable)'),('Uso de doble cara','No'),('Número del artículo','3218408')]
    pairs=parse_structured_details(source)
    assert [(x.key,x.value) for x in pairs]==expected
    assert pairs[9].source_text=='Tipo de cubierta para muebles Mantel (no desechable)'
    assert not parse_structured_details('Antideslizante a veces; Extraíble dato perdido; Uso de doble cara quizá')
    assert not parse_structured_details('Apto para tipo mueble Mesa; Tipo de cubierta muebles Mantel')
    # A source NO remains a typed polarity assertion, never a guessed YES.
    assert parse_structured_details('Uso de doble cara No')[0].value=='No'
