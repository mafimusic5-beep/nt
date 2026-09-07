from types import SimpleNamespace

from src.backend.utils.node_city import (
    normalize_node_city,
    normalize_pool_region_code,
    region_display_name,
)


def test_country_city_codes_collapse_to_one_country_pool():
    assert normalize_pool_region_code("de-frankfurt") == "de"
    assert normalize_pool_region_code("de-berlin") == "de"
    assert normalize_pool_region_code("es-madrid") == "es"


def test_country_pool_labels_are_russian():
    assert region_display_name("de") == "Германия"
    assert region_display_name("es-madrid") == "Испания"
    assert region_display_name("fr-paris") == "Франция"


def test_public_node_label_uses_country_not_technical_name():
    node = SimpleNamespace(
        region_code="de-frankfurt",
        name="server-2-50b8db",
        endpoint="82.165.163.77",
    )
    assert normalize_node_city(node) == "Германия"
