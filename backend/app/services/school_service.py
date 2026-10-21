import asyncio
from typing import Optional

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.models.pricing import Pricing
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.models.source_page import SourcePage
from app.services.geocoding.bounds import SOFIA_MUNICIPALITY_BOUNDS, get_city_bounds
from app.services.geocoding.service import LISTABLE_UNPINNED_REASONS
from app.utils import school_search
from app.utils.i18n_resolver import resolve_address_i18n, resolve_name_i18n
from app.utils.school_attributes import build_filterable_attributes
from app.utils.school_facts import publication_attributes
from app.utils.website_data import attributes_for_publication
from app.utils.website_name import PUBLISHED_KEY as WEBSITE_NAME_KEY
from app.utils.website_name import address_name, shown_website_name

SOFIA_MAP_BOUNDS = SOFIA_MUNICIPALITY_BOUNDS

class SchoolService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _resolved_location_clause(location_model=SchoolLocation):
        return and_(
            location_model.lat.is_not(None),
            location_model.lng.is_not(None),
        )

    @staticmethod
    def _has_resolved_location(city: Optional[str] = None):
        resolved_location = aliased(SchoolLocation)
        return exists(
            select(1)
            .select_from(resolved_location)
            .where(
                resolved_location.school_id == School.id,
                SchoolService._scoped_resolved_location_clause(resolved_location, city),
            )
            .correlate(School)
        )

    @staticmethod
    def _terminal_location_clause(location_model=SchoolLocation):
        """Null coordinates with durable failure evidence are listable, not mappable."""
        provider = location_model.geocode_meta["provider"].as_string()
        reason = location_model.geocode_meta["rejection_reason"].as_string()
        return and_(
            location_model.lat.is_(None),
            location_model.lng.is_(None),
            location_model.geocode_meta["status"].as_string().in_(("failed", "rejected")),
            provider.is_not(None),
            func.length(func.trim(provider)) > 0,
            reason.is_not(None),
            func.length(func.trim(reason)) > 0,
            func.trim(reason).in_(tuple(sorted(LISTABLE_UNPINNED_REASONS))),
        )

    @staticmethod
    def _listable_location_clause(location_model=SchoolLocation, city: Optional[str] = None):
        return or_(
            SchoolService._scoped_resolved_location_clause(location_model, city),
            SchoolService._terminal_location_clause(location_model),
        )

    @staticmethod
    def _has_listable_location(city: Optional[str] = None):
        location = aliased(SchoolLocation)
        return exists(
            select(1)
            .select_from(location)
            .where(
                location.school_id == School.id,
                SchoolService._listable_location_clause(location, city),
            )
            .correlate(School)
        )

    @staticmethod
    def _normalize_city_filter(city: Optional[str]) -> Optional[str]:
        if city is None:
            return None
        normalized = city.strip().lower()
        if not normalized or normalized == "all":
            return None
        return normalized

    @staticmethod
    def _city_clause(city: Optional[str]):
        normalized = SchoolService._normalize_city_filter(city)
        if normalized is None:
            return None
        return func.lower(School.city) == normalized

    @staticmethod
    def _location_bounds_clause(location_model=SchoolLocation, city: Optional[str] = None):
        bounds = get_city_bounds("bg", SchoolService._normalize_city_filter(city))
        if bounds is None:
            return None
        return and_(
            location_model.lat >= bounds["south"],
            location_model.lat <= bounds["north"],
            location_model.lng >= bounds["west"],
            location_model.lng <= bounds["east"],
        )

    @staticmethod
    def _scoped_resolved_location_clause(location_model=SchoolLocation, city: Optional[str] = None):
        bounds_clause = SchoolService._location_bounds_clause(location_model, city)
        if bounds_clause is None:
            return SchoolService._resolved_location_clause(location_model)
        return and_(
            SchoolService._resolved_location_clause(location_model),
            bounds_clause,
        )

    def _list_query(self):
        """Query relationships required by list and search responses."""
        return select(School).options(
            selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
            # The publish gate reads the linked page's validity; load only that column
            # so the response query never pulls page markdown.
            selectinload(School.pricing)
            .selectinload(Pricing.source_page)
            .load_only(SourcePage.id, SourcePage.is_valid),
            selectinload(School.exam_results),
        )

    def _detail_query(self):
        """Query the complete relationship graph used by detail and compare responses."""
        return self._list_query().options(
            selectinload(School.field_sources),
        )

    async def get_schools_by_age_group(
        self,
        age_group: str,
        country_code: str = "bg",
        city: Optional[str] = "sofia",
    ) -> list[School]:
        """Get all schools that have locations for a specific age group."""
        query = (
            self._list_query()
            .join(SchoolLocation)
            .join(SchoolLocationAgeGroupShift, SchoolLocationAgeGroupShift.location_id == SchoolLocation.id)
            .where(SchoolLocationAgeGroupShift.age_group == age_group)
            .where(School.country_code == country_code)
            .where(self._listable_location_clause(SchoolLocation, city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await self.db.execute(query)
        return result.scalars().unique().all()

    async def get_school_with_details(self, school_id: int) -> School | None:
        """Get a school with all related data loaded."""
        query = (
            self._detail_query()
            .where(School.id == school_id)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_schools_filtered(
        self,
        country_code: str = "bg",
        city: Optional[str] = "sofia",
        age_group: Optional[str] = None,
        school_type: Optional[str] = None,
        education_level: Optional[str] = None,
        include_crossover: bool = False,
        language_focus: Optional[list[str]] = None,
        special_programs: Optional[list[str]] = None,
        facilities: Optional[list[str]] = None,
        teaching_approach: Optional[list[str]] = None,
    ) -> list[School]:
        """
        List schools with optional filters.

        Args:
            country_code: Country to filter by
            age_group: Filter by age group
            school_type: Filter by school type (state, private, international)
            education_level: Filter by education level
            include_crossover: When filtering for preschool age group, include both
                              kindergartens and primary schools with preschool programs

        Returns:
            List of schools matching the filters with list-response relationships loaded
        """
        query = (
            self._list_query()
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)

        # Filter by age group if specified
        if age_group:
            query = (
                query
                .join(SchoolLocation)
                .join(SchoolLocationAgeGroupShift, SchoolLocationAgeGroupShift.location_id == SchoolLocation.id)
                .where(SchoolLocationAgeGroupShift.age_group == age_group)
                .where(self._listable_location_clause(SchoolLocation, city))
            )

        # Apply education_level filter. Preschool is special:
        # - kindergarten => kindergarten-only preschool results
        # - primary => school-side preschool results (including all-through schools)
        # - include_crossover => no education-level restriction
        if education_level:
            if age_group == "preschool" and include_crossover:
                pass
            elif age_group == "preschool" and education_level == "primary":
                query = query.where(School.education_level != "kindergarten")
            else:
                query = query.where(School.education_level == education_level)

        # Filter by school type if specified
        if school_type:
            query = query.where(School.school_type == school_type)

        result = await self.db.execute(query)
        schools = result.scalars().unique().all()

        if not any([language_focus, special_programs, facilities, teaching_approach]):
            return schools

        def matches_filter(school: School) -> bool:
            # Match against the same projection the API serves, not the raw JSONB:
            # facilities/programs/languages only exist nested inside `attributes.extracted`.
            attributes = build_filterable_attributes(
                publication_attributes(
                    school.attributes, school.scrape_status, school.website_url, school.education_level
                ),
                school.school_type,
            )

            if language_focus:
                values = attributes.get("language_focus") or []
                focus_pairs = set()
                for item in values:
                    if isinstance(item, dict):
                        language = item.get("language")
                        level = item.get("level")
                        if language and level:
                            focus_pairs.add(f"{language}:{level}")
                        elif language:
                            focus_pairs.add(language)
                    elif isinstance(item, str):
                        focus_pairs.add(item)

                if not any(value in focus_pairs for value in language_focus):
                    return False

            if special_programs:
                values = attributes.get("special_programs") or []
                if not any(value in values for value in special_programs):
                    return False

            if facilities:
                values = attributes.get("facilities") or []
                if not any(value in values for value in facilities):
                    return False

            if teaching_approach:
                values = attributes.get("teaching_approach") or []
                if not any(value in values for value in teaching_approach):
                    return False

            return True

        return [school for school in schools if matches_filter(school)]

    async def get_available_filters(
        self,
        country_code: str = "bg",
        city: Optional[str] = "sofia",
    ) -> dict[str, list[str]]:
        query = (
            select(
                School.attributes, School.scrape_status, School.school_type, School.website_url, School.education_level
            )
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await self.db.execute(query)
        rows = result.all()
        # The projection below is CPU work for every school: keep it off the event loop.
        return await asyncio.to_thread(self._filter_options, rows)

    @staticmethod
    def _filter_options(rows) -> dict[str, list[str]]:
        categories = {
            "language_focus_pairs": set(),
            "language_focus_languages": set(),
            "language_focus_levels": set(),
            "special_programs": set(),
            "facilities": set(),
            "teaching_approach": set(),
        }

        # Options come from the same filterable projection the list endpoint matches
        # against, so emitted options can never diverge from what actually matches.
        # facilities/special_programs/teaching_approach are already mapped onto the
        # controlled vocabulary there (P1.9), so only canonical tags are surfaced.
        for attrs, scrape_status, school_type, website_url, education_level in rows:
            if not attrs:
                continue
            filterable = build_filterable_attributes(
                publication_attributes(attrs, scrape_status, website_url, education_level), school_type
            )

            for value in filterable.get("language_focus") or []:
                language = value.get("language")
                level = value.get("level")
                if language:
                    categories["language_focus_languages"].add(language)
                if level:
                    categories["language_focus_levels"].add(level)
                if language and level:
                    categories["language_focus_pairs"].add(f"{language}:{level}")

            for key in ["special_programs", "facilities", "teaching_approach"]:
                for value in filterable.get(key) or []:
                    categories[key].add(value)

        return {key: sorted(values) for key, values in categories.items()}

    async def search_index(
        self, country_code: str = "bg", city: Optional[str] = "sofia"
    ) -> dict[int, school_search.PreparedSchool]:
        """The names and addresses name search matches against, for every listed school.

        Building it projects every school's attributes, so the API keeps it cached
        (`routers/schools.py`) instead of building it on each search.
        """
        city_clause = self._city_clause(city)
        query = (
            select(
                School.id,
                School.name_i18n,
                School.attributes,
                School.scrape_status,
                School.website_url,
                SchoolLocation.address_i18n,
            )
            .outerjoin(SchoolLocation, SchoolLocation.school_id == School.id)
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
        )
        if city_clause is not None:
            query = query.where(city_clause)
        rows = (await self.db.execute(query)).all()
        # CPU work for every school: keep it off the event loop.
        return await asyncio.to_thread(self._search_entries, rows)

    @staticmethod
    def _search_entries(rows) -> dict[int, school_search.PreparedSchool]:
        schools: dict[int, dict] = {}
        for school_id, name_i18n, attributes, scrape_status, website_url, address_i18n in rows:
            entry = schools.get(school_id)
            if entry is None:
                name_i18n = name_i18n or {}
                names = list(name_i18n.values())
                # Only the website name of ``publication_attributes`` matters for a name;
                # building the shown facts for every school on each search is not needed.
                public = attributes_for_publication(attributes, scrape_status)
                # The website's own Latin name, also when a corroborated display name wins.
                if website_name := shown_website_name(attributes, scrape_status, website_url):
                    public[WEBSITE_NAME_KEY] = website_name
                    names.append(website_name)
                names.extend(resolve_name_i18n(name_i18n, public).values())
                entry = schools[school_id] = {
                    "registry_name": name_i18n.get("bg", ""),
                    "names": names,
                    # A brand written only in its logo has its one Latin spelling in the web
                    # address (busy-school.com); ranked with addresses, below any name match.
                    "addresses": [address] if (address := address_name(website_url, scrape_status)) else [],
                }
            entry["addresses"].extend((address_i18n or {}).values())
            entry["addresses"].extend(resolve_address_i18n(address_i18n).values())
        return {school_id: school_search.PreparedSchool(**entry) for school_id, entry in schools.items()}

    async def search_schools(
        self,
        search_query: str,
        country_code: str = "bg",
        city: Optional[str] = "sofia",
        limit: int = 10,
        index: Optional[dict[int, school_search.PreparedSchool]] = None,
    ) -> list[School]:
        """
        Search schools by name (registry and public display names) or address.

        Matching and ranking rules (type abbreviations, "№"/ordinals, curated acronyms,
        transliteration) live in app/utils/school_search.py. Display names go through
        the same publication predicate as response serialization. ``index`` is a cached
        `search_index` for the same country and city; without one it is built here.
        """
        tokens = school_search.query_tokens(search_query)
        if not tokens:
            return []
        if index is None:
            index = await self.search_index(country_code, city)

        ranked: list[tuple[int, int, int]] = []
        for school_id, school in index.items():
            rank = school_search.rank_prepared(tokens, school)
            if rank is not None:
                ranked.append((rank, len(school.registry), school_id))
        matched_ids = [school_id for _, _, school_id in sorted(ranked)[:limit]]
        if not matched_ids:
            return []

        # The same scope as the index, on current rows: a cached index can be minutes old.
        query = (
            self._list_query()
            .where(School.id.in_(matched_ids))
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await self.db.execute(query)
        schools_by_id = {school.id: school for school in result.scalars().unique().all()}
        return [schools_by_id[school_id] for school_id in matched_ids if school_id in schools_by_id]

    async def get_schools_by_ids(self, school_ids: list[int]) -> list[School]:
        """
        Get multiple schools by their IDs with all relationships eager-loaded.

        Args:
            school_ids: List of school IDs to fetch

        Returns:
            List of schools matching the IDs
        """
        if not school_ids:
            return []

        query = (
            self._detail_query()
            .where(School.id.in_(school_ids))
        )
        result = await self.db.execute(query)
        return result.scalars().unique().all()
