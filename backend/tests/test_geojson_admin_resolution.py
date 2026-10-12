import json

from app.services.geocoding.bg.geojson import GeoJSONProvider, city_storage_value


def _feature(name: str, city: str) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [23.3, 42.7]},
        "properties": {"name": name, "city": city, "street": "ул. Тест 1"},
    }


def test_city_storage_value_uses_ascii_settlement_names():
    assert city_storage_value("София") == "sofia"
    assert city_storage_value("Столична") == "sofia"
    assert city_storage_value("Елин Пелин") == "elin pelin"
    assert city_storage_value("с. Осоица") == "osoitsa"


def test_geojson_admin_resolution_confirms_hint_and_fails_closed(tmp_path):
    path = tmp_path / "education.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    _feature("ОУ Иван Вазов", "СВОГЕ"),
                    _feature("ОУ Иван Вазов", "СТОЛИЧНА"),
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = GeoJSONProvider(str(path))

    confirmed = provider.resolve_admin_municipality(
        school_name="ОУ Иван Вазов",
        addresses=["гр. Своге, ул. Тест 1"],
        municipality_hint="Своге",
    )
    assert confirmed.municipality == "СВОГЕ"
    assert confirmed.ambiguous is False

    ambiguous = provider.resolve_admin_municipality(
        school_name="ОУ Иван Вазов",
        addresses=["ул. Тест 1"],
    )
    assert ambiguous.municipality is None
    assert ambiguous.ambiguous is True


def test_geojson_admin_resolution_requires_address_agreement_without_hint(tmp_path):
    path = tmp_path / "education.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [_feature("Уникално училище", "СВОГЕ")],
            }
        ),
        encoding="utf-8",
    )
    provider = GeoJSONProvider(str(path))

    confirmed = provider.resolve_admin_municipality(
        school_name="Уникално училище",
        addresses=["гр. Своге, ул. Тест 1"],
    )
    assert confirmed.municipality == "СВОГЕ"
    assert confirmed.ambiguous is False

    mismatch = provider.resolve_admin_municipality(
        school_name="Уникално училище",
        addresses=["гр. София, ул. Тест 1"],
    )
    assert mismatch.municipality is None
    assert mismatch.ambiguous is True
    assert mismatch.evidence == "address_locality_does_not_confirm_geojson_municipality"
