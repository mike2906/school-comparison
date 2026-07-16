import re
from typing import Optional

from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift
from app.services.geocoding.bounds import SOFIA_MUNICIPALITY_BOUNDS, get_city_bounds
from app.utils.i18n_resolver import resolve_address_i18n, resolve_name_i18n
from app.utils.school_attributes import build_filterable_attributes
from app.utils.website_data import (
    WEBSITE_DATA_WITHHELD_KEY,
    WEBSITE_PUBLISHABLE_STATUSES,
    attributes_for_publication,
)

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
            selectinload(School.pricing),
        )

    def _detail_query(self):
        """Query the complete relationship graph used by detail and compare responses."""
        return self._list_query().options(
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
                attributes_for_publication(school.attributes, school.scrape_status)
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
            select(School.attributes, School.scrape_status)
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
        )
        city_clause = self._city_clause(city)
        if city_clause is not None:
            query = query.where(city_clause)
        result = await self.db.execute(query)
        rows = result.all()

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
        for attrs, scrape_status in rows:
            if not attrs:
                continue
            filterable = build_filterable_attributes(
                attributes_for_publication(attrs, scrape_status)
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
        variants = self._school_search_variants(search_query)
        if not variants:
            return []

        escaped_variants = [
            variant.replace("%", r"\%").replace("_", r"\_")
            for variant in variants
        ]
        patterns = [f"%{variant}%" for variant in escaped_variants]
        registry_and_address_fields = (
            School.name_i18n["bg"].as_string(),
            School.name_i18n["en"].as_string(),
            SchoolLocation.address_i18n["bg"].as_string(),
            SchoolLocation.address_i18n["en"].as_string(),
        )
        display_fields = (
            School.attributes["display_name_i18n"]["bg"].as_string(),
            School.attributes["display_name_i18n"]["en"].as_string(),
        )
        registry_and_address_clauses = [
            field.ilike(pattern, escape="\\")
            for pattern in patterns
            for field in registry_and_address_fields
        ]
        display_match_clauses = [
            field.ilike(pattern, escape="\\")
            for pattern in patterns
            for field in display_fields
        ]
        evidence_signals = cast(School.attributes["display_name_evidence"]["signals"], String)
        corroborated_display_clause = and_(
            or_(*display_match_clauses),
            School.scrape_status.in_(WEBSITE_PUBLISHABLE_STATUSES),
            School.attributes[WEBSITE_DATA_WITHHELD_KEY].as_boolean().is_not(True),
            evidence_signals.like('%"website_domain_alias_match"%'),
            evidence_signals.like('%"repeated_on_page_identity"%'),
        )

        # Match IDs first so multiple matching locations do not duplicate schools
        # or consume the result limit, while keeping autocomplete work in SQL.
        city_clause = self._city_clause(city)
        matching_school_ids = (
            select(School.id)
            .outerjoin(SchoolLocation, SchoolLocation.school_id == School.id)
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
            .where(or_(*registry_and_address_clauses, corroborated_display_clause))
            .distinct()
            .limit(limit)
        )
        if city_clause is not None:
            matching_school_ids = matching_school_ids.where(city_clause)
        matching_school_ids = matching_school_ids.subquery()

        matched_ids = (
            await self.db.execute(select(matching_school_ids.c.id))
        ).scalars().all()
        if len(matched_ids) < limit:
            matched_ids.extend(
                await self._search_resolved_fallback_ids(
                    variants=variants,
                    country_code=country_code,
                    city=city,
                    exclude_ids=set(matched_ids),
                    limit=limit - len(matched_ids),
                )
            )
        if not matched_ids:
            return []

        query = (
            self._list_query()
            .where(School.id.in_(matched_ids))
        )
        result = await self.db.execute(query)
        schools_by_id = {school.id: school for school in result.scalars().unique().all()}
        return [schools_by_id[school_id] for school_id in matched_ids if school_id in schools_by_id]

    async def _search_resolved_fallback_ids(
        self,
        *,
        variants: list[str],
        country_code: str,
        city: Optional[str],
        exclude_ids: set[int],
        limit: int,
    ) -> list[int]:
        """Search derived display fallbacks without hydrating the full response graph."""
        if limit <= 0:
            return []

        folded_variants = [variant.casefold() for variant in variants]
        city_clause = self._city_clause(city)
        query = (
            select(
                School.id,
                School.name_i18n,
                School.attributes,
                School.scrape_status,
                SchoolLocation.address_i18n,
            )
            .outerjoin(SchoolLocation, SchoolLocation.school_id == School.id)
            .where(School.country_code == country_code)
            .where(self._has_listable_location(city))
        )
        if city_clause is not None:
            query = query.where(city_clause)
        if exclude_ids:
            query = query.where(School.id.not_in(exclude_ids))

        result = await self.db.execute(query)
        matched_ids: list[int] = []
        seen_ids: set[int] = set()
        for school_id, name_i18n, attributes, scrape_status, address_i18n in result.all():
            if school_id in seen_ids:
                continue
            searchable_values = list((name_i18n or {}).values())
            searchable_values.extend(
                resolve_name_i18n(
                    name_i18n,
                    attributes_for_publication(attributes, scrape_status),
                ).values()
            )
            searchable_values.extend((address_i18n or {}).values())
            searchable_values.extend(resolve_address_i18n(address_i18n).values())

            if not any(
                variant in str(value).casefold()
                for variant in folded_variants
                for value in searchable_values
            ):
                continue

            seen_ids.add(school_id)
            matched_ids.append(school_id)
            if len(matched_ids) >= limit:
                break

        return matched_ids

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
            self._detail_query()
            .where(School.id.in_(school_ids))
        )
        result = await self.db.execute(query)
        return result.scalars().unique().all()
