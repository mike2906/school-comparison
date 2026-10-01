# frontend/AGENTS.md — Frontend rules

Read the root `AGENTS.md` first. This file adds what you need before changing anything under
`frontend/`. Checks: `npm run lint`, `npm run test`, `npm run build`.

---

## i18n Rules

1. **All UI strings** go through `t('key')` (react-i18next) or translation dictionaries. Never
   hardcode English or Bulgarian in components. Add every key to both
   `src/i18n/bg.json` and `src/i18n/en.json`.
2. **School names and addresses** arrive per language (`name_i18n` / `address_i18n`,
   `{"bg": ..., "en": ...}`). Display the version for the user's language.
3. **AI summaries** arrive per language in `summary_i18n`; pick by locale.
4. **Domain terms** have dedicated keys (see root `AGENTS.md` → Domain terms).
5. The URL decides the language: Bulgarian at `/`, English under `/en/`
   (`src/utils/languageUrl.js`). There is no browser detection and no saved preference.
   The router runs under the language's basename, so write links and `navigate()` calls
   without a prefix (`/schools/12`); they stay in the current language. Switching language
   is a full navigation to the other prefix (`LanguageToggle`).

```jsx
<button>Search Schools</button>          // bad
<button>{t('search_schools')}</button>   // good
```

Country-specific labels and calculations (age groups, education levels) come from the
country config via `useCountry()` and the helpers in `src/utils/countryConfig.js`.

---

## Patterns

**API calls** — use the wrappers in `src/api/`:
```javascript
import { fetchSchools } from '../api/schools';

const schools = await fetchSchools({ age_group: 'first' });
```

**Translations:**
```javascript
import { useTranslation } from 'react-i18next';

const { t, i18n } = useTranslation();
return <h1>{t('welcome')}</h1>;
```

**Distances** — use Leaflet's built-in `distanceTo` (see `src/utils/distance.js`), not a
custom haversine.

**Map tiles** (CARTO Voyager, `TILE_URL` in `src/components/Map/SchoolMap.jsx`): CARTO
basemaps need an API key, passed as `?key=` from `VITE_CARTO_API_KEY` (see
`frontend/.env.example`). Without it every tile carries an "API KEY REQUIRED" watermark.
The key ships in the browser bundle, so it is not a secret: restrict it to our domains in
the CARTO dashboard instead.

**Prerendering** — `npm run build` runs `scripts/prerender.js` after Vite: it writes a real
HTML file per valid URL and language (title, description, canonical, hreflang, Open Graph,
a small static content block), plus `sitemap.xml`, `robots.txt` and the 404 pages. The
builders are in `src/prerender/pages.js`; school data comes from the public API
(`PRERENDER_API_URL`). Because a top-level `404.html` ends the host's SPA fallback, **a new
route needs an entry in `fixedPages`** or it will return 404 in production. Deploys use
`npm run build:production`, which fails rather than ship without school pages.

**Pricing display** — show each price's `source` (official / scraped_website / forum /
not_found) in the UI.
