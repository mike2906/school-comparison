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
5. Language preference is stored in `localStorage` under `language`
   (`src/i18n/index.js`). Default: Bulgarian.

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

**Map tiles** (CartoDB Positron):
```jsx
<TileLayer
  url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
  attribution='&copy; OpenStreetMap contributors &copy; CARTO'
/>
```

**Pricing display** — show each price's `source` (official / scraped_website / forum /
not_found) in the UI.
