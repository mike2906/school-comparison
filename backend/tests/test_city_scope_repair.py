import json

import pytest

from app.models import School, SchoolLocation
from app.services.geocoding.bg.geojson import GeoJSONProvider, city_storage_value
from scripts import repair_city_scope


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


@pytest.mark.asyncio
async def test_run_repair_writes_report_before_mutation(monkeypatch, tmp_path):
    events = []

    async def fake_classify(db, provider):
        return []

    def fake_write(rows, *, report_root, timestamp):
        events.append("report")
        return report_root / timestamp / "classification.md"

    async def fake_apply(db, rows):
        events.append("mutation")
        return 0

    monkeypatch.setattr(repair_city_scope, "classify_candidates", fake_classify)
    monkeypatch.setattr(repair_city_scope, "write_classification_report", fake_write)
    monkeypatch.setattr(repair_city_scope, "apply_relabels", fake_apply)

    await repair_city_scope.run_repair(
        object(),
        provider=object(),
        report_root=tmp_path,
        timestamp="test-run",
        apply=True,
        expected_out_of_bounds=0,
        expected_no_coordinates=0,
    )

    assert events == ["report", "mutation"]


@pytest.mark.asyncio
async def test_candidate_sweep_includes_both_required_populations(db_session, tmp_path):
    geojson_path = tmp_path / "education.geojson"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    _feature("Province School", "СВОГЕ"),
                    _feature("Sofia School", "СТОЛИЧНА"),
                ],
            }
        ),
        encoding="utf-8",
    )
    schools = [
        School(
            name_i18n={"bg": "Province School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
            attributes={"moe_municipality_name": "Своге", "moe_town_name": "Своге"},
        ),
        School(
            name_i18n={"bg": "Sofia School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        ),
    ]
    db_session.add_all(schools)
    await db_session.flush()
    db_session.add_all(
        [
            SchoolLocation(
                school_id=schools[0].id,
                address_i18n={"bg": "гр. Своге, ул. Тест 1"},
                lat=42.97,
                lng=23.35,
                is_primary=True,
            ),
            SchoolLocation(
                school_id=schools[1].id,
                address_i18n={"bg": "гр. София, ул. Тест 1"},
                is_primary=True,
            ),
        ]
    )
    await db_session.commit()

    rows = await repair_city_scope.classify_candidates(
        db_session,
        GeoJSONProvider(str(geojson_path)),
    )

    by_name = {row.name: row for row in rows}
    assert by_name["Province School"].population == "out_of_bounds"
    assert by_name["Province School"].target_city == "svoge"
    assert by_name["Sofia School"].population == "no_coordinates"
    assert by_name["Sofia School"].action == "keep sofia"
