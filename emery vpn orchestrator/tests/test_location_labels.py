from src.common.location_labels import russian_city_name, russian_location_label


def test_russian_location_label_uses_country_and_city() -> None:
    assert russian_location_label("DE", "Kleve") == "Клеве, Германия"


def test_russian_location_label_uses_country_without_city() -> None:
    assert russian_location_label("DE") == "Германия"


def test_russian_city_name_transliterates_unknown_latin_city() -> None:
    assert russian_city_name("Kleve") == "Клеве"


def test_russian_location_label_knows_common_datacenter_city() -> None:
    assert russian_location_label("DE", "Frankfurt") == "Франкфурт, Германия"
