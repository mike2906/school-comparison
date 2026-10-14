import assert from 'node:assert/strict'
import test from 'node:test'

import { atAGlanceKeysIn, atAGlanceLines, factTopicsIn, factsReadDate, getSchoolFacts } from './schoolFacts.js'

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
