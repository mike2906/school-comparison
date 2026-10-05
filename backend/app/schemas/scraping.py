"""Schemas for scraping/discovery pipeline."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DiscoveredLocation(BaseModel):
    """A discovered school location during Stage 1 discovery."""

    address_i18n: dict[str, str] = Field(..., description="Address in multiple languages (e.g., {'bg': '...', 'en': '...'})")
    district: Optional[str] = Field(None, description="District/neighborhood name")
    lat: Optional[float] = Field(None, description="Latitude")
    lng: Optional[float] = Field(None, description="Longitude")
    phone: Optional[str] = Field(None, description="Phone number")
    is_primary: bool = Field(True, description="Whether this is the primary/main location")
    location_tags: list[str] = Field(
        default_factory=list,
        description="Location-level tags/refs (e.g. source-specific branch identifiers)",
    )
    geocode_meta: dict = Field(
        default_factory=dict,
        description="Internal geocoding provenance and rejection metadata",
    )

    # Age group shifts (many-to-many relationship data)
    age_groups: list[str] = Field(default_factory=list, description="Age groups served at this location")
    shifts: dict[str, str] = Field(
        default_factory=dict,
        description="Shift per age group, e.g., {'first': 'morning', 'preschool': 'full_day'}",
    )
    has_organised_groups: dict[str, bool] = Field(
        default_factory=dict, description="After-school care per age group"
    )


class DiscoveredSchool(BaseModel):
    """
    Schema for a school discovered during Stage 1 (Discovery).

    This represents the minimal data extracted from government registries
    or search results before any website scraping happens.
    """

    # Identification
    institutional_id: Optional[str] = Field(None, description="MoE Institutional ID (Код по НЕИСПУО) - primary idempotency key")
    name_i18n: dict[str, str] = Field(..., description="School name in multiple languages")

    # Classification
    country_code: str = Field("bg", description="Country code (ISO 3166-1 alpha-2)")
    city: Optional[str] = Field(None, description="City name (e.g., 'sofia')")
    school_type: str = Field(..., description="state | private | international")
    education_level: str = Field(..., description="nursery | kindergarten | primary | lower_secondary | upper_secondary")

    # Contact/Location
    source_url: Optional[str] = Field(None, description="URL of the registry page where this school was found")
    website_url: Optional[str] = Field(None, description="School's official website URL (if found)")
    locations: list[DiscoveredLocation] = Field(
        default_factory=list, description="School locations with addresses, coords, age groups"
    )

    # Additional attributes (flexible JSONB storage)
    attributes: dict = Field(
        default_factory=dict,
        description="Additional school-specific data (e.g., languages_offered, special_programs)",
    )

    # Admission info (if available from registry)
    admission_info: dict = Field(default_factory=dict, description="Admission system details (if available)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "institutional_id": "12345",
                "name_i18n": {"bg": "ДГ №1 Щастливо детство", "en": "KG #1 Happy Childhood"},
                "country_code": "bg",
                "city": "sofia",
                "school_type": "state",
                "education_level": "kindergarten",
                "source_url": "https://kg.sofia.bg/school/123",
                "website_url": "https://example-kg.bg",
                "locations": [
                    {
                        "address_i18n": {"bg": "ул. Иван Вазов 15, София", "en": "15 Ivan Vazov St, Sofia"},
                        "district": "Средец",
                        "lat": 42.6977,
                        "lng": 23.3219,
                        "is_primary": True,
                        "age_groups": ["first", "second"],
                        "shifts": {"first": "morning", "second": "full_day"},
                        "has_organised_groups": {"first": True, "second": True},
                    }
                ],
            }
        }
    )
