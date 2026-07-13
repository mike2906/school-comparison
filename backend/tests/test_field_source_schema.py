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


def test_value_json_list_payload_is_not_public():
    payload = _base_payload()
    payload["value_json"] = ["STEM Кабинет", "физкултурен салон", "външно спортно игрище"]

    model = FieldSourceResponse.model_validate(payload)

    assert "value_json" not in model.model_dump()
    assert not hasattr(model, "value_json")


def test_value_json_object_payload_is_not_public():
    payload = _base_payload()
    payload["value_json"] = {
        "languages": [
            {"language": "English", "level": None},
            {"language": "Bulgarian", "level": None},
        ]
    }

    model = FieldSourceResponse.model_validate(payload)

    assert "value_json" not in model.model_dump()
    assert not hasattr(model, "value_json")
