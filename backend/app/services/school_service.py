import re
from typing import Optional

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift


SOFIA_MAP_BOUNDS = {
    "south": 42.55,
    "west": 23.15,
    "north": 42.85,
    "east": 23.55,
}


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
        normalized = SchoolService._normalize_city_filter(city)
        if normalized != "sofia":
            return None
        return and_(
            location_model.lat >= SOFIA_MAP_BOUNDS["south"],
            location_model.lat <= SOFIA_MAP_BOUNDS["north"],
            location_model.lng >= SOFIA_MAP_BOUNDS["west"],
            location_model.lng <= SOFIA_MAP_BOUNDS["east"],
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

    def _base_query(self):
        """Base query with all relationships eager-loaded."""
        return select(School).options(
            selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
            selectinload(School.pricing),
            selectinload(School.exam_results),
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
            self._base_query()
            .join(SchoolLocation)
            .join(SchoolLocationAgeGroupShift, SchoolLocationAgeGroupShift.location_id == SchoolLocation.id)
            .where(SchoolLocationAgeGroupShift.age_group == age_group)
            .where(School.country_code == country_code)
            .where(self._scoped_resolved_location_clause(SchoolLocation, city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await self.db.execute(query)
        return result.scalars().unique().all()

    async def get_school_with_details(self, school_id: int) -> School | None:
        """Get a school with all related data loaded."""
        query = (
            self._base_query()
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
            List of schools matching the filters with all relationships eager-loaded
        """
        query = (
            self._base_query()
            .where(School.country_code == country_code)
            .where(self._has_resolved_location(city))
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
                .where(self._scoped_resolved_location_clause(SchoolLocation, city))
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
            attributes = school.attributes or {}

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
            select(School.attributes)
            .where(School.country_code == country_code)
            .where(self._has_resolved_location(city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await self.db.execute(query)
        rows = result.scalars().all()

        categories = {
            "language_focus_pairs": set(),
            "language_focus_languages": set(),
            "language_focus_levels": set(),
            "special_programs": set(),
            "facilities": set(),
            "teaching_approach": set(),
        }

        for attrs in rows:
            if not attrs:
                continue
            language_values = attrs.get("language_focus") or []
            for value in language_values:
                if isinstance(value, dict):
                    language = value.get("language")
                    level = value.get("level")
                    if language:
                        categories["language_focus_languages"].add(language)
                    if level:
                        categories["language_focus_levels"].add(level)
                    if language and level:
                        categories["language_focus_pairs"].add(f"{language}:{level}")
                elif isinstance(value, str):
                    categories["language_focus_pairs"].add(value)
                    if ":" in value:
                        language, level = value.split(":", 1)
                        if language:
                            categories["language_focus_languages"].add(language)
                        if level:
                            categories["language_focus_levels"].add(level)

            for key in ["special_programs", "facilities", "teaching_approach"]:
                values = attrs.get(key) or []
                for value in values:
                    categories[key].add(value)

        return {key: sorted(values) for key, values in categories.items()}

    async def search_schools(
        self,
        search_query: str,
        country_code: str = "bg",
        city: Optional[str] = "sofia",
        limit: int = 10,
    ) -> list[School]:
        """
        Search schools by name or location.

        Args:
            search_query: Search term (already sanitized)
            country_code: Country to filter by
            limit: Maximum number of results to return

        Returns:
            List of schools matching the search query
        """
        # Escape special SQL ILIKE wildcards to prevent unintended pattern matching.
        # Parents often type school numbers without the Bulgarian "№" marker
        # (for example "ДГ 5" instead of stored "ДГ №5"), so search both forms.
        patterns = [
            f"%{variant.replace('%', '\\%').replace('_', '\\_')}%"
            for variant in self._school_search_variants(search_query)
        ]
        search_fields = (
            School.name_i18n["bg"].as_string(),
            School.name_i18n["en"].as_string(),
            School.attributes["display_name_i18n"]["bg"].as_string(),
            School.attributes["display_name_i18n"]["en"].as_string(),
            SchoolLocation.address_i18n["bg"].as_string(),
            SchoolLocation.address_i18n["en"].as_string(),
        )
        search_clauses = [
            field.ilike(pattern, escape="\\")
            for pattern in patterns
            for field in search_fields
        ]

        # Match on school names, branded display names, and location addresses.
        # Query matching ids first so multiple matching locations do not duplicate
        # schools or consume the result limit.
        city_clause = self._city_clause(city)
        matching_school_ids = (
            select(School.id)
            .outerjoin(SchoolLocation, SchoolLocation.school_id == School.id)
            .where(School.country_code == country_code)
            .where(self._has_resolved_location(city))
            .where(or_(*search_clauses))
            .distinct()
            .limit(limit)
        )
        if city_clause is not None:
            matching_school_ids = matching_school_ids.where(city_clause)
        matching_school_ids = matching_school_ids.subquery()

        query = (
            self._base_query()
            .join(matching_school_ids, School.id == matching_school_ids.c.id)
        )
        result = await self.db.execute(query)
        return result.scalars().unique().all()

    @staticmethod
    def _school_search_variants(search_query: str) -> list[str]:
        query = re.sub(r"\s+", " ", search_query.strip())
        variants = [query]

        if "№" in query:
            variants.append(re.sub(r"\s*№\s*", " ", query))
        else:
            variants.append(re.sub(r"\b(\D+?)\s+(\d+)\b", r"\1 №\2", query, count=1))
            variants.append(re.sub(r"\b(\D+?)\s+(\d+)\b", r"\1 № \2", query, count=1))

        out: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            normalized = re.sub(r"\s+", " ", variant).strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(normalized)
        return out

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
            self._base_query()
            .where(School.id.in_(school_ids))
        )
        result = await self.db.execute(query)
        return result.scalars().unique().all()
