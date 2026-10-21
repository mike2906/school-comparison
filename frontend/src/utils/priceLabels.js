// A price row's plan name and age group are the school website's own words, usually in
// Bulgarian. Words we have our own label for (a fee category, a grade, a kindergarten
// stage) are shown in the page language; other text in the page language's translation when
// the API has one, else as written and marked with its language.
import { gradeBandLabel, gradesInText, gradesNamedIn, parseGradeBand } from './gradeBand.js'
import { priceCategory } from './priceRows.js'

const LANGUAGES = ['bg', 'en']

function normalize(text) {
  return String(text || '').toLocaleLowerCase('bg').replace(/\s+/g, ' ').trim()
}

// Kindergarten stages a website writes as the whole age group or plan name.
const STAGE_KEYS = {
  'ясла': 'ageGroups.nursery',
  'детска ясла': 'ageGroups.nursery',
  'nursery': 'ageGroups.nursery',
  'градина': 'educationLevels.kindergarten',
  'детска градина': 'educationLevels.kindergarten',
  'kindergarten': 'educationLevels.kindergarten',
}

/** Our label for a kindergarten stage ("Ясла" → "Nursery"), or null. */
export function stageLabel(text, t) {
  const key = STAGE_KEYS[normalize(text)]
  return key ? t(key) : null
}

// A grade word or a preschool name; a bare "I" or "IV" may number payment options.
const GRADE_NAMED_RE = /клас|кл\.|grade|pre-?school|^(?:пгу|пг|пк|pk)$/iu

// "10. клас", "VIII - XII клас", "Preschool" or a school stage ("Гимназия"), and nothing else.
// Not "Подготвителен клас": at a language gymnasium that is the intensive 8th-grade year.
function gradesOnlyLabel(text, t) {
  const name = normalize(text)
  const band = (GRADE_NAMED_RE.test(name) && name !== 'подготвителен клас' ? parseGradeBand(text) : null)
    || (!/[/|]/.test(text) && !gradesInText(text) ? gradesNamedIn(text) : null)
  return band ? gradeBandLabel(band, t) : null
}

/**
 * A row's website words (`plan_name`, `age_group`) for the page: in the page language
 * (`pricing_context.<field>_en` / `<field>_bg`, the fees agent's English or the API's stored
 * translation) when there is one, else as written.
 */
export function websiteWords(price, field, language) {
  const raw = price?.[field]?.trim()
  if (!raw) return null
  const lang = String(language || '').startsWith('en') ? 'en' : 'bg'
  return price?.pricing_context?.[`${field}_${lang}`]?.trim() || raw
}

/**
 * The plan name to show for a price row, or null when it only repeats the row's category
 * ("Транспорт" under Transport). A bare grade or kindergarten stage is shown as our own
 * label; anything else as `websiteWords`.
 */
export function planNameLabel(price, t, language) {
  const raw = price?.plan_name?.trim()
  if (!raw) return null
  const shown = websiteWords(price, 'plan_name', language)
  const names = [normalize(raw), normalize(shown)]
  if (price.category && LANGUAGES.some(lng => names.includes(normalize(t(`pricing.${priceCategory(price)}`, { lng }))))) return null
  return stageLabel(raw, t) || gradesOnlyLabel(raw, t) || shown
}

/**
 * The `lang` to mark website text with when it is not in the page language: "bg" for
 * Cyrillic text on the English page, "en" for Latin-only text on the Bulgarian page.
 * Undefined when the text matches the page, so no attribute is written.
 */
export function textLang(text, pageLanguage) {
  const value = String(text || '')
  if (/\p{Script=Cyrillic}/u.test(value)) return pageLanguage === 'bg' ? undefined : 'bg'
  if (/\p{Script=Latin}/u.test(value)) return pageLanguage === 'en' ? undefined : 'en'
  return undefined
}

/**
 * `planNameLabel`, or null when it reads the same as the age-group label shown beside it
 * ("ПК" under "ПК" would read "Preschool • Preschool").
 */
export function planNameBeside(price, t, shownAgeGroup, language) {
  const name = planNameLabel(price, t, language)
  return name && name === shownAgeGroup ? null : name
}
