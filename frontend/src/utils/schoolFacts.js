// Facts the school-facts stage read from a school's own pages (API `school_facts`).
// Topic order matches backend app/utils/school_facts.py TOPICS.
export const FACT_TOPICS = [
  'curriculum',
  'languages',
  'school_day',
  'class_size',
  'meals',
  'transport',
  'activities',
  'facilities',
  'support',
  'admissions',
]

// [{ topic, text, sourceUrl }] in topic order, text in `language` (Bulgarian if missing).
export function getSchoolFacts(school, language) {
  const facts = school?.school_facts?.facts
  if (!Array.isArray(facts)) return []
  return facts
    .filter(fact => FACT_TOPICS.includes(fact?.topic))
    .map(fact => ({
      topic: fact.topic,
      text: fact.text_i18n?.[language] || fact.text_i18n?.bg || '',
      sourceUrl: fact.source_url,
    }))
    .filter(fact => fact.text)
    .sort((a, b) => FACT_TOPICS.indexOf(a.topic) - FACT_TOPICS.indexOf(b.topic))
}

// Topics any of `schools` has a fact for, in display order.
export function factTopicsIn(schools, language) {
  const present = new Set(schools.flatMap(school => getSchoolFacts(school, language).map(fact => fact.topic)))
  return FACT_TOPICS.filter(topic => present.has(topic))
}

// The date part of `read_at` (ISO), or ''.
export function factsReadDate(school) {
  const value = school?.school_facts?.read_at
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : ''
}
