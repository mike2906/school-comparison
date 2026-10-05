# Sofia School Comparison App — Project Plan v2

> Historical design proposal. This document includes ideas and schema choices that were
> not implemented. Use `GO_LIVE_PLAN.md`, `AGENTS.md`, the current models and README for
> current behavior. In particular, JSON storage and publication gates supersede earlier
> JSONB and unrestricted summary proposals.

## Project Overview

A bilingual (Bulgarian / English) web app that helps parents in Sofia discover, filter, and compare kindergartens and schools based on location, age group, pricing, exam results, and more. Data is scraped and periodically refreshed. AI is used strategically (and cheaply) for summaries and sentiment analysis.

---

## Bulgarian Education System — Reference

Understanding this properly is essential to building the app correctly. The system is not a simple linear age → school mapping.

### School Structure & Age Groups

| Level | Bulgarian Term | Ages | Grades | Notes |
|---|---|---|---|---|
| Nursery | Детска ясла (СДЯ) | 10mo – 3yrs | — | Under Ministry of Health. Separate application. |
| Kindergarten | Детска градина (ДГ) | 3 – 6/7 | — | 4 age groups (see below). Compulsory from age 4. |
| Pre-school group in school | ПГУ (подготвителна група) | 6 – 7 | — | Some schools offer this; it's the year before Grade 1. |
| Primary | Основно (1–4 клас) | 6/7 – 10/11 | 1–4 | Certificate issued after Grade 4. |
| Lower secondary | Прогимназия (5–7 клас) | 10/11 – 13/14 | 5–7 | Certificate after Grade 7. Critical year: NVO exam. |
| Upper secondary | Средно образование | 13/14 – 17/18 | 8–12 | Gymnasium. Entry after Grade 7 NVO. |

### Kindergarten Age Groups (Critical for the age filter)

| Group | Bulgarian | Age at start of school year (Sept 15) |
|---|---|---|
| Nursery group (ясла) | Яслена група | 10 months – 3 years |
| First group | Първа група | Turns 3 that calendar year |
| Second group | Втора група | Turns 4 that calendar year |
| Third group | Трета група | Turns 5 that calendar year |
| Pre-school group | Предучилищна група | Turns 6 that calendar year |

**Key rule:** A child enters a group in the calendar year they turn that age — not on their exact birthday. So a child born in December 2022 enters "First group" in September 2025 (the year they turn 3), alongside a child born in January 2022.

### NVO (НВО) — National External Assessment

The NVO is the national standardised exam. It's the single most important data point for comparing schools at the primary/secondary level.

- **NVO after Grade 4:** Tests Bulgarian Language & Literature, Mathematics, and two other subjects (Man & Nature, Man & Society). Results are public per-school. Used internally, not for high school admission.
- **NVO after Grade 7:** Tests Bulgarian Language & Literature and Mathematics (mandatory). This is the big one — it directly determines which high school (gymnasium) a student can get into. The top gymnasiums require scores above 95/100.
- **NVO after Grade 10:** Newer addition, tests Bulgarian language literacy and math literacy.

**For the app:** The Grade 7 NVO results per school are the most valuable comparison data. Scrape these from the Ministry of Education's results platform.

### Admission Systems

**State kindergartens/schools — Points ("Бал") system:**

This is a well-documented, mechanical system. Sofia Municipality runs it through kg.sofia.bg. Children are ranked by points; those with more points get priority. The points system has two tiers:

*General criteria (everyone gets these):*
- Criterion 0: Preference order bonus — 3 pts for 1st choice, 2 for 2nd, 1 for 3rd
- Criterion 1: Address duration in Sofia Municipality — up to 5 pts (permanent address 3+ years)
- Criterion 2: Address in the same district as the school — 3 pts
- Criteria 3 & 4: Working parent or studying parent — 2 pts each per parent (only one of these two can be used per parent)
- Criterion 5: Child previously attended a registered nursery — 1 pt
- Criteria 6/7/8 (only one applies): Sibling at same school (1 pt), twins (1 pt), or children born < 2 years apart (1 pt)

*Social criteria (reserved quota — 30% of seats):*
- Orphaned children (6–7 pts), family disability (4 pts), foster/adopted children (3 pts), large families with 3+ children (2 pts), etc.

*Additional criteria:*
- Children with special educational needs — 7 pts
- Children with chronic illness — 3 pts

**Tiebreaker:** If points are equal, order of registration in the online system decides.

**State gymnasiums (after Grade 7) — Score ("Бал") system:**

The admission score is a formula:
`Score = NVO Bulgarian + NVO Math + converted grades from two qualifying subjects (chosen by the school)`

Each school publishes the minimum score needed for admission from the previous year. This is publicly available and extremely useful for the app.

**Private schools:** No standardised system. Most use entry tests or interviews. The app can note what's known but can't calculate anything mechanical here.

### Shifts & After-School Care

**Shifts (Смени):** Many state schools in Sofia operate on two shifts due to capacity. Morning shift is roughly 7:30–13:00, afternoon shift is 13:30–19:00. Younger students (Grades 1–4) almost always get the morning shift. This is a practical concern for parents — a school that puts your child on the afternoon shift is very different from one that doesn't.

**Extended care / organised groups (Organised groups — Организирани групи):** Some state schools offer after-school supervised care for primary-age children (typically Grades 1–4). This is sometimes called "organised groups" or linked to "groups of interest" (групи по интереси). It means the child stays at school after lessons end, doing homework and activities, until a parent picks them up (often until ~18:00–19:00). Not all schools offer this. It's a major practical factor for working parents and should be a filterable field.

### Pricing Reality

- **State kindergartens:** Free tuition. Parents pay only a municipality-set monthly food fee (relatively small). Some pay extra for optional activities (English, art, dance).
- **State schools (Grade 1+):** Completely free. Food is also subsidised/free for many grades.
- **Private kindergartens/schools:** Tuition varies enormously. Always distinguish: tuition (такса обучение), food (храна), transport (транспорт), and activities (допvassимки). Some bundle everything; others itemise.

---

## Tech Stack & Rationale

### Backend
| Tool | Role | Why |
|---|---|---|
| **FastAPI** | API framework | Async-first, great docs (OpenAPI auto-generated), perfect for a scraping-heavy app |
| **Pydantic v2** | Data validation & serialisation | Native to FastAPI, enforces clean data at every boundary |
| **SQLAlchemy 2.0** | ORM | Mature, flexible, works beautifully with async via `asyncpg` |
| **Alembic** | DB migrations | Standard companion to SQLAlchemy — version-control your schema |
| **PostgreSQL** | Database | PostGIS extension gives you geospatial queries for free (distance, radius search) |
| **Celery + Redis** | Background task queue | Scraping & refresh jobs should run on a schedule, not block requests |

### Frontend
| Tool | Role | Why |
|---|---|---|
| **React (Vite)** | UI framework | Fast dev server, huge ecosystem, great for map + list hybrid UIs |
| **Leaflet.js** | Map | **Free, open source, no API key needed**. Use OpenStreetMap tiles |
| **Recharts** | Exam result graphs | Lightweight, React-native charting |
| **Tailwind CSS** | Styling | Utility-first, fast to build, easy to keep consistent |
| **i18next** | Internationalisation | Industry standard for React i18n. JSON translation files, simple `t('key')` calls |

### AI / LLM Layer
| Tool | Role | Why |
|---|---|---|
| **PydanticAI** | LLM orchestration | Pairs naturally with your Pydantic models, structured output out of the box |
| **OpenRouter** | LLM router | Routes to the cheapest model that fits the task. See model strategy below |

### Maps (Free Tier Strategy)
- **OpenStreetMap + Leaflet** for the map display — completely free, no key needed
- **OpenRouteService (ORS)** for drive-time routing — free tier gives 1,000 requests/day, plenty for a hobby project. Alternative: OSRM (fully open source, self-hostable if you want zero cost long-term)

---

## i18n Strategy (Bulgarian / English)

### How to handle it

Use **i18next** with **react-i18next**. The pattern is simple:

- All user-facing strings live in JSON files: `en.json` and `bg.json`
- In components you call `t('school.name_label')` instead of writing the string directly
- Language switcher in the nav, stores preference in localStorage

### What gets translated vs. what doesn't

| Translated (UI chrome) | NOT translated (data) |
|---|---|
| Button labels, nav, headings | School names (always in Bulgarian) |
| Filter labels, form text | Addresses |
| Error messages, tooltips | AI-generated summaries — see below |
| "Verified" / "Unverified" badges | |

### AI summaries — the tricky part

School summaries are AI-generated. You have two options:

1. **Generate in both languages at scrape time.** Store `summary_bg` and `summary_en` in the DB. Costs 2x LLM calls but gives you perfect summaries in both languages. Recommended — it's cheap with a small model and you only do it once per school per refresh.
2. **Generate in Bulgarian, translate on-the-fly.** Cheaper initially but adds latency and another LLM call at render time. Not recommended.

**Go with option 1.** At ~500 schools and 2 summaries each, with a small model on OpenRouter, you're still well under $5 total for the initial run.

### Bulgarian text you'll need to hardcode / know

Some terms are domain-specific and the LLM might not handle them well in translation. Keep a glossary in your i18n files:

| Key | Bulgarian | English |
|---|---|---|
| State school | Държавно/Общregaloততటшкола | State / Municipal school |
| Private school | Частна школа | Private school |
| Kindergarten | Детска градина | Kindergarten |
| Nursery | Детска ясла | Nursery |
| Tuition | Такса обучение | Tuition |
| Food | Храна | Food / Meals |
| Transport | Транспорт | Transport |
| NVO | НВО | National Assessment (NVO) |
| Points / Score | Бал | Points |
| Shift | Смена | Shift |
| Morning shift | Сутрешна смена | Morning shift |
| Afternoon shift | Следобедна смена | Afternoon shift |
| Organised groups | Организирани групи | After-school care |
| Unverified | Неофициален | Unverified |

---

## AI Model Strategy (Cost Optimisation)

Route tasks to the cheapest model that can handle them reliably:

| Task | Suggested Model Tier | Why |
|---|---|---|
| School summary generation (BG + EN) | Small (e.g. Gemma, Llama small) | Summarisation is straightforward |
| Scraping extraction (structured data from HTML) | Small-Medium | Pattern extraction from known layouts |
| Sentiment analysis (bgmamma posts) | Small | Classification/scoring task |
| Price extraction from unstructured pages | Medium | Needs reasoning to interpret pricing tables |
| Page-change detection | Small | Simple comparison task |
| Admission score calculation | None — this is pure logic | Don't waste LLM calls on math |

**Key principle:** Use PydanticAI's structured output everywhere. Define a Pydantic model for what you want back. This keeps costs down and makes hallucinations obvious.

---

## Database Schema (v2 — Revised)

### Design decisions first

1. **Pricing is per age group, not per school.** A private kindergarten might charge 800 BGN/month for a 3-year-old and 950 BGN/month for a 6-year-old. The `pricing` table needs an `age_group` field.

2. **School info is semi-structured → use JSONB.** Things like "does it have organised groups?", "which shift?", "what activities are offered?" vary between school types and individual schools. A rigid column-per-field approach will have you adding columns constantly. Store the flexible stuff in a JSONB `attributes` field on the school or location. The structured stuff (name, type, coordinates) stays as proper columns. This gives you the best of both worlds: queryable core data + flexible extras.

3. **Summaries are stored in both languages.** Two columns: `summary_bg`, `summary_en`.

4. **Admission data is structured differently by school type.** State schools have the points system; gymnasiums have the NVO score system; private schools have ad-hoc info. A single `admission_info` JSONB field per school handles all three without contortion.

```
┌─────────────────────────────┐
│         schools             │
│ id (PK)                     │
│ name (Bulgarian, always)    │
│ school_type                 │  ENUM: state | private | international
│ education_level             │  ENUM: nursery | kindergarten | primary |
│                             │        lower_secondary | upper_secondary
│ source_url                  │  (gov registry URL)
│ website_url                 │  (official site, if found)
│ summary_bg                  │  AI-generated, Bulgarian
│ summary_en                  │  AI-generated, English
│ num_pupils                  │
│ admission_info              │  JSONB — see below
│ attributes                  │  JSONB — flexible school features
│ created_at                  │
│ updated_at                  │
│ page_hash                   │  hash of source page, for change detection
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│     school_locations        │
│ id (PK)                     │
│ school_id (FK)              │
│ age_group                   │  ENUM: nursery | first | second | third |
│                             │        preschool | grade_1_4 | grade_5_7 |
│                             │        grade_8_12
│ address                     │
│ lat / lng                   │  (or PostGIS geometry point)
│ phone                       │
│ shift                       │  ENUM: morning | afternoon | full_day | null
│ has_organised_groups        │  BOOLEAN
│ is_primary                  │  BOOLEAN (main campus)
└─────────────────────────────┘
        │
        ├─────────────────────────┐
        ▼                         ▼
┌──────────────────┐   ┌──────────────────────┐
│    pricing       │   │    exam_results      │
│ id (PK)          │   │ id (PK)              │
│ school_id (FK)   │   │ school_id (FK)       │
│ age_group        │   │ year                 │
│  (nullable —     │   │ exam_type            │  ENUM: nvo_4 | nvo_7 | nvo_10
│   null = all)    │   │ subject              │  e.g. "bulgarian", "math"
│ category         │   │ metric               │  e.g. "avg_score", "pass_rate"
│  (tuition, food, │   │ value                │
│   transport,     │   │ source_url           │
│   activities)    │   │ scraped_at           │
│ amount           │   └──────────────────────┘
│ currency         │
│ period           │  monthly | yearly | one_time
│ source           │  ENUM: official | scraped_website | forum | not_found
│ source_url       │
│ scraped_at       │
└──────────────────┘

┌──────────────────────┐     ┌──────────────────────┐
│  social_scores       │     │  scrape_log          │
│ id (PK)              │     │ id (PK)              │
│ school_id (FK)       │     │ school_id (FK)       │
│ vibe_score (0-100)   │     │ scrape_type          │  (discovery, website,
│ sentiment_breakdown  │     │                      │   prices, nvo, social)
│  (JSONB)             │     │ status               │  (success, failed, skipped)
│ sources_checked      │     │ page_hash            │
│  (JSONB: list of     │     │ raw_html             │  stored for debugging
│   URLs checked)      │     │ error_message        │
│ computed_at          │     │ scraped_at           │
└──────────────────────┘     └──────────────────────┘
```

### admission_info JSONB — examples by school type

```json
// State kindergarten
{
  "system": "points",
  "platform_url": "https://kg.sofia.bg",
  "historical_min_points": [
    { "year": 2024, "first_group": 14, "second_group": 12 }
  ]
}

// State gymnasium (after Grade 7)
{
  "system": "nvo_score",
  "score_formula": "nvo_bel + nvo_math + 2_qualifying_subjects",
  "qualifying_subjects": ["history", "geography"],
  "historical_min_scores": [
    { "year": 2024, "min_score": 312 }
  ]
}

// Private school
{
  "system": "entry_test",
  "has_interview": true,
  "notes_bg": "Приемен изпит по математика и български",
  "notes_en": "Entry test in math and Bulgarian"
}
```

### attributes JSONB — examples

```json
{
  "has_organised_groups": true,
  "shifts": ["morning"],
  "activities_offered": ["english", "art", "dance", "swimming"],
  "languages_of_instruction": ["bulgarian"],
  "special_focus": "math",         // e.g. math school, language school, art school
  "has_canteen": true,
  "uniform_required": false
}
```

---

## Build Order (Recommended)

### Phase 1 — Foundation
- [ ] Project scaffolding: FastAPI + React/Vite + PostgreSQL/PostGIS locally
- [ ] Alembic migrations for core schema
- [ ] Set up i18next with `en.json` and `bg.json` — even if 90% is placeholder, do it now. Retrofitting i18n later is painful.
- [ ] Manually seed 5–10 schools with realistic data (mix of state kindergartens, private, one gymnasium). Include multiple locations for at least one school.
- [ ] Leaflet map showing pins, filtered by child's age
- [ ] Click pin → basic school card (name, address, type, shift info)
- [ ] Language switcher in nav

### Phase 2 — Scraping Pipeline
- [ ] Scraper for Sofia Municipality kindergarten list and state school registry
- [ ] Website discovery: for each school, attempt to find their official site
- [ ] NVO results scraper (Grade 4 and Grade 7 results from MoE platform)
- [ ] Celery + Redis for scheduled jobs
- [ ] Page-hash change detection: only re-scrape if hash differs
- [ ] `scrape_log` table populated on every run — this is your debugging lifeline
- [ ] Store raw HTML in scrape_log

### Phase 3 — AI Layer
- [ ] PydanticAI + OpenRouter integration
- [ ] School summary generation (both BG and EN, in one prompt if the model handles it, otherwise two cheap calls)
- [ ] Price extraction from school websites (Pydantic model enforces structure)
- [ ] Source labelling on all pricing data
- [ ] Historical min-points/min-scores extraction for admission_info JSONB

### Phase 4 — Rich UI
- [ ] Full school detail card: summary, contact, pricing breakdown with source badges, shift/organised groups info
- [ ] Distance from user location (geolocation API → PostGIS distance query)
- [ ] NVO results graph (Recharts, last 3–5 years per subject)
- [ ] Compare panel: "Add to compare" → side-by-side cards
- [ ] Age filter properly maps to the Bulgarian age group system (calendar year logic, not just age number)
- [ ] Price display respects the tuition vs consumables distinction clearly

### Phase 5 — Stretch Goals
- [ ] **Points calculator:** Form where user enters their situation (address duration, working/studying, siblings, etc.), app calculates their points score. Compare against historical min-points for each school. This is mechanical — no LLM needed, just the rules coded up.
- [ ] **Gymnasium admission score helper:** Show the score formula for each gym, let user enter their child's grades + NVO scores, calculate the total, compare to historical minimums.
- [ ] **Drive-time at 8am:** ORS or OSRM routing. Cache results per school (recalculate weekly max).
- [ ] **Social vibe score:** Scrape bgmamma threads mentioning school names, run sentiment analysis via cheap LLM, store aggregated score.
- [ ] **Unofficial price search:** If no official price found, search forums. Display with prominent "Unverified — from forum" badge.
- [ ] **Private school difficulty score:** Composite of: price tier, waitlist mentions in forums, class size vs capacity if available. Be fully transparent about the formula.

### Phase 6 — Polish
- [ ] "Best match" score: user sets weights (price, distance, results, vibe) via sliders, app ranks schools
- [ ] Data freshness badge on every card ("Last updated: X days ago")
- [ ] Favourites / saved schools
- [ ] Price change notifications (detected during scrape refresh)

---

## Cost Estimates (Hobby / Free Tier)

| Service | Free Tier | Notes |
|---|---|---|
| OpenStreetMap + Leaflet | Completely free | Attribution required |
| OpenRouteService | 1,000 req/day | Fine for drive-time |
| OpenRouter (LLM) | Pay per token | ~500 schools × 2 summaries × ~500 tokens = ~500K tokens. At Llama/Gemma small prices, this is < $1 for initial run. Refresh runs are mostly skipped (page hash unchanged). |
| PostgreSQL | Free locally; Supabase free tier for hosting | Supabase includes PostGIS |
| Redis | Upstash free tier | |
| Hosting | Render (backend) + Vercel (frontend) free tiers | |

**Realistic total monthly cost: $0–$5.**

---

## Key Risks & Mitigations

1. **Scraping breaks** → Store raw HTML in scrape_log. Page-hash check reduces frequency. Always have a manual override to re-seed data.
2. **LLM hallucinations** → Pydantic structured output catches schema mismatches. Never trust LLM-extracted prices without cross-referencing raw HTML. Source badges make it visible to users.
3. **The age filter logic is wrong** → The Bulgarian system uses calendar year, not exact age. Test this carefully with edge cases (child born Dec 31 vs Jan 1).
4. **i18n is a mess** → Do it from day one. One missing translation key in production is annoying; hundreds is a rewrite.
5. **The points calculator is inaccurate** → The rules come from a Sofia Municipality ordinance. Code them carefully, cite the source, and add a disclaimer: "Based on current Sofia Municipality rules. Always verify with the official system at kg.sofia.bg."
6. **Scope creep** → Phases 1–4 first. Everything else is bonus.

---

## When to Move to the Terminal

Here's the honest answer to your question: **you're almost there now.** Here's what we should nail down in this chat before you go:

1. ✅ Tech stack confirmed
2. ✅ Schema designed
3. ✅ Bulgarian education context understood
4. ✅ Build phases laid out
5. 🔜 **Next from me:** I'll generate the `CLAUDE.md` file (below) and a scaffold script that sets up your entire project structure in one go — directory layout, initial `pyproject.toml`, `package.json`, first Alembic migration, i18next config, the works.

Once you have those two files, you go to the terminal and start with Claude Code. The CLAUDE.md tells Claude Code everything it needs to know about the project so you don't have to re-explain context every session.

---
