from typing import Optional

from sqlalchemy import select, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift


class SchoolService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _base_query(self):
        """Base query with all relationships eager-loaded."""
        return select(School).options(
            selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
            selectinload(School.pricing),
            selectinload(School.exam_results),
            selectinload(School.field_sources),
        )

    async def get_schools_by_age_group(self, age_group: str, country_code: str = "bg") -> list[School]:
        """Get all schools that have locations for a specific age group."""
        query = (
            self._base_query()
            .join(SchoolLocation)
            .join(SchoolLocationAgeGroupShift, SchoolLocationAgeGroupShift.location_id == SchoolLocation.id)
            .where(SchoolLocationAgeGroupShift.age_group == age_group)
            .where(School.country_code == country_code)
        )
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
        query = self._base_query().where(School.country_code == country_code)

        # Filter by age group if specified
        if age_group:
            query = (
                query
                .join(SchoolLocation)
                .join(SchoolLocationAgeGroupShift, SchoolLocationAgeGroupShift.location_id == SchoolLocation.id)
                .where(SchoolLocationAgeGroupShift.age_group == age_group)
            )

        # Apply education_level filter unless include_crossover is true for preschool
        # This allows showing both kindergartens and primary schools for preschool age
        if education_level and not (age_group == "preschool" and include_crossover):
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

    async def get_available_filters(self, country_code: str = "bg") -> dict[str, list[str]]:
        query = select(School.attributes).where(School.country_code == country_code)
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

    async def search_schools(self, search_query: str, country_code: str = "bg", limit: int = 10) -> list[School]:
        """
        Search schools by name.

        Args:
            search_query: Search term (already sanitized)
            country_code: Country to filter by
            limit: Maximum number of results to return

        Returns:
            List of schools matching the search query
        """
        # Escape special SQL ILIKE wildcards to prevent unintended pattern matching
        sanitized_query = search_query.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{sanitized_query}%"

        # Use cast to String for cross-database compatibility (works on both PostgreSQL and SQLite)
        query = (
            self._base_query()
            .where(School.country_code == country_code)
            .where(
                (cast(School.name_i18n, String).ilike(pattern, escape="\\"))
            )
            .limit(limit)
        )
        result = await self.db.execute(query)
        return result.scalars().all()

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
