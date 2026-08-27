from scripts.refine_dictionary_translations import _brand_pattern, _residual_latin


def test_brand_pattern_is_cached_and_removes_known_brand_in_one_scan():
    brands = ("Hello Kitty", "Epson")

    first = _brand_pattern(brands)
    second = _brand_pattern(brands)

    assert first is second
    assert _residual_latin("Hello Kitty 收纳盒", list(brands)) == []
    assert _residual_latin("Caja de plástico", list(brands)) == ["Caja", "de", "plástico"]

