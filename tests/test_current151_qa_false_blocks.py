"""CI_SAFE: pure source/target QA controls from the finite current-field review."""
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation, _numbers, _units
from action_tracker.localization.body_names import mask_source_bound_body_names


def check(source, target, field='details'):
    attr={'details':'details_es','description':'desc_es','spec':'spec_es'}[field]
    return guard_translation(SourceFacts(sku='1001', **{attr:source}), {field:target}, (field,))


def rules(result):
    return {f['rule_id'] for f in result['findings']}


@pytest.mark.parametrize('unit,value', [('cm','28'),('mm','12')])
def test_diameter_symbol_is_not_a_latin_model_boundary(unit,value):
    assert check(f'Ø {value} {unit}', f'Ø{value}{unit}', 'spec')['status']=='PASS'
    assert 'UNIT_DROPPED' in rules(check(f'Ø {value} {unit}', f'Ø{value}', 'spec'))
    assert 'UNIT_DROPPED' in rules(check(f'Ø {value} {unit}', f'Ø{value}kg', 'spec'))
    assert not _units(f'MODELØ{value}{unit}')


@pytest.mark.parametrize('key',['充电速度','充电快/慢','充电器快/慢','快充/慢充','充电方式','充电速度（快/慢）','充电器充电速度'])
def test_speed_selected_value_is_not_charger_type_or_current(key):
    source='Cargador rápido / lento: Rápido; Tipo de cargador: Cargador universal; Corriente de carga: 5000 mA'
    target=f'{key}：快速；充电器类型：通用充电器；充电电流：5000毫安'
    assert not any(r.startswith('DETAIL_CHARGING_SPEED') for r in rules(check(source,target)))
    assert 'DETAIL_CHARGING_SPEED_CHANGED' in rules(check(source,target.replace('：快速','：慢速')))
    assert 'DETAIL_CHARGING_SPEED_MISSING' in rules(check(source,'充电器类型：通用充电器；充电电流：5000毫安'))


def test_conflicting_duplicate_speed_cannot_hide_behind_type_exclusion():
    result=check('Cargador rápido / lento: Rápido', '充电速度：快速；充电模式：慢速；充电器类型：通用充电器')
    assert 'DETAIL_CHARGING_SPEED_MISSING' in rules(result)


@pytest.mark.parametrize('source,target',[
 ('Perfumado: Sí; Sin perfume: No','有香味：是；无香味：否'),
 ('Perfumado: No; Sin perfume: Sí','有香味：否；无香味：是'),
])
def test_perfumed_and_perfume_free_are_distinct_boolean_rows(source,target):
    assert check(source,target)['status']=='PASS'
    assert 'DETAIL_BOOLEAN_FIELD_MISSING' in rules(check(source,target.split('；')[0]))
    reversed_first=target.replace('是','TEMP',1).replace('否','是',1).replace('TEMP','否')
    assert 'DETAIL_BOOLEAN_POLARITY_CHANGED' in rules(check(source,reversed_first))


def test_explicit_shoelace_hole_alternatives_preserve_both_options():
    source='2 pares. Zapatos con seis o siete agujeros, también siete u ocho agujeros'
    assert _numbers('seis o siete agujeros')==_numbers('6或7个鞋带孔')
    assert _numbers('siete u ocho agujeros')==_numbers('7或8个鞋带孔')
    assert _numbers('treinta y seis o siete agujeros')!={'6':1,'7':1}
    assert _numbers('doscientos seis o siete agujeros')!={'6':1,'7':1}
    assert _numbers('novecientos seis o siete agujeros')!={'6':1,'7':1}
    assert 'NUMERIC_DROPPED' in rules(check('seis o siete agujeros','6个鞋带孔','description'))
    assert 'NUMERIC_ADDED' in rules(check('Producto','6或7个鞋带孔','description'))


@pytest.mark.parametrize('name', ['Bar-le-Duc Still','Bar-le-Duc','Capri-Sun','Hello Kitty','Minnie Mouse','Stitch','Disney','Spidey','La Patrulla Canina','Pop-it'])
def test_reviewed_complete_names_are_bound_to_own_source_and_body(name):
    assert 'SPANISH_RESIDUAL' not in rules(check(name, name, 'description'))
    assert mask_source_bound_body_names('Producto',name)==name
    assert mask_source_bound_body_names('ñ'+name,name)==name
    assert mask_source_bound_body_names(name+'ñ',name).strip()
    assert mask_source_bound_body_names(name,'ñ'+name)=='ñ'+name
    assert mask_source_bound_body_names(name+'x',name).strip()
    assert mask_source_bound_body_names(name,name+'x').strip()
    assert 'SPANISH_RESIDUAL' in rules(check(name+'x', name, 'description'))
    assert 'SPANISH_RESIDUAL' in rules(check(name, name+'x', 'description'))
    assert 'SPANISH_RESIDUAL' in rules(check(name,name+' con material','description'))
    source=SourceFacts(sku='1001',name_es=name,desc_es='Producto')
    result=guard_translation(source,{'description':name},('description',))
    assert 'SPANISH_RESIDUAL' in rules(result)


def test_multiword_identity_does_not_allow_individual_words():
    assert mask_source_bound_body_names('La Patrulla Canina','Canina')=='Canina'
    assert mask_source_bound_body_names('Bar-le-Duc Still','Still')=='Still'
from action_tracker.localization.normalization.structured_details import parse_structured_details


def test_reviewed_flattened_labels_preserve_original_fragments_order_and_duplicates():
    source=('Material: Polirresina; Incluye fuente de luz Sí; Instalación De pie; '
            'Pilas incluidas No; Tema Navidad; Tipo de alimentación Batería; '
            'Tipo de decoración de temporada Decoración de temporada; '
            'Tipo de fuente de luz Lámpara LED; Tipo de temporada Invierno; '
            'Código de batería AA; Código de batería AA; Tipo de decoración Estatuilla')
    pairs=parse_structured_details(source)
    assert len(pairs)==12
    assert [(p.key,p.value) for p in pairs][1:4]==[
        ('Incluye fuente de luz','Sí'),('Instalación','De pie'),('Pilas incluidas','No')]
    assert pairs[6].key=='Tipo de decoración de temporada'
    assert pairs[6].value=='Decoración de temporada'
    assert [p.source_text for p in pairs]==source.split('; ')
    assert [(p.key,p.value) for p in pairs][-3:]==[
        ('Código de batería','AA'),('Código de batería','AA'),('Tipo de decoración','Estatuilla')]


@pytest.mark.parametrize('fragment',['Tema','Instalación','Etiqueta desconocida Navidad','Incluye fuente de luz Quizás','Pilas incluidas Roto'])
def test_flattened_recovery_does_not_guess_unknown_labels_or_boolean_values(fragment):
    assert not parse_structured_details(fragment)



def test_angel_requires_own_source_explicit_disney_character_context():
    source='Stitch y Ángel de Disney'
    assert 'SPANISH_RESIDUAL' not in rules(check(source,'迪士尼Stitch和Ángel','description'))
    for ordinary in ['Ángel de Navidad','Ángel decorativo','Disney y Ángel','Stitch y un ángel']:
        assert mask_source_bound_body_names(ordinary,'Ángel')=='Ángel'
        assert 'SPANISH_RESIDUAL' in rules(check(ordinary,'Ángel','description'))


@pytest.mark.parametrize('source', [
    'ñStitch y Ángel de Disney',
    'éStitch y Ángel de Disney',
    'Stitch y Ángel de Disneyñ',
    'Stitch y Ángel de Disneyé',
])
def test_angel_character_context_respects_unicode_word_boundaries(source):
    assert mask_source_bound_body_names(source, 'Ángel') == 'Ángel'
    assert 'SPANISH_RESIDUAL' in rules(check(source, 'Ángel', 'description'))


def test_angel_character_context_still_accepts_punctuation_boundaries():
    source = '(Stitch y Ángel de Disney).'
    assert not mask_source_bound_body_names(source, 'Ángel').strip()
    assert 'SPANISH_RESIDUAL' not in rules(check(source, 'Ángel', 'description'))
