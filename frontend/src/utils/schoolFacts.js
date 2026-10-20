import { intlLocale } from './format.js'
import { languageLabel } from './languages.js'

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

// [{ topic, text, sourceUrl, rank }] in topic order, text in `language` (Bulgarian if
// missing). `rank` is "key" (shown first) or "more" (behind "show more"); `keyOnly` keeps
// the key facts. A fact from before ranks counts as key.
export function getSchoolFacts(school, language, { keyOnly = false } = {}) {
  const facts = school?.school_facts?.facts
  if (!Array.isArray(facts)) return []
  return facts
    .filter(fact => FACT_TOPICS.includes(fact?.topic))
    .map(fact => ({
      topic: fact.topic,
      text: fact.text_i18n?.[language] || fact.text_i18n?.bg || '',
      sourceUrl: fact.source_url,
      rank: fact.rank === 'more' ? 'more' : 'key',
    }))
    .filter(fact => fact.text && (!keyOnly || fact.rank === 'key'))
    .sort((a, b) => FACT_TOPICS.indexOf(a.topic) - FACT_TOPICS.indexOf(b.topic))
}

// Topics any of `schools` has a key fact for, in display order (the compare page's rows).
export function factTopicsIn(schools, language) {
  const present = new Set(
    schools.flatMap(school => getSchoolFacts(school, language, { keyOnly: true }).map(fact => fact.topic)),
  )
  return FACT_TOPICS.filter(topic => present.has(topic))
}

// The date part of `read_at` (ISO), or ''.
export function factsReadDate(school) {
  const value = school?.school_facts?.read_at
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : ''
}

const name = (item, language) => item?.[language] || item?.bg || ''

// Today as YYYY-MM-DD in the parent's own time zone.
function localToday() {
  const now = new Date()
  const pad = value => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

// The school's open days from today on, earliest first (API `at_a_glance.open_days`).
// The API drops passed days when the data is published, but a published copy can be
// days old, so the page checks again.
export function upcomingOpenDays(school, today = localToday()) {
  const days = school?.school_facts?.at_a_glance?.open_days
  if (!Array.isArray(days)) return []
  return days
    .filter(day => /^\d{4}-\d{2}-\d{2}/.test(day?.date || '') && day.date.slice(0, 10) >= today)
    .sort((a, b) => a.date.localeCompare(b.date))
}

// An open day's date written out in `language` ("26 October 2026").
export function openDayDate(value, language) {
  return value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString(intlLocale(language), { day: 'numeric', month: 'long', year: 'numeric' }) : ''
}

// Where open days link: a sign-up form if any upcoming day has one (schools tend to use
// one form for all), else the page announcing the first. `register` says which.
export function openDaysLink(days) {
  const signUp = days.find(day => day.register_url)
  if (signUp) return { href: signUp.register_url, register: true }
  return days[0]?.source_url ? { href: days[0].source_url, register: false } : null
}

// The school's fact labels summed up (API `school_facts.at_a_glance`), as
// [{ key, value }] lines in display order, `value` in `language`; lines with nothing to
// say are left out. `t` is i18next's.
export function atAGlanceLines(school, language, t, today = undefined) {
  const glance = school?.school_facts?.at_a_glance
  if (!glance) return []
  // Grade 0 is a school's preparatory group (or a kindergarten's oldest group), not a kindergarten.
  const grade = value => (value === 0 ? t('schoolFacts.glance.preschool') : t('schoolFacts.glance.grade', { grade: value }))
  const grades = row => {
    if (row.grade === null || row.grade === undefined) return ''
    if (!row.grade_to) return grade(row.grade)
    return row.grade === 0
      ? `${t('schoolFacts.glance.preschool')} – ${grade(row.grade_to)}`
      : t('schoolFacts.glance.grades', { from: row.grade, to: row.grade_to })
  }
  const forGrades = (row, what) => (grades(row) ? `${grades(row)}: ${what}` : what)
  const date = value => openDayDate(value, language)
  const lines = []
  const add = (key, value, link) => value && lines.push(link ? { key, value, ...link } : { key, value })

  add('admission', (glance.admission || []).map(row => {
    const types = (row.types || []).map(type => t(`schoolFacts.glance.admissionTypes.${type}`)).join(', ')
    const subjects = (row.subjects || []).map(subject => name(subject, language)).filter(Boolean).join(', ')
    const what = [types, subjects && `(${subjects})`].filter(Boolean).join(' ')
    return what && forGrades(row, what)
  }).filter(Boolean).join('; '))
  add('places', (glance.places || []).map(row => {
    const status = t(`schoolFacts.glance.placesStatus.${row.status}`)
    return forGrades(row, status)
  }).join('; '))
  add('applications', glance.applications && (
    glance.applications.open && glance.applications.close
      ? t('schoolFacts.glance.window', { open: date(glance.applications.open), close: date(glance.applications.close) })
      : glance.applications.open
        ? t('schoolFacts.glance.opens', { date: date(glance.applications.open) })
        : t('schoolFacts.glance.closes', { date: date(glance.applications.close) })
  ))
  add('highestGrade', glance.highest_grade && grade(glance.highest_grade))
  add('curricula', (glance.curricula || []).map(item => t(`schoolFacts.glance.curriculaNames.${item}`)).join(', '))
  add('diploma', glance.diploma_recognised === true
    ? t('schoolFacts.glance.recognised')
    : glance.diploma_recognised === false ? t('schoolFacts.glance.notRecognised') : '')
  add('instruction', (glance.instruction_languages || []).map(item => languageLabel(item, t)).join(', '))
  add('languages', (glance.languages || []).map(item => {
    const extra = [
      item.level && t(`advancedFilters.levels.${item.level}`),
      item.hours_per_week && t('schoolFacts.glance.hours', { count: item.hours_per_week }),
    ].filter(Boolean).join(', ')
    return extra ? `${languageLabel(item.language, t)} (${extra})` : languageLabel(item.language, t)
  }).join(', '))
  const openDays = upcomingOpenDays(school, today)
  const link = openDaysLink(openDays)
  add('openDays', openDays.map(day => date(day.date)).join(', '), link && {
    href: link.href,
    linkText: t(link.register ? 'schoolFacts.glance.register' : 'schoolFacts.source'),
  })
  add('bus', (glance.bus_areas || []).map(area => name(area, language)).filter(Boolean).join(', '))
  add('support', glance.special_needs_support === true && t('schoolFacts.glance.yes'))
  return lines
}

// The at-a-glance keys any of `schools` has a line for, in display order (compare rows).
export function atAGlanceKeysIn(schools, language, t) {
  const present = new Set(schools.flatMap(school => atAGlanceLines(school, language, t).map(line => line.key)))
  return GLANCE_KEYS.filter(key => present.has(key))
}

const GLANCE_KEYS = [
  'admission', 'places', 'applications', 'highestGrade', 'curricula', 'diploma',
  'openDays', 'instruction', 'languages', 'bus', 'support',
]
