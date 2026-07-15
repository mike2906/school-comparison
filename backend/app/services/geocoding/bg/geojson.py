"""GeoJSON-based geocoding provider for Bulgarian schools.

Uses the EU Commission's education GeoJSON data as a primary source for coordinates.
This avoids API rate limits and provides instant lookups for ~500+ Sofia schools.

Data source: https://gisco-services.ec.europa.eu/pub/education/
"""
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult
from app.services.geocoding.bounds import SOFIA_MUNICIPALITY_BOUNDS, point_in_bounds
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

SOFIA_MAP_BOUNDS = SOFIA_MUNICIPALITY_BOUNDS


@dataclass(frozen=True)
class AdminMunicipalityResolution:
    """Fail-closed administrative classification from the GeoJSON dataset."""

    municipality: Optional[str]
    evidence: str
    ambiguous: bool = False


def city_storage_value(value: Optional[str]) -> Optional[str]:
    """Convert a Bulgarian settlement label to the database city convention."""
    if not value:
        return None

    cleaned = re.sub(r"^(?:гр\.?|с\.?|село)\s+", "", value.strip(), flags=re.IGNORECASE)
    transliterated = transliterate_bulgarian(cleaned).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", transliterated).strip()
    if normalized in {"sofia", "stolichna"}:
        return "sofia"
    return normalized or None


class GeoJSONProvider(BaseGeocodingProvider):
    """
    Geocoding provider using EU Commission's GeoJSON education data for Bulgaria.

    This provider:
    - Loads BG education institutions from local GeoJSON file
    - Matches schools by normalized name
    - Returns coordinates instantly (no API calls)
    - Covers 517 Sofia institutions (kindergartens, schools, gymnasiums)
    """

    def __init__(self, geojson_path: Optional[str] = None):
        """
        Initialize GeoJSON provider.

        Args:
            geojson_path: Path to BG.geojson file (default: backend/data/bg/education.geojson)
        """
        if geojson_path is None:
            # Default path relative to backend root (one extra level up since we're in bg/ subfolder)
            backend_root = Path(__file__).parent.parent.parent.parent.parent
            geojson_path = backend_root / "data" / "bg" / "education.geojson"

        self.geojson_path = Path(geojson_path)
        self._index = None  # Lazy-loaded on first use
        self._website_index = None  # Lazy-loaded host -> feature index
        self._features_by_name = None  # Lazy-loaded normalized name -> all matching features
        self._admin_municipalities = None  # Lazy-loaded normalized municipality -> source label

    @property
    def provider_name(self) -> str:
        return "geojson_bg"

    def _load_index(self):
        """Load and index GeoJSON data."""
        if self._index is not None:
            return

        logger.info(f"Loading GeoJSON index from {self.geojson_path}")

        if not self.geojson_path.exists():
            logger.error(f"GeoJSON file not found: {self.geojson_path}")
            self._index = {}
            self._website_index = {}
            self._features_by_name = {}
            self._admin_municipalities = {}
            return

        with open(self.geojson_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Build index: (normalized_name, normalized_city) -> feature
        self._index = {}
        self._website_index = {}
        self._features_by_name = {}
        self._admin_municipalities = {}
        for feature in data.get('features', []):
            props = feature['properties']
            name = props.get('name', '').strip()
            city = props.get('city', '').strip()
            if not name:
                continue

            # Store by (normalized_name, normalized_city) tuple
            normalized_name = self._normalize_name(name)
            normalized_city = self._normalize_city(city)
            key = (normalized_name, normalized_city)
            self._features_by_name.setdefault(normalized_name, []).append(feature)
            if normalized_city:
                self._admin_municipalities.setdefault(normalized_city, city.upper().strip())

            # Keep the best match (prefer entries with more complete data)
            if key not in self._index or self._has_better_data(feature, self._index[key]):
                self._index[key] = feature

            website_host = self._normalize_website_host(props.get('url', ''))
            if website_host and (
                website_host not in self._website_index
                or self._has_better_data(feature, self._website_index[website_host])
            ):
                self._website_index[website_host] = feature

        logger.info(f"Indexed {len(self._index)} schools from GeoJSON")

    def resolve_admin_municipality(
        self,
        *,
        school_name: str,
        addresses: list[str],
        municipality_hint: Optional[str] = None,
    ) -> AdminMunicipalityResolution:
        """Resolve a municipality without consulting stored coordinates.

        MoE municipality labels are authoritative hints, but must exist in the
        GeoJSON administrative values. Records without such a hint may only be
        resolved when their school-name match points to one municipality and an
        address is present; multiple municipality matches fail closed.
        """
        self._load_index()

        if municipality_hint:
            normalized_hint = self._normalize_city(municipality_hint)
            source_label = (self._admin_municipalities or {}).get(normalized_hint)
            if source_label:
                return AdminMunicipalityResolution(
                    municipality=source_label,
                    evidence="moe_municipality_confirmed_by_geojson",
                )
            return AdminMunicipalityResolution(
                municipality=None,
                evidence=f"municipality_not_in_geojson:{municipality_hint}",
                ambiguous=True,
            )

        normalized_name = self._normalize_name(school_name or "")
        matches = (self._features_by_name or {}).get(normalized_name, [])
        municipalities = {
            self._normalize_city((feature.get("properties") or {}).get("city", ""))
            for feature in matches
            if (feature.get("properties") or {}).get("city")
        }
        if not any((address or "").strip() for address in addresses):
            return AdminMunicipalityResolution(
                municipality=None,
                evidence="missing_address",
                ambiguous=True,
            )
        if len(municipalities) == 1:
            normalized = next(iter(municipalities))
            return AdminMunicipalityResolution(
                municipality=(self._admin_municipalities or {}).get(normalized, normalized),
                evidence="addressed_school_unique_geojson_municipality",
            )
        if len(municipalities) > 1:
            return AdminMunicipalityResolution(
                municipality=None,
                evidence="school_name_matches_multiple_geojson_municipalities",
                ambiguous=True,
            )
        return AdminMunicipalityResolution(
            municipality=None,
            evidence="school_not_found_in_geojson",
            ambiguous=True,
        )

    def _normalize_city(self, city: str) -> str:
        """
        Normalize city name for matching.

        Handles variations and ensures consistent uppercase format.
        Maps common city name variants to match GeoJSON convention.

        Args:
            city: City name to normalize

        Returns:
            Normalized city name (uppercase, stripped)
        """
        normalized = city.upper().strip()

        # Map city variants to GeoJSON convention
        # GeoJSON uses province/municipality names (СТОЛИЧНА, ПЛОВДИВ, etc.)
        city_mappings = {
            'SOFIA': 'СТОЛИЧНА',      # English
            'СОФИЯ': 'СТОЛИЧНА',       # Bulgarian Cyrillic
            'SOFIYA': 'СТОЛИЧНА',      # Alternative transliteration
        }

        return city_mappings.get(normalized, normalized)

    def _extract_city_from_address(self, address: str) -> Optional[str]:
        """
        Extract city name from Bulgarian address string.

        Handles common patterns like:
        - "гр. София, ул. X"
        - "с. Казичене, ул. Y"
        - "София, ул. Z"
        - "район Витоша, ул. X" (returns None - district, not city)

        Args:
            address: Full address string

        Returns:
            City name or None if not found
        """
        if not address:
            return None

        # Pattern 1: "гр. [City]," or "с. [City],"
        match = re.match(r'(?:гр\.|с\.)\s*([^,]+)', address)
        if match:
            return match.group(1).strip()

        # Pattern 2: City name at start before comma (e.g., "София, ул. X")
        # Common Bulgarian cities
        bulgarian_cities = [
            'София', 'Пловдив', 'Варна', 'Бургас', 'Русе', 'Стара Загора',
            'Плевен', 'Сливен', 'Добрич', 'Шумен', 'Перник', 'Хасково',
            'Ямбол', 'Пазарджик', 'Благоевград', 'Велико Търново', 'Враца',
            'Габрово', 'Асеновград', 'Видин', 'Казанлък', 'Кърджали',
            'Кюстендил', 'Монтана', 'Димитровград', 'Търговище', 'Силистра',
            'Ловеч', 'Разград', 'Смолян', 'Дупница', 'Свищов', 'Гоце Делчев',
            'Петрич', 'Самоков', 'Сандански', 'Нова Загора', 'Карлово'
        ]
        for city in bulgarian_cities:
            if address.startswith(city):
                return city

        return None

    def _normalize_name(self, name: str) -> str:
        """
        Normalize school name for matching.

        Handles variations like:
        - "ДГ №2" <-> "ДЕТСКА ГРАДИНА №2"
        - "СУ" <-> "СРЕДНО УЧИЛИЩЕ"
        - "ОУ" <-> "ОСНОВНО УЧИЛИЩЕ"
        - Removes (с яслени групи), quotes, extra spaces
        - Removes legal entity suffixes (ЕООД, ООД, ЕАД, etc.)
        - Removes ownership prefixes (ЧАСТНА, ЧАСТНО)

        Args:
            name: School name to normalize

        Returns:
            Normalized name (uppercase, no punctuation, standardized abbreviations)
        """
        # Convert to uppercase
        normalized = name.upper().strip()

        # Remove all quote types (regular, smart quotes, Bulgarian quotes)
        normalized = normalized.replace('"', '').replace('"', '').replace('"', '').replace('„', '').replace('"', '')

        # Remove legal entity suffixes (Bulgarian business forms)
        legal_suffixes = [' ЕООД', ' ООД', ' ЕАД', ' АД', ' ЕТ', ' СД']
        for suffix in legal_suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]

        # Remove ownership prefixes for better matching
        ownership_prefixes = ['ЧАСТНА ', 'ЧАСТНО ', 'ЧАСТЕН ']
        for prefix in ownership_prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]

        # Remove parenthetical suffixes like (с яслени групи)
        normalized = re.sub(r'\s*\(.*?\)\s*', ' ', normalized)

        # Expand common abbreviations (bidirectional)
        # This allows "ДГ №2" to match "ДЕТСКА ГРАДИНА №2"
        abbreviations = {
            'ДЕТСКА ГРАДИНА': 'ДГ',
            'СРЕДНО УЧИЛИЩЕ': 'СУ',
            'ОСНОВНО УЧИЛИЩЕ': 'ОУ',
            'ПРОФЕСИОНАЛНА ГИМНАЗИЯ': 'ПГ',
            'ПРОФИЛИРАНА ГИМНАЗИЯ': 'ПГ',
            'НАЧАЛНО УЧИЛИЩЕ': 'НУ',
            'ОБЕДИНЕНО УЧИЛИЩЕ': 'ОБУ',
        }

        # Replace full forms with abbreviations for consistent matching
        for full, abbr in abbreviations.items():
            normalized = normalized.replace(full, abbr)

        # Remove extra spaces, punctuation
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        normalized = normalized.replace('№', '')
        normalized = normalized.replace('.', '')
        normalized = normalized.replace(',', '')

        return normalized

    def _has_better_data(self, feature1: dict, feature2: dict) -> bool:
        """Check if feature1 has better/more complete data than feature2."""
        props1 = feature1['properties']
        props2 = feature2['properties']

        # Prefer entries with street addresses
        if props1.get('street') and not props2.get('street'):
            return True

        # Prefer entries with phone numbers
        if props1.get('tel') and not props2.get('tel'):
            return True

        return False

    def _normalize_website_host(self, website: str) -> Optional[str]:
        """Normalize website URL/host to comparable host value."""
        value = (website or "").strip().lower()
        if not value:
            return None

        candidate = value if "://" in value else f"https://{value}"
        parsed = urlparse(candidate)
        host = (parsed.netloc or parsed.path).strip().lower()
        if not host:
            return None
        if "/" in host:
            host = host.split("/", 1)[0]
        if host.startswith("www."):
            host = host[4:]
        return host or None

    def _result_from_feature(self, feature: dict) -> GeocodingResult:
        """Build a GeocodingResult from a matched GeoJSON feature."""
        coords = feature['geometry']['coordinates']
        lng, lat = coords[0], coords[1]

        props = feature['properties']
        street = props.get('street', '')
        matched_city = props.get('city', '')
        postcode = props.get('postcode', '')
        formatted_address = f"{street}, {postcode} {matched_city}".strip(', ')

        return GeocodingResult(
            lat=lat,
            lng=lng,
            success=True,
            provider=self.provider_name,
            formatted_address=formatted_address,
            method="geojson_name_match",
            precision="approximate",
        )

    def _feature_matches_city_bounds(self, feature: dict, normalized_city: Optional[str]) -> bool:
        if normalized_city != "СТОЛИЧНА":
            return True

        coords = feature["geometry"]["coordinates"]
        lng, lat = coords[0], coords[1]
        return point_in_bounds(lat, lng, SOFIA_MAP_BOUNDS)

    async def geocode(self, address: str, country_code: str = "bg", school_name: Optional[str] = None, city: Optional[str] = None) -> GeocodingResult:
        """
        Geocode by matching school name and city in GeoJSON index.

        Args:
            address: Address string (fallback for city extraction if city param not provided)
            country_code: Country code (must be "bg")
            school_name: School name to match (REQUIRED for this provider)
            city: City name for matching (preferred over extracting from address)

        Returns:
            GeocodingResult with coordinates or error
        """
        if country_code != "bg":
            return GeocodingResult(
                success=False,
                error="GeoJSON provider only supports Bulgaria (bg)",
                provider=self.provider_name,
            )

        if not school_name:
            return GeocodingResult(
                success=False,
                error="School name required for GeoJSON lookup",
                provider=self.provider_name,
            )

        # Lazy-load index
        self._load_index()

        if not self._index:
            return GeocodingResult(
                success=False,
                error="GeoJSON index not loaded",
                provider=self.provider_name,
            )

        # Normalize school name
        normalized_name = self._normalize_name(school_name)

        # Use provided city or extract from address as fallback
        if not city:
            city = self._extract_city_from_address(address)

        normalized_city = self._normalize_city(city) if city else None

        # Try exact match (name + city)
        feature = None
        if normalized_city:
            key = (normalized_name, normalized_city)
            feature = self._index.get(key)
            if feature:
                logger.debug(f"GeoJSON exact match: '{school_name}' in '{city}'")

        # Fallback: try name-only matching if city match failed
        if not feature:
            # Find all features with matching name (any city)
            matching_features = [
                (k, f) for k, f in self._index.items()
                if k[0] == normalized_name
            ]

            if len(matching_features) == 1:
                # Only one school with this name across all cities - safe to use
                feature = matching_features[0][1]
                feature_city = matching_features[0][0][1]
                logger.debug(f"GeoJSON name-only match: '{school_name}' (unique, city={feature_city})")
            elif len(matching_features) > 1:
                # Multiple schools with same name in different cities
                cities = [k[1] for k, _ in matching_features]
                logger.warning(
                    f"GeoJSON ambiguous match: '{school_name}' exists in {len(matching_features)} cities ({', '.join(cities[:3])}...). "
                    f"Cannot determine correct city from address '{address}'. Skipping."
                )
                return GeocodingResult(
                    success=False,
                    error=f"Ambiguous match: school name exists in {len(matching_features)} cities",
                    provider=self.provider_name,
                )

        if not feature:
            logger.debug(f"No GeoJSON match for '{school_name}' (normalized: '{normalized_name}', city: '{city}')")
            return GeocodingResult(
                success=False,
                error="No match in GeoJSON index",
                provider=self.provider_name,
            )

        props = feature['properties']
        if not self._feature_matches_city_bounds(feature, normalized_city):
            logger.warning(
                "GeoJSON match outside expected city bounds: '%s' in %s at %s",
                school_name,
                props.get("city", ""),
                feature["geometry"]["coordinates"],
            )
            return GeocodingResult(
                success=False,
                error="GeoJSON match outside expected city bounds",
                provider=self.provider_name,
            )

        logger.info(f"GeoJSON match: '{school_name}' in {props.get('city', '')}")
        return self._result_from_feature(feature)

    async def geocode_by_website(self, website_url: str) -> GeocodingResult:
        """Geocode by website host match in the GeoJSON dataset."""
        self._load_index()

        host = self._normalize_website_host(website_url)
        if not host:
            return GeocodingResult(
                success=False,
                error="Website host missing",
                provider=self.provider_name,
            )

        feature = (self._website_index or {}).get(host)
        if not feature:
            return GeocodingResult(
                success=False,
                error="No website host match in GeoJSON index",
                provider=self.provider_name,
            )

        logger.info("GeoJSON website match: '%s' -> %s", website_url, host)
        return self._result_from_feature(feature)

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult:
        """
        Reverse geocoding not supported by GeoJSON provider.

        Use Nominatim provider for reverse geocoding.
        """
        return GeocodingResult(
            success=False,
            error="Reverse geocoding not supported by GeoJSON provider",
            provider=self.provider_name,
        )
