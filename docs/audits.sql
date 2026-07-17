-- Pre/post-refresh data-quality audits for the Sofia launch dataset.
-- Run from the repository root:
--   docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d sofia_schools \
--     < docs/audits.sql
--
-- These queries inspect stored data. The API publish-boundary check must also be run
-- against a live backend because raw `schools.attributes` is intentionally private.
-- Save non-empty list, detail, and compare responses, then make jq fail if any internal
-- key appears (the command must exit 0 and print `true`):
--   set -euo pipefail
--   api=http://localhost:8000
--   tmp=$(mktemp -d)
--   curl -fsS "$api/schools?country_code=bg&city=sofia" > "$tmp/list.json"
--   jq -e 'type == "array" and length >= 2' "$tmp/list.json" >/dev/null
--   first=$(jq -r '.[0].id' "$tmp/list.json")
--   ids=$(jq -r '.[0:2] | map(.id) | join(",")' "$tmp/list.json")
--   curl -fsS "$api/schools/$first" > "$tmp/detail.json"
--   curl -fsS "$api/compare?ids=$ids" > "$tmp/compare.json"
--   jq -s -e '[.[] | .. | objects | keys[]? | select(
--       . == "extracted" or . == "extracted_i18n" or
--       . == "data_validation" or . == "source_refs" or
--       . == "website_extracted" or . == "value_json" or
--       . == "field_path" or . == "confidence_score" or
--       . == "submitted_by" or
--       startswith("moe_")
--     )] | length == 0' "$tmp/list.json" "$tmp/detail.json" "$tmp/compare.json"

\set ON_ERROR_STOP on

\set country 'bg'
\set city 'sofia'

\echo '1. Validation status and report coverage'
WITH scoped AS (
    SELECT attributes
    FROM schools
    WHERE country_code = :'country' AND lower(city) = lower(:'city')
)
SELECT
    count(*) AS schools,
    count(*) FILTER (
        WHERE json_typeof(attributes->'data_validation') = 'object'
          AND (attributes->'data_validation')::text <> '{}'
    ) AS with_report,
    round(
        100.0 * count(*) FILTER (
            WHERE json_typeof(attributes->'data_validation') = 'object'
              AND (attributes->'data_validation')::text <> '{}'
        )
        / nullif(count(*), 0),
        1
    ) AS report_coverage_pct,
    count(*) FILTER (WHERE attributes->'data_validation'->>'status' = 'ok') AS validation_ok,
    round(
        100.0 * count(*) FILTER (
            WHERE attributes->'data_validation'->>'status' = 'ok'
        ) / nullif(count(*), 0),
        1
    ) AS validation_ok_pct
FROM scoped;

SELECT
    coalesce(attributes->'data_validation'->>'status', '<missing>') AS validation_status,
    count(*) AS schools
FROM schools
WHERE country_code = :'country' AND lower(city) = lower(:'city')
GROUP BY 1
ORDER BY 2 DESC, 1;

\echo '1a. Current-report gate for stored website-derived candidates'
WITH scoped AS (
    SELECT school.*
    FROM schools AS school
    WHERE school.country_code = :'country'
      AND lower(school.city) = lower(:'city')
), candidates AS (
    SELECT school.*
    FROM scoped AS school
    WHERE school.scrape_status IN ('extracted', 'summarized')
      AND coalesce(school.attributes->>'website_data_withheld', 'false') <> 'true'
      AND (
          coalesce(school.attributes->'extracted', '{}'::json)::text <> '{}'
          OR coalesce(school.attributes->'display_name_i18n', '{}'::json)::text <> '{}'
          OR coalesce(school.admission_info->'website_extracted', '{}'::json)::text <> '{}'
          OR coalesce(school.summary_i18n, '{}'::json)::text <> '{}'
          OR EXISTS (
              SELECT 1 FROM pricing
              WHERE pricing.school_id = school.id
                AND pricing.source = 'SCRAPED_WEBSITE'
                AND nullif(btrim(pricing.source_url), '') IS NOT NULL
                AND json_typeof(pricing.pricing_context->'confidence') = 'number'
                AND (pricing.pricing_context->>'confidence')::numeric BETWEEN 0.7 AND 1.0
          )
      )
)
SELECT
    count(*) AS stored_candidate_schools,
    count(*) FILTER (
        WHERE json_typeof(attributes->'data_validation') = 'object'
          AND attributes->'data_validation'->>'_schema_version' = '1'
    ) AS publishable_by_report_state,
    round(
        100.0 * count(*) FILTER (
            WHERE json_typeof(attributes->'data_validation') = 'object'
              AND attributes->'data_validation'->>'_schema_version' = '1'
        ) / nullif(count(*), 0),
        1
    ) AS current_report_coverage_pct,
    count(*) FILTER (
        WHERE coalesce(attributes->'data_validation'->>'_schema_version', '') <> '1'
    ) AS withheld_for_missing_or_stale_report,
    0 AS published_without_report
FROM candidates;

\echo '1b. Stored website data currently withheld by URL state'
SELECT
    scrape_status,
    count(*) AS schools_with_stored_website_data
FROM schools AS school
WHERE school.country_code = :'country'
  AND lower(school.city) = lower(:'city')
  AND (
      school.scrape_status NOT IN ('extracted', 'summarized')
      OR coalesce(school.attributes->>'website_data_withheld', 'false') = 'true'
  )
  AND (
      coalesce(school.attributes->'extracted', '{}'::json)::text <> '{}'
      OR coalesce(school.admission_info->'website_extracted', '{}'::json)::text <> '{}'
      OR EXISTS (
          SELECT 1 FROM pricing
          WHERE pricing.school_id = school.id
            AND pricing.source = 'SCRAPED_WEBSITE'
      )
  )
GROUP BY scrape_status
ORDER BY scrape_status;

\echo '2. Stored summary publication eligibility'
WITH scoped AS (
    SELECT
        attributes,
        json_typeof(summary_i18n) = 'object'
            AND summary_i18n::text <> '{}' AS has_summary
    FROM schools
    WHERE country_code = :'country' AND lower(city) = lower(:'city')
)
SELECT
    count(*) FILTER (WHERE has_summary) AS stored_summaries,
    count(*) FILTER (
        WHERE has_summary
          AND attributes->'data_validation'->>'status' = 'ok'
    ) AS publishable_summaries,
    count(*) FILTER (
        WHERE has_summary
          AND coalesce(attributes->'data_validation'->>'status', '') <> 'ok'
    ) AS summaries_that_must_be_withheld
FROM scoped;

\echo '3. Coordinate and precision-metadata coverage'
WITH scoped AS (
    SELECT location.*
    FROM school_locations AS location
    JOIN schools AS school ON school.id = location.school_id
    WHERE school.country_code = :'country' AND lower(school.city) = lower(:'city')
)
SELECT
    count(*) AS locations,
    count(*) FILTER (WHERE lat IS NOT NULL AND lng IS NOT NULL) AS geocoded,
    count(*) FILTER (
        WHERE lat IS NOT NULL AND lng IS NOT NULL
          AND geocode_meta->>'precision' IN ('exact', 'approximate')
    ) AS geocoded_with_precision,
    round(
        100.0 * count(*) FILTER (
            WHERE lat IS NOT NULL AND lng IS NOT NULL
              AND geocode_meta->>'precision' IN ('exact', 'approximate')
        ) / nullif(count(*) FILTER (WHERE lat IS NOT NULL AND lng IS NOT NULL), 0),
        1
    ) AS precision_metadata_coverage_pct,
    count(*) FILTER (WHERE geocode_meta->>'precision' = 'exact') AS exact,
    round(
        100.0 * count(*) FILTER (WHERE geocode_meta->>'precision' = 'exact')
        / nullif(count(*) FILTER (WHERE lat IS NOT NULL AND lng IS NOT NULL), 0),
        1
    ) AS exact_pct_of_geocoded
FROM scoped;

SELECT
    count(*) AS website_map_points,
    count(*) FILTER (
        WHERE geocode_meta->>'method' = 'website_map_link'
          AND geocode_meta->>'precision' = 'exact'
    ) AS website_map_points_with_expected_meta
FROM school_locations AS location
JOIN schools AS school ON school.id = location.school_id
WHERE school.country_code = :'country'
  AND lower(school.city) = lower(:'city')
  AND location.location_tags::jsonb @> '["coords_source=website_map_link"]'::jsonb;

\echo '4. Duplicate coordinate groups (review legitimate co-located schools manually)'
SELECT
    round(location.lat::numeric, 5) AS lat,
    round(location.lng::numeric, 5) AS lng,
    count(DISTINCT school.id) AS school_count,
    array_agg(DISTINCT school.id ORDER BY school.id) AS school_ids,
    array_agg(DISTINCT school.name_i18n->>'bg' ORDER BY school.name_i18n->>'bg') AS school_names
FROM school_locations AS location
JOIN schools AS school ON school.id = location.school_id
WHERE school.country_code = :'country'
  AND lower(school.city) = lower(:'city')
  AND location.lat IS NOT NULL
  AND location.lng IS NOT NULL
GROUP BY round(location.lat::numeric, 5), round(location.lng::numeric, 5)
HAVING count(DISTINCT school.id) >= 2
ORDER BY school_count DESC, lat, lng;

\echo '5. Coordinates outside the shared Sofia municipality bounds'
SELECT
    location.id AS location_id,
    school.id AS school_id,
    school.name_i18n->>'bg' AS school_name,
    location.address_i18n->>'bg' AS address,
    location.lat,
    location.lng,
    location.geocode_meta
FROM school_locations AS location
JOIN schools AS school ON school.id = location.school_id
WHERE school.country_code = :'country'
  AND lower(school.city) = lower(:'city')
  -- P1.3 deliberately treats MoE region 23 as Sofia-oblast, not Sofia-city.
  AND coalesce(school.attributes->>'moe_region_code', '') <> '23'
  AND location.lat IS NOT NULL
  AND location.lng IS NOT NULL
  AND NOT (
      location.lat BETWEEN 42.50 AND 42.86
      AND location.lng BETWEEN 23.10 AND 23.60
  )
ORDER BY school.id, location.id;

\echo '6. Display-name candidates and corroborated overrides'
SELECT
    count(*) FILTER (
        WHERE json_typeof(attributes->'display_name_i18n') = 'object'
          AND (attributes->'display_name_i18n')::text <> '{}'
    ) AS candidates,
    count(*) FILTER (
        WHERE attributes->'display_name_evidence'->>'status' = 'corroborated'
    ) AS corroborated,
    count(*) FILTER (
        WHERE json_typeof(attributes->'display_name_i18n') = 'object'
          AND (attributes->'display_name_i18n')::text <> '{}'
          AND coalesce(attributes->'display_name_evidence'->>'status', '') <> 'corroborated'
    ) AS rejected_or_uncorroborated
FROM schools
WHERE country_code = :'country' AND lower(city) = lower(:'city');

SELECT
    id AS school_id,
    name_i18n AS registry_name,
    attributes->'display_name_i18n' AS display_name,
    attributes->'display_name_evidence' AS evidence
FROM schools
WHERE country_code = :'country'
  AND lower(city) = lower(:'city')
  AND attributes->'display_name_evidence'->>'status' = 'corroborated'
ORDER BY id;

\echo '7. Spot-check coverage and actionable discrepancies'
SELECT
    count(*) AS schools,
    count(*) FILTER (
        WHERE json_typeof(attributes->'data_validation'->'spot_check') = 'object'
    ) AS spot_checked,
    count(*) FILTER (
        WHERE json_typeof(attributes->'data_validation'->'spot_check') = 'object'
          AND (attributes->'data_validation'->'spot_check'->>'has_discrepancy')::boolean
    ) AS with_discrepancy
FROM schools
WHERE country_code = :'country' AND lower(city) = lower(:'city');

\echo '8. Scraped pricing rows that fail the public gate'
SELECT
    pricing.id AS pricing_id,
    school.id AS school_id,
    school.name_i18n->>'bg' AS school_name,
    pricing.source_url,
    pricing.pricing_context->'confidence' AS confidence
FROM pricing
JOIN schools AS school ON school.id = pricing.school_id
WHERE school.country_code = :'country'
  AND lower(school.city) = lower(:'city')
  AND pricing.source = 'SCRAPED_WEBSITE'
  AND (
      nullif(btrim(pricing.source_url), '') IS NULL
      OR CASE
          WHEN json_typeof(pricing.pricing_context->'confidence') = 'number'
          THEN (pricing.pricing_context->>'confidence')::numeric NOT BETWEEN 0.7 AND 1.0
          ELSE true
      END
  )
ORDER BY school.id, pricing.id;

\echo '9. Raw internal-key inventory (these may exist in storage but must not reach the API)'
SELECT internal_key, count(*) AS schools
FROM schools
CROSS JOIN LATERAL json_object_keys(coalesce(attributes, '{}'::json)) AS internal_key
WHERE country_code = :'country'
  AND lower(city) = lower(:'city')
  AND (
      internal_key IN ('extracted', 'extracted_i18n', 'data_validation', 'source_refs')
      OR internal_key LIKE 'moe\_%' ESCAPE '\'
  )
GROUP BY internal_key
ORDER BY internal_key;
