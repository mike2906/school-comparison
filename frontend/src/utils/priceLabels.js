// A price row's plan name and age group are the school website's own words, usually in
// Bulgarian. Words we have our own label for (a fee category, a grade, a kindergarten
// stage) are shown in the page language; other text is shown as written and marked with
// its language, so a Bulgarian fee name on the English page is read as Bulgarian.
import { gradeBandLabel, gradesInText, gradesNamedIn, parseGradeBand } from './gradeBand.js'

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
 * The plan name to show for a price row, or null when it only repeats the row's category
 * ("Транспорт" under Transport). A bare grade or kindergarten stage is shown as our own
 * label; anything else as the website wrote it.
 */
export function planNameLabel(price, t) {
  const raw = price?.plan_name?.trim()
  if (!raw) return null
  const name = normalize(raw)
  if (price.category && LANGUAGES.some(lng => normalize(t(`pricing.${price.category}`, { lng })) === name)) return null
  return stageLabel(raw, t) || gradesOnlyLabel(raw, t) || raw
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
