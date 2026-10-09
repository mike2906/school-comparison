# backend/AGENTS.md — Backend rules

Read the root `AGENTS.md` first. This file adds what you need before changing anything under
`backend/`. Run every command from `backend/` with `uv run`.

---

## Database Schema (Core Tables)

`app/models/` is the source of truth; this is the orientation map. Flexible and localized
columns are SQLAlchemy `JSON` (Postgres `json`, not `jsonb`).

### schools
- `country_code` (FK → countries.code), `city` (lowercase ASCII, e.g. `sofia`)
- `name_i18n` (JSON, e.g. `{"bg": "...", "en": "..."}`)
- `school_type`, `education_level` (plain strings; allowed values come from the country's
  `education_config`)
- `summary_i18n` (JSON, AI-generated per language)
- `admission_info` (JSON) — admission system details
- `attributes` (JSON) — flexible school-specific data (internal; see publish boundary)
  - Language focus is an open list of `{language, level}` entries (no hardcoded languages),
    normalized in `app/utils/school_attributes.py`.
- `website_url`, `scrape_status`, `institutional_id` (MoE НЕИСПУО code)

### school_locations (one school can have multiple locations)
- `school_id` (FK → schools.id)
- `address_i18n` (JSON), `lat`, `lng` (plain floats, no PostGIS), `district`, `is_primary`

### location_age_group_shifts (per location × age group)
- `location_id` (FK → school_locations.id) + `age_group` (composite PK)
- `shift` (e.g. morning | afternoon | full_day), `has_organised_groups` (after-school care)

### pricing (for private schools)
- `school_id` (FK), `age_group` (nullable) — prices vary by age
- `category` enum: tuition | food | transport | activities | registration | materials |
  extended_day | uniforms | extracurricular | camp
- `amount` or `amount_min`/`amount_max`, `currency`, `academic_year`, `plan_name`
- `period` enum, **nullable** (null when the school did not state one — never infer it):
  monthly | yearly | one_time | quarter | term | semester
- `source` enum: official | scraped_website | forum | not_found — MUST display in UI
- `source_url`, `pricing_context` (JSON, internal)

### exam_results (NVO scores for state schools)
- `school_id` (FK), `year`, `exam_type` (nvo_4 | nvo_7 | nvo_10)
- `subject`, `metric`, `value`
- Current canonical imported metric: `average_score` (official school-level average scores)
- `pupil_count` — pupils who sat the exam ("явили се"); internal, never a response field.
  A result with fewer than `NVO_MIN_PUPILS` (`app/utils/display_gating.py`) is withheld from
  every response, the benchmark averages and the summary facts.

Pipeline/provenance tables: `field_sources`, `source_pages`, `spot_check_results`,
`scrape_log`, `pipeline_runs`, `provider_request_ledger`, `countries`.

---

## Publish Boundary — API responses are gated (P1.7/P1.15–P1.16)

`schools.attributes`, `admission_info`, `pricing_context`, and raw `FieldSource` values are
internal storage; nothing reaches the wire unless it is declared in the corresponding response
allowlist. Website-derived fields must additionally pass `app/utils/website_data.py`
(publishable scrape status and no withholding marker) and the Stage 6 field-level validation
gate. Useful scraped admission fields are projected through localized attributes; never expose
`admission_info.website_extracted` or `FieldSource.value_json` directly.

`app/utils/display_gating.py` drops display fields, pricing rows, and summaries flagged by
error-level issues / actionable spot-check discrepancies, plus pricing rows with no
`source_url` or confidence below `PRICING_CONFIDENCE_FLOOR`. When adding a response field, go
through those projections/gates — never serialize raw JSON or ORM rows directly, or you
silently bypass the boundary.

Stage 6 also checks each price row against its linked page text
(`app/scrapers/price_evidence.py`, UF45): a row whose amount is not in its label's run of
prices, whose label names the other level, whose period is per day/week, or that is a
deposit or one installment filed as tuition, or an offer whose end date has passed, gets
an error on `pricing[{id}]`; a null period the page states, beside the amount or in the
heading of its table or list, is filled in. On a site a sibling school shares, so does a
row linked to a page whose address names only a stage the school does not teach; on a
site a kindergarten shares with a school, a fee whose own words are the other one's. A kindergarten showing a same-site school's name
gets an error on `attributes.display_name_i18n` and falls back to its registry name.
A re-extraction that would drop a fee the page still shows, drop a stated period, or
change a period to one the page does not state beside the amount, is held
(`attributes.pricing_hold`) and the published rows stay. Published tuition is also
bounded both ways (`implausible_tuition_row_ids`): too cheap or too dear for a year, or
against the school's median tuition, is withheld; a tuition row with no period is held to
the floors as a yearly fee.

URL validation failures set a persistent website-data withholding marker; only extraction plus
deterministic validation may clear it. Gate coverage is coupled to the validator's spot-check
scope (`SPOT_CHECK_CORE_FIELD_PREFIXES`) by a guard test.

Routes live in `app/routers/` (`schools.py`, `compare.py`, `countries.py`); every response
passes through this boundary.

---

## Patterns

**Settings** — factory pattern; import `get_settings()`, not a `settings` instance:
```python
from app.config import get_settings

settings = get_settings()  # returns singleton
db_url = settings.DATABASE_URL
```

**Database session:**
```python
from app.database import get_db

async def my_route(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(School))
```

**Migrations** — start from autogenerate, then review and adjust the generated file before
applying it:
```bash
uv run alembic revision --autogenerate -m "description"
# review/adjust the generated migration
uv run alembic upgrade head
```
Autogenerate misses some changes (data moves, Postgres enum values, renames). Add
hand-written operations (`op.execute`, etc.) for those when necessary, and verify the
migration (upgrade and downgrade on a local DB) before applying it anywhere shared.
CD runs `alembic upgrade head` before it starts the new image, and its rollback restores
the previous image without downgrading. A migration must therefore leave the schema usable
by the previous image: add in one release, remove in a later one (expand, then contract).
One that cannot is a big change: it goes to Mike and is deployed by hand
(`deploy/README.md`).

**Geocoding** — GeoJSON keys schools by **province/municipality**: Sofia is `СТОЛИЧНА`, not
`СОФИЯ` (DB stores `city="sofia"`; the provider normalizes). Read `skills/geocoding/SKILL.md`
before touching `app/services/geocoding`.

**Environment** — copy `backend/.env.example` (every variable, with defaults) to
`backend/.env`. Required:
`DATABASE_URL`, `REDIS_URL`; `OPENROUTER_API_KEY` for LLM stages.

---

## Testing

Write tests before implementing these. They encode Bulgarian admission and pricing rules that
are easy to get subtly wrong, and a silent mistake misleads parents:
- Admission points calculator (every criterion, edge cases)
- NVO score formula
- Age → group mapping (Target Admission Year logic)
- Price extraction validation

Simple CRUD routes and UI components don't need tests yet.

```bash
uv run pytest -q 2>&1 | tail -3        # full suite
uv run pytest tests/test_api.py -v     # one file
```
