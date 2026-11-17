"""Paper tissue attribute polysemy must not waive cleaning-cloth facts."""
import pytest
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.qa import guard_translation

SOURCE = 'Material: Papel; Cantidad: 110 unidades; Número de capas: 3; Tipo de paño / pañuelo de mano: Caja / independiente'
TARGET = '材质：纸；数量：110件；层数：3；手帕/纸巾类型：盒装/独立式'

def check(text=SOURCE, target=TARGET, field='details'):
    facts = (SemanticFact('PRODUCT_TYPE','paño','清洁布',source_field='details_es'),)
    return guard_translation(SourceFacts.from_record({'details_es':text}),
                             {field:target},(field,),semantic_facts=facts)

def test_paper_tissue_attribute_does_not_require_cleaning_cloth():
    assert check()['overall_ready']

@pytest.mark.parametrize('text',[
    SOURCE.replace('Papel','Algodón'), SOURCE.replace('Material: Papel; ',''),
    SOURCE.replace('Material: Papel','Material: Papel; Material: Algodón'),
    SOURCE.replace(' / pañuelo de mano',''), SOURCE+'; Uso: paño',
    SOURCE+'; Incluye: paño de limpieza', SOURCE.replace('Caja / independiente',''),
])
def test_incomplete_conflicting_or_separate_cloth_keeps_guard(text):
    assert not check(text)['overall_ready']

@pytest.mark.parametrize('target',[
    TARGET.replace('110','11'), TARGET.replace('3','2'),
    TARGET.replace('手帕/纸巾类型','清洁布类型'),
    TARGET.replace('手帕/纸巾类型','不是手帕/纸巾类型'),
    TARGET.replace('手帕/纸巾类型','类型'),
])
def test_wrong_quantity_layers_object_negation_or_omission_stays_blocked(target):
    assert not check(target=target)['overall_ready']

def test_real_cleaning_cloth_still_requires_cloth_translation():
    assert not check('Material: Poliéster; Tipo de paño: Microfibra', '材质：涤纶；纸巾类型：微纤维')['overall_ready']
    assert check('Material: Poliéster; Tipo de paño: Microfibra', '材质：涤纶；清洁布类型：微纤维')['overall_ready']

def test_technical_token_guard_still_blocks_dropped_token():
    assert not check(SOURCE+'; Modelo: USB-C', TARGET+'；型号：通用')['overall_ready']
