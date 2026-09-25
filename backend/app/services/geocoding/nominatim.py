"""OpenStreetMap Nominatim geocoding provider."""
import logging
import asyncio
from typing import Optional
import re
import httpx

from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult

logger = logging.getLogger(__name__)

# A result of one of these types is a settlement or administrative area, returned at its
# centroid (e.g. the Sofia city node when no street matched). It carries no information
# about where the school is, so it must be treated as "no coordinates".
AREA_LEVEL_RESULT_TYPES = frozenset({
    "city",
    "town",
    "municipality",
    "city_district",
    "county",
    "state_district",
    "state",
    "region",
    "country",
})
AREA_LEVEL_MATCH_ERROR = "area_level_match_only"
# Every result named a street in a different neighbourhood/district than the address.
AREA_MISMATCH_ERROR = "area_mismatch"

# Neighbourhood (кв., ж.к., в.з., м.) and district (район, р-н) markers in Bulgarian addresses.
_AREA_MARKER_RE = re.compile(
    r'(?:^|[\s,])(кв|ж\.?\s*к|жк|в\.?\s*з|м|район|р-н)(?:\.\s*|\s+)"?([^,"„“”]+)',
    flags=re.IGNORECASE,
)
_DISTRICT_MARKERS = ("район", "р-н")
# Result address keys that name a neighbourhood or settlement. Not `county`: in Sofia that
# is the район, which is checked separately.
_RESULT_AREA_KEYS = (
    "suburb", "quarter", "neighbourhood", "residential", "city_district", "borough",
    "village", "hamlet", "town", "isolated_dwelling", "allotments",
)
# Latin letters that source data uses in place of look-alike Cyrillic ones.
_LATIN_TO_CYRILLIC = str.maketrans("AaBEeKkMHOoPpCcTXxy", "АаВЕеКкМНОоРрСсТХху")


class NominatimProvider(BaseGeocodingProvider):
    """
    Geocoding provider using OpenStreetMap Nominatim API.

    IMPORTANT: This provider respects Nominatim Usage Policy:
    https://operations.osmfoundation.org/policies/nominatim/

    - Rate limit: Maximum 1 request per second
    - User-Agent: Must include contact information
    - Caching: Results should be cached to avoid redundant requests
    - No heavy usage: Don't use this for bulk geocoding of thousands of addresses
    """

    # Nominatim API endpoint (official OSM instance)
    BASE_URL = "https://nominatim.openstreetmap.org"

    # Rate limit: 1 request per second (OSM policy)
    MIN_REQUEST_INTERVAL = 1.0  # seconds

    def __init__(self, user_agent: str = "SofiaSchoolComparison/1.0 (contact@example.com)"):
        """
        Initialize Nominatim provider.

        Args:
            user_agent: User-Agent string with contact information (REQUIRED by OSM policy)
        """
        self.user_agent = user_agent
        self._last_request_time: Optional[float] = None
        self._rate_limit_lock = asyncio.Lock()  # Ensure thread-safe rate limiting

    @property
    def provider_name(self) -> str:
        return "nominatim"

    async def _enforce_rate_limit(self):
        """
        Enforce rate limit: wait if necessary to ensure 1 second between requests.

        This is REQUIRED by Nominatim Usage Policy.
        Thread-safe via asyncio.Lock to prevent concurrent requests from bypassing rate limit.
        """
        async with self._rate_limit_lock:
            if self._last_request_time is not None:
                import time
                elapsed = time.monotonic() - self._last_request_time
                if elapsed < self.MIN_REQUEST_INTERVAL:
                    wait_time = self.MIN_REQUEST_INTERVAL - elapsed
                    logger.debug(f"Rate limiting: waiting {wait_time:.2f}s before next Nominatim request")
                    await asyncio.sleep(wait_time)

            import time
            self._last_request_time = time.monotonic()

    def _normalize_bulgarian_address(self, address: str) -> str:
        """
        Normalize Bulgarian address for better Nominatim results.

        Removes city prefixes, street abbreviations, and formatting that confuses geocoding.

        Examples:
            'гр. София, ул. "Брегалница", №48' -> 'Брегалница 48, София'
            'район Панчарево, ж. к. Малинова Долина, ул. "Първа" № 24' -> 'Първа 24, Малинова Долина, Панчарево, София'
        """
        # Remove quotes first (before removing abbreviations)
        logger.debug(f"Before quote removal: {address!r}")
        address = address.replace('"', '').replace('"', '').replace('"', '')
        address = re.sub(r'[„“”]', '', address)
        logger.debug(f"After quote removal: {address!r}")

        # Expand common abbreviated street names from source datasets.
        address = re.sub(r'\bПлачк\.\s*манастир\b', 'Плачковски манастир', address, flags=re.IGNORECASE)

        # Normalize common OCR/transcription issues (Latin H used in Cyrillic words).
        address = re.sub(r'\bH(?=[А-Яа-я])', 'Н', address)

        # Extract locality prefix (гр./с.) and keep it for re-append later.
        # This avoids dropping crucial disambiguation data for village addresses.
        locality = None
        locality_match = re.match(r'^\s*(гр\.|с\.)\s*([^,]+)\s*,\s*(.+)$', address, flags=re.IGNORECASE)
        if locality_match:
            locality = locality_match.group(2).strip()
            address = locality_match.group(3).strip()

        # Remove district prefix (район X,)
        address = re.sub(r'район\s+([^,]+),\s*', r'\1, ', address)

        # Canonicalize neighborhood markers to improve block-based lookup.
        address = re.sub(r'ж\.\s*к\.\s*', 'ж.к. ', address)
        address = re.sub(r'жк\.\s*', 'ж.к. ', address)
        address = re.sub(r'жк\s+', 'ж.к. ', address)

        # Standardize квартал marker.
        address = re.sub(r'кв\.\s*', 'кв. ', address)

        # Some sources use "до бл.18" - convert to "бл. 18" form.
        address = re.sub(r'\s*до\s+бл\.?\s*', ', бл. ', address, flags=re.IGNORECASE)

        # Convert section markers like "I ч." to numeric suffix ("1"), which maps
        # better to neighborhood naming in OSM (e.g., "Връбница 1").
        def _section_to_number(match: re.Match[str]) -> str:
            roman = match.group(1).upper()
            roman_map = {
                "I": "1",
                "II": "2",
                "III": "3",
                "IV": "4",
                "V": "5",
                "VI": "6",
                "VII": "7",
                "VIII": "8",
                "IX": "9",
                "X": "10",
            }
            return f" {roman_map.get(roman, '')} "

        address = re.sub(r'\b([IVX]+)\s*ч\.\s*', _section_to_number, address)

        # Remove entrance hints (вх. A / вх.А и вх.Г).
        address = re.sub(
            r',?\s*вх\.?\s*[A-Za-zА-Яа-я0-9]+(?:\s*и\s*вх\.?\s*[A-Za-zА-Яа-я0-9]+)*',
            '',
            address,
            flags=re.IGNORECASE,
        )

        # Drop notes that only confuse the lookup: "(сграда на ...)", floors, apartments.
        address = re.sub(r'\([^)]*\)?', '', address)
        address = re.sub(
            r',?\s*(?<![A-Za-zА-Яа-я])(?:ет\.|ап\.)\s*[0-9A-Za-zА-Яа-я]+(?:\s*и\s*(?:ет\.\s*)?[0-9]+)*',
            '',
            address,
            flags=re.IGNORECASE,
        )
        address = re.sub(r',?\s*партер\b', '', address, flags=re.IGNORECASE)

        # Remove street abbreviations (бул. BEFORE ул. to avoid partial match)
        logger.debug(f"Before abbreviation removal: {address!r}")
        address = re.sub(r'бул\.\s*', '', address)  # Remove "бул." first
        address = re.sub(r'ул\.\s*', '', address)   # Then remove "ул."
        logger.debug(f"After abbreviation removal: {address!r}")

        # "кв." + block usually maps better as "ж.к." in Sofia datasets.
        if re.search(r'\bкв\.\s*', address, flags=re.IGNORECASE) and re.search(r'бл\.\s*\d+', address):
            address = re.sub(r'\bкв\.\s*', 'ж.к. ', address, count=1, flags=re.IGNORECASE)

        # Remove noisy zoning phrase that doesn't help geocoding precision.
        address = re.sub(r',?\s*Търговска\s+зона\s*,?', ', ', address, flags=re.IGNORECASE)

        # If multiple block numbers are present, keep only the first block token.
        block_matches = list(re.finditer(r'бл\.?\s*\d+[A-Za-zА-Яа-я]?', address, flags=re.IGNORECASE))
        if len(block_matches) > 1:
            first_block = block_matches[0]
            address = f"{address[:first_block.end()]}"

        # Keep only the first block letter when source encodes alternatives (e.g., "510 А,Б").
        address = re.sub(
            r'(бл\.?\s*\d+\s*[A-Za-zА-Яа-я]?),\s*[A-Za-zА-Яа-я]\b',
            r'\1',
            address,
            flags=re.IGNORECASE,
        )

        # Ensure neighborhood and block are comma-separated ("ж.к. X, бл. Y").
        address = re.sub(r'(ж\.к\.\s*[^,]+)\s+(бл\.?\s*\d+)', r'\1, \2', address, count=1, flags=re.IGNORECASE)

        # Replace № with space
        address = address.replace('№', ' ')

        # Clean up extra spaces and commas
        address = re.sub(r'\s+', ' ', address).strip()
        address = re.sub(r',\s*,', ',', address)
        address = re.sub(r'\s+,', ',', address)
        address = address.strip(' ,')

        # Re-append locality if it was stripped from a city/village prefix.
        if locality and locality.lower() not in address.lower():
            address = f"{address}, {locality}" if address else locality

        return address

    @staticmethod
    def _append_city_if_missing(address: str, city: Optional[str]) -> str:
        """Append city context when address does not contain it."""
        if not city:
            return address

        normalized_city = city.strip()
        if not normalized_city:
            return address

        # DB stores "sofia"; use Bulgarian form for Bulgarian addresses.
        if normalized_city.lower() == "sofia":
            normalized_city = "София"

        # Match by comma-delimited tokens to avoid false positives like "София парк".
        tokens = [token.strip().lower() for token in address.split(',') if token.strip()]
        city_present = any(token == normalized_city.lower() for token in tokens)
        if city_present:
            return address
        return f"{address}, {normalized_city}"

    def _build_bulgarian_query_candidates(
        self,
        normalized_address: str,
        city: Optional[str],
        street_fallback_area: Optional[str] = None,
    ) -> list[str]:
        """
        Build fallback query variants for Bulgarian addresses.

        Variants are ordered from most specific to more permissive.
        """
        queries: list[str] = []

        def add(query: str):
            q = re.sub(r'\s+', ' ', query).strip(' ,')
            if q and q not in queries:
                queries.append(q)

        base = self._append_city_if_missing(normalized_address, city)
        add(base)

        # Variant: convert to canonical ж.к. format and normalize block formatting.
        block_variant = base
        block_variant = re.sub(r'\bкв\.\s*', 'ж.к. ', block_variant, flags=re.IGNORECASE)
        block_variant = re.sub(r'бл\.?\s*(\d+)', r'бл. \1', block_variant, flags=re.IGNORECASE)
        block_variant = re.sub(r'(бл\.\s*\d+)\s*[-–]\s*\d+', r'\1', block_variant, flags=re.IGNORECASE)
        add(block_variant)

        # Variant: some Sofia datasets encode block addresses as "<district>, <street_number>, <building_number>, <city>".
        # Example: "ж.к. Дружба 1, 5016, 3, София" -> "ж.к. Дружба 1, бл. 3, София"
        tokens = [t.strip() for t in base.split(',') if t.strip()]
        if (
            len(tokens) >= 4
            and ("ж.к." in tokens[0].lower() or "кв." in tokens[0].lower())
            and re.fullmatch(r'\d{3,5}', tokens[1])
            and re.fullmatch(r'\d+[A-Za-zА-Яа-я-]*', tokens[2])
        ):
            add(f"{tokens[0]}, бл. {tokens[2]}, {tokens[-1]}")

        # Variant: when address has "<street>, <number>, <city>", remove leading neighborhood segment.
        # Example: "Л. Толстой, Генерал Жостов, 1, София" -> "Генерал Жостов 1, София"
        if len(tokens) >= 4 and re.fullmatch(r'\d+[A-Za-zА-Яа-я-]*', tokens[-2]):
            add(f"{tokens[-3]} {tokens[-2]}, {tokens[-1]}")

        # Variant: for block-heavy addresses, search by neighborhood + first block + city.
        block_match = re.search(r'бл\.?\s*(\d+[A-Za-zА-Яа-я]?)', base, flags=re.IGNORECASE)
        if block_match:
            block_token = f"бл. {block_match.group(1)}"
            area_token = None
            for token in tokens:
                lowered = token.lower()
                if (
                    'ж.к.' in lowered or 'кв.' in lowered or
                    'младост' in lowered or 'обеля' in lowered or
                    'връбница' in lowered or 'софия парк' in lowered
                ):
                    area_token = token
                    break
            if area_token:
                city_token = "София" if city and city.lower() == "sofia" else (city or (tokens[-1] if tokens else ""))
                add(f"{area_token}, {block_token}, {city_token}")

        # Variant: street without the house number, which OSM often lacks. Only when the
        # neighbourhood or district is known, so geocode() can check the street is the right
        # one (Sofia has many same-named streets).
        if street_fallback_area:
            street = None
            for i, token in enumerate(tokens):
                if re.match(r'(?:ж\.к\.|кв\.|бл\.)', token, flags=re.IGNORECASE):
                    continue
                numbered = re.fullmatch(r'(.*[А-Яа-я].*?)\s+\d+[A-Za-zА-Яа-я]?', token)
                if numbered and numbered.group(1).strip().lower() == "софия":
                    continue  # "София 1618": city plus postcode, not a street
                if numbered:
                    street = numbered.group(1)
                elif (
                    re.search(r'[А-Яа-я]', token)
                    and i + 1 < len(tokens)
                    and re.fullmatch(r'\d+[A-Za-zА-Яа-я]?', tokens[i + 1])
                ):
                    street = token
                if street:
                    break
            if street:
                add(self._append_city_if_missing(f"{street}, {street_fallback_area}", city))

        return queries

    @staticmethod
    def _area_keyword(name: str) -> Optional[str]:
        """Return the first word of 4+ letters in a place name, lowercased, or None."""
        name = name.translate(_LATIN_TO_CYRILLIC).lower()
        return next((w for w in re.findall(r'[а-я]{4,}', name)), None)

    @staticmethod
    def _address_areas(address: str) -> tuple[list[str], Optional[str]]:
        """Neighbourhood names and the район named in a raw address (e.g. кв. Витоша)."""
        neighbourhoods: list[str] = []
        district = None
        for match in _AREA_MARKER_RE.finditer(address):
            name = match.group(2).strip()
            if match.group(1).lower() in _DISTRICT_MARKERS:
                district = district or name
            else:
                neighbourhoods.append(name)
        return neighbourhoods, district

    def _address_area_keywords(self, address: str) -> list[str]:
        """Keywords of the neighbourhoods named in a raw address."""
        keywords = []
        for name in self._address_areas(address)[0]:
            keyword = self._area_keyword(name)
            if keyword and keyword not in keywords:
                keywords.append(keyword)
        return keywords

    def _result_matches_expected_area(
        self,
        result: dict,
        area_keywords: list[str],
        district: Optional[str],
    ) -> bool:
        """
        Check that a result lies in the neighbourhood/district the address names.

        Sofia has many same-named streets (ул. Вършец, ул. Мургаш, ул. Йордан Стубел), so a
        street match alone can land in another part of the city. A result passes when it is
        in the known district and names at least one of the address's neighbourhoods. A
        street-level match in a neighbouring quarter is rejected even in the right district
        (кв. Кремиковци vs Горни Богров): no pin is safer than a wrong one. An exact house
        number in the right district passes. Missing result hierarchy is not a mismatch.
        """
        address = result.get("address") or {}
        if not isinstance(address, dict):
            return True
        area_text = " ".join(
            v.translate(_LATIN_TO_CYRILLIC).lower()
            for key in _RESULT_AREA_KEYS
            if isinstance((v := address.get(key)), str)
        )
        district_keyword = self._area_keyword(district) if district else None
        if district_keyword:
            # Sofia results carry the район as `county`; villages in the municipality have
            # no county, but their display name still lists the район (Казичене, Панчарево).
            county = address.get("county")
            if isinstance(county, str) and county.strip():
                district_text = county
            else:
                # Without the road itself, which may share the district's name (ул. Оборище).
                district_text = ", ".join(
                    part for part in (result.get("display_name") or "").split(",")
                    if part.strip() != address.get("road")
                )
            if district_text and district_keyword not in district_text.translate(_LATIN_TO_CYRILLIC).lower():
                return False
            if district_text and address.get("house_number"):
                # The exact house in the right district; OSM often files it under a
                # neighbouring quarter (кв. Княжево → Карпузица).
                return True
        if area_keywords and area_text:
            return any(k in area_text for k in area_keywords)
        return True

    @staticmethod
    def _expected_city_tokens(expected_city: Optional[str]) -> set[str]:
        """Return acceptable city tokens for matching Nominatim results."""
        if not expected_city:
            return set()

        city = expected_city.strip().lower()
        if not city:
            return set()

        if city in {"sofia", "софия", "sofiya"}:
            return {"sofia", "софия", "stolichna", "столична"}

        return {city}

    @staticmethod
    def _result_city_tokens(result: dict) -> set[str]:
        """Extract normalized city-like tokens from Nominatim address details."""
        address = result.get("address") or {}
        if not isinstance(address, dict):
            return set()

        keys = ("city", "town", "village", "municipality", "county", "state_district", "state")
        tokens: set[str] = set()
        for key in keys:
            value = address.get(key)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized:
                    tokens.add(normalized)
        return tokens

    def _result_matches_expected_city(self, result: dict, expected_city: Optional[str]) -> bool:
        """
        Validate that a Nominatim result is in the expected city context.

        This prevents false positives such as matching street name "София" in another city.
        """
        expected_tokens = self._expected_city_tokens(expected_city)
        if not expected_tokens:
            return True

        result_tokens = self._result_city_tokens(result)
        if not result_tokens:
            # Be permissive when address hierarchy is unavailable.
            return True

        return bool(expected_tokens.intersection(result_tokens))

    @staticmethod
    def _house_number_was_asked(house_number: object, query: str) -> bool:
        """Whether a result's house number is one the query asked for.

        Nominatim can answer "Седмочисленици 23" with a shop at № 9 on the same street, or
        a street-only query with some house on it: right street, not the building.
        """
        if not isinstance(house_number, str):
            return False
        number = re.search(r"\d+", house_number)
        return bool(number) and number.group().lstrip("0") in {
            n.lstrip("0") for n in re.findall(r"\d+", query)
        }

    @staticmethod
    def _is_area_level_result(result: dict) -> bool:
        """Return whether a Nominatim result is a whole settlement/area, not a place in it."""
        result_type = result.get("addresstype") or result.get("type")
        return result_type in AREA_LEVEL_RESULT_TYPES

    async def geocode(
        self,
        address: str,
        country_code: str = "bg",
        school_name: Optional[str] = None,
        city: Optional[str] = None,
        district: Optional[str] = None,
    ) -> GeocodingResult:
        """
        Geocode an address using Nominatim.

        Args:
            address: Full address string (e.g., "ул. Иван Вазов 15, София")
            country_code: ISO country code (default: "bg")
            school_name: Optional school name (not used by Nominatim)
            city: Optional city name (not used by Nominatim - address should be complete)
            district: Optional known district (район), checked against the result

        Returns:
            GeocodingResult with coordinates or error
        """
        # Build query candidates (normalization + optional fallback variants).
        if country_code == "bg":
            normalized_address = self._normalize_bulgarian_address(address)
            neighbourhoods, address_district = self._address_areas(address)
            area_keywords = self._address_area_keywords(address)
            # OSM `county` is the район only inside Sofia; elsewhere it is the municipality.
            is_sofia = (city or "").strip().lower() == "sofia"
            district = (district or address_district) if is_sofia else None
            # Most specific named neighbourhood (the last one), else the district.
            street_fallback_area = next(
                (a for a in reversed(neighbourhoods) if self._area_keyword(a)), None
            ) or (district if district and self._area_keyword(district) else None)
            search_queries = self._build_bulgarian_query_candidates(
                normalized_address, city, street_fallback_area=street_fallback_area
            )
            logger.debug(f"Normalized address: '{address}' -> '{normalized_address}'")
        else:
            search_queries = [address]
            area_keywords = []

        rejected_area_level = False
        rejected_area_mismatch = False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for query in search_queries:
                    # Enforce rate limit between each request.
                    await self._enforce_rate_limit()

                    response = await client.get(
                        f"{self.BASE_URL}/search",
                        params={
                            "q": query,
                            "format": "json",
                            "countrycodes": country_code,
                            "limit": 1,
                            "addressdetails": 1,
                        },
                        headers={
                            "User-Agent": self.user_agent,
                        },
                    )
                    response.raise_for_status()

                    results = response.json()
                    if not results:
                        continue

                    # Get first (best) result
                    result = results[0]
                    if country_code == "bg" and city and not self._result_matches_expected_city(result, city):
                        logger.warning(
                            f"Nominatim: Rejected result for query '{query}' due to city mismatch. "
                            f"Expected '{city}', got city tokens {sorted(self._result_city_tokens(result))}"
                        )
                        continue
                    if country_code == "bg" and not self._result_matches_expected_area(
                        result, area_keywords, district
                    ):
                        logger.warning(
                            "Nominatim: Rejected result for query '%s' outside the address's "
                            "neighbourhood/district (%s, %s): %s",
                            query,
                            area_keywords,
                            district,
                            result.get("display_name"),
                        )
                        rejected_area_mismatch = True
                        continue
                    if self._is_area_level_result(result):
                        logger.warning(
                            "Nominatim: Rejected area-level result (%s) for query '%s': %s",
                            result.get("addresstype") or result.get("type"),
                            query,
                            result.get("display_name"),
                        )
                        rejected_area_level = True
                        continue

                    lat = float(result["lat"])
                    lng = float(result["lon"])
                    formatted_address = result.get("display_name")
                    address_details = result.get("address") or {}
                    precision = (
                        "exact"
                        if self._house_number_was_asked(address_details.get("house_number"), query)
                        else "approximate"
                    )

                    logger.info(f"Nominatim: Successfully geocoded '{address}' using query '{query}' → ({lat}, {lng})")

                    return GeocodingResult(
                        lat=lat,
                        lng=lng,
                        success=True,
                        provider=self.provider_name,
                        formatted_address=formatted_address,
                        method="nominatim_address",
                        precision=precision,
                    )

                logger.warning(
                    f"Nominatim: No results found for address: {address}. "
                    f"Tried queries: {search_queries}"
                )
                return GeocodingResult(
                    success=False,
                    error=(
                        AREA_LEVEL_MATCH_ERROR if rejected_area_level
                        else AREA_MISMATCH_ERROR if rejected_area_mismatch
                        else "No results found"
                    ),
                    provider=self.provider_name,
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"Nominatim HTTP error for '{address}': {e}")
            return GeocodingResult(
                success=False,
                error=f"HTTP {e.response.status_code}",
                provider=self.provider_name,
            )
        except Exception as e:
            logger.error(f"Nominatim error geocoding '{address}': {e}")
            return GeocodingResult(
                success=False,
                error=str(e),
                provider=self.provider_name,
            )

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult:
        """
        Reverse geocode coordinates to an address.

        Args:
            lat: Latitude
            lng: Longitude

        Returns:
            GeocodingResult with formatted address
        """
        # Enforce rate limit
        await self._enforce_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/reverse",
                    params={
                        "lat": lat,
                        "lon": lng,
                        "format": "json",
                        "addressdetails": 1,
                    },
                    headers={
                        "User-Agent": self.user_agent,
                    },
                )
                response.raise_for_status()

                result = response.json()

                if "error" in result:
                    logger.warning(f"Nominatim: No address found for ({lat}, {lng})")
                    return GeocodingResult(
                        success=False,
                        error=result["error"],
                        provider=self.provider_name,
                    )

                formatted_address = result.get("display_name")

                logger.info(f"Nominatim: Successfully reverse geocoded ({lat}, {lng}) → '{formatted_address}'")

                return GeocodingResult(
                    lat=lat,
                    lng=lng,
                    success=True,
                    provider=self.provider_name,
                    formatted_address=formatted_address,
                    method="nominatim_address",
                    precision="exact",
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"Nominatim HTTP error for ({lat}, {lng}): {e}")
            return GeocodingResult(
                success=False,
                error=f"HTTP {e.response.status_code}",
                provider=self.provider_name,
            )
        except Exception as e:
            logger.error(f"Nominatim error reverse geocoding ({lat}, {lng}): {e}")
            return GeocodingResult(
                success=False,
                error=str(e),
                provider=self.provider_name,
            )
