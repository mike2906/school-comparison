import assert from 'node:assert/strict'
import test from 'node:test'

import { atAGlanceKeysIn, atAGlanceLines, factTopicsIn, factsReadDate, getSchoolFacts, openDayDate, upcomingOpenDays } from './schoolFacts.js'

const school = (facts, readAt = '2026-10-13T20:00:00+00:00') => ({ school_facts: { read_at: readAt, facts } })
const fact = (topic, en, bg = 'Факт', url = 'https://s.example/a') => ({ topic, text_i18n: { en, bg }, source_url: url })

test('facts are ordered by topic, in the page language, without unknown topics', () => {
  const facts = getSchoolFacts(school([fact('meals', 'Lunch'), fact('gossip', 'x'), fact('curriculum', 'Cambridge')]), 'en')
  assert.deepEqual(facts.map(f => f.text), ['Cambridge', 'Lunch'])
  assert.equal(facts[0].sourceUrl, 'https://s.example/a')
})

test('a fact falls back to Bulgarian, and a school with none has none', () => {
  assert.equal(getSchoolFacts(school([{ topic: 'meals', text_i18n: { bg: 'Обяд' }, source_url: 'u' }]), 'en')[0].text, 'Обяд')
  assert.deepEqual(getSchoolFacts({}, 'en'), [])
  assert.deepEqual(getSchoolFacts({ school_facts: null }, 'en'), [])
})

test('compare rows cover the topics any school has, in display order', () => {
  assert.deepEqual(factTopicsIn([school([fact('meals', 'a')]), school([fact('languages', 'b')]), {}], 'en'), ['languages', 'meals'])
})

test('the read date is the date part of an ISO timestamp', () => {
  assert.equal(factsReadDate(school([], '2026-10-13T20:00:00+00:00')), '2026-10-13')
  assert.equal(factsReadDate(school([], 'soon')), '')
})

test('facts keep their rank; keyOnly keeps the key ones, and a fact with no rank is key', () => {
  const ranked = school([{ ...fact('meals', 'Lunch'), rank: 'more' }, fact('curriculum', 'Cambridge')])
  assert.deepEqual(getSchoolFacts(ranked, 'en').map(f => [f.text, f.rank]), [['Cambridge', 'key'], ['Lunch', 'more']])
  assert.deepEqual(getSchoolFacts(ranked, 'en', { keyOnly: true }).map(f => f.text), ['Cambridge'])
  assert.deepEqual(factTopicsIn([ranked], 'en'), ['curriculum'])
})

const t = (key, values = {}) => `${key}${Object.keys(values).length ? JSON.stringify(values) : ''}`

test('grade 0 is the preschool group at a school and the kindergarten at a kindergarten', () => {
  const glance = { admission: [{ grade: 0, types: ['trial_day'], subjects: [] }] }
  const line = school => atAGlanceLines({ ...school, school_facts: { facts: [], at_a_glance: glance } }, 'en', t)[0].value
  assert.ok(line({ education_level: 'school' }).startsWith('schoolFacts.glance.preschool:'))
  assert.ok(line({ education_level: 'kindergarten' }).startsWith('schoolFacts.glance.kindergarten:'))
})

test('at a glance lines say what the labels add up to, in display order', () => {
  const glance = {
    admission: [{ grade: 5, types: ['entrance_exam'], subjects: [{ bg: 'Математика', en: 'Maths' }] }],
    places: [{ grade: 1, status: 'full' }],
    highest_grade: 4,
    curricula: ['cambridge'],
    bus_areas: [{ bg: 'Бояна', en: 'Boyana' }],
    special_needs_support: null,
  }
  const lines = atAGlanceLines({ school_facts: { facts: [], at_a_glance: glance } }, 'bg', t)
  assert.deepEqual(lines.map(line => line.key), ['admission', 'places', 'highestGrade', 'curricula', 'bus'])
  assert.equal(
    lines[0].value,
    'schoolFacts.glance.grade{"grade":5}: schoolFacts.glance.admissionTypes.entrance_exam (Математика)',
  )
  assert.equal(lines[4].value, 'Бояна')
  assert.deepEqual(atAGlanceLines({}, 'en', t), [])
  assert.deepEqual(atAGlanceKeysIn([{ school_facts: { at_a_glance: glance } }, {}], 'en', t).slice(0, 2), ['admission', 'places'])
})

test('an admission row for a range of grades names the range; one with no grade has no prefix', () => {
  const glance = {
    admission: [
      { grade: 2, grade_to: 4, types: ['entrance_exam'], subjects: [] },
      { grade: null, grade_to: null, types: ['interview'], subjects: [] },
    ],
  }
  const [line] = atAGlanceLines({ school_facts: { facts: [], at_a_glance: glance } }, 'en', t)
  assert.equal(
    line.value,
    'schoolFacts.glance.grades{"from":2,"to":4}: schoolFacts.glance.admissionTypes.entrance_exam; schoolFacts.glance.admissionTypes.interview',
  )
})

test('open days list their dates with the sign-up link, or the source page when there is none', () => {
  const school = (days) => ({ school_facts: { facts: [], at_a_glance: { open_days: days } } })
  const [withLink] = atAGlanceLines(school([
    { date: '2026-11-12', register_url: null, source_url: 'https://s.bg/news' },
    { date: '2026-12-03', register_url: 'https://forms.example/x', source_url: 'https://s.bg/news' },
  ]), 'en', t, '2026-10-15')
  assert.equal(withLink.key, 'openDays')
  assert.equal(withLink.href, 'https://forms.example/x')
  assert.equal(withLink.linkText, 'schoolFacts.glance.register')
  const [noLink] = atAGlanceLines(school([{ date: '2026-11-12', source_url: 'https://s.bg/news' }]), 'en', t, '2026-10-15')
  assert.equal(noLink.href, 'https://s.bg/news')
  assert.equal(noLink.linkText, 'schoolFacts.source')
})

test('upcoming open days drop passed dates and come earliest first', () => {
  const school = { school_facts: { at_a_glance: { open_days: [
    { date: '2026-11-10' }, { date: '2026-10-14' }, { date: '2026-10-15' }, { date: null }, { date: '26.10.2026' },
  ] } } }
  assert.deepEqual(upcomingOpenDays(school, '2026-10-15').map(day => day.date), ['2026-10-15', '2026-11-10'])
  assert.deepEqual(upcomingOpenDays({}, '2026-10-15'), [])
  // Only passed days: no open-days line at a glance.
  assert.deepEqual(atAGlanceLines({ school_facts: { at_a_glance: { open_days: [{ date: '2026-10-01', source_url: 'https://s.bg' }] } } }, 'en', t, '2026-10-15'), [])
})

test('open day dates are written day first in English', () => {
  assert.equal(openDayDate('2026-10-26', 'en'), '26 October 2026')
})
