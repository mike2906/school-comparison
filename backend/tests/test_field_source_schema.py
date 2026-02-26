from datetime import datetime, timezone

from app.models.field_source import SourceType
from app.schemas.field_source import FieldSourceResponse


def _base_payload() -> dict:
    return {
        "id": 1,
        "school_id": 1,
        "field_key": "facilities",
        "source_type": SourceType.SCRAPED_WEBSITE,
        "created_at": datetime.now(timezone.utc),
    }


def test_value_json_accepts_list_payload():
    payload = _base_payload()
    payload["value_json"] = ["STEM Кабинет", "физкултурен салон", "външно спортно игрище"]

    model = FieldSourceResponse.model_validate(payload)

    assert isinstance(model.value_json, list)
    assert model.value_json[0] == "STEM Кабинет"


def test_value_json_accepts_nested_object_payload():
    payload = _base_payload()
    payload["value_json"] = {
        "languages": [
            {"language": "English", "level": None},
            {"language": "Bulgarian", "level": None},
        ]
    }

    model = FieldSourceResponse.model_validate(payload)

    assert isinstance(model.value_json, dict)
    assert "languages" in model.value_json
