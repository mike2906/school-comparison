import test from 'node:test'
import assert from 'node:assert/strict'

import { dziGradeKey, dziSubjectLabel, getDziTable, parseDziSubject } from './dzi.js'
import { getAvailableExamTypes } from './nvo.js'

const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

const dzi = (year, subject, value, extra = {}) => ({
  exam_type: 'dzi', metric: 'average_grade', year, subject, value, ...extra,
})

test('parseDziSubject reads the level and curriculum and rejects unknown subjects', () => {
  assert.deepEqual(parseDziSubject('math_pp'), { base: 'math', cefr: null, curriculum: 'pp' })
  assert.deepEqual(parseDziSubject('english_b2_pp'), { base: 'english', cefr: 'B2', curriculum: 'pp' })
  assert.deepEqual(parseDziSubject('english_b11_oop'), { base: 'english', cefr: 'B1.1', curriculum: 'oop' })
  assert.deepEqual(parseDziSubject('bulgarian'), { base: 'bulgarian', cefr: null, curriculum: null })
  assert.equal(parseDziSubject('music_pp'), null)
})

test('dziSubjectLabel names the subject, level and curriculum', () => {
  assert.equal(dziSubjectLabel('bulgarian', t), 'dzi.subjects.bulgarian')
  assert.equal(
    dziSubjectLabel('english_b2_pp', t),
    'dzi.subjectWithCurriculum:{"subject":"dzi.subjects.english B2","curriculum":"dzi.curriculum.pp"}'
  )
})

test('dziGradeKey follows the report-card bands', () => {
  assert.equal(dziGradeKey(2.99), 'poor')
  assert.equal(dziGradeKey(3.0), 'fair')
  assert.equal(dziGradeKey(3.49), 'fair')
  assert.equal(dziGradeKey(3.5), 'good')
  assert.equal(dziGradeKey(4.49), 'good')
  assert.equal(dziGradeKey(4.5), 'veryGood')
  assert.equal(dziGradeKey(5.5), 'excellent')
  assert.equal(dziGradeKey(undefined), null)
})

test('getDziTable keeps the newest years, orders subjects and adds the latest benchmark', () => {
  const results = [
    dzi(2026, 'math_pp', 5.1, { source_url: 'https://data.egov.bg/data/resourceView/x' }),
    dzi(2026, 'bulgarian_oop', 4.8),
    dzi(2025, 'bulgarian_oop', 4.7),
    dzi(2024, 'bulgarian_oop', 4.6),
    dzi(2023, 'bulgarian_oop', 4.5),
    dzi(2025, 'english_b2_pp', 5.4),
    dzi(2026, 'music_pp', 5.9),
    { exam_type: 'nvo_10', metric: 'average_score', year: 2026, subject: 'bulgarian', value: 60 },
  ]
  const averages = { by_year: { dzi: { 2026: { bulgarian_oop: 4.31 } } } }

  const table = getDziTable(results, averages)

  assert.deepEqual(table.years, [2026, 2025, 2024])
  assert.equal(table.latestYear, 2026)
  assert.deepEqual(table.subjects.map(row => row.subject), ['bulgarian_oop', 'math_pp', 'english_b2_pp'])
  assert.deepEqual(table.subjects[0].values, { 2026: 4.8, 2025: 4.7, 2024: 4.6 })
  assert.equal(table.subjects[0].benchmark, 4.31)
  assert.equal(table.subjects[1].benchmark, null)
  assert.equal(table.sourceUrl, 'https://data.egov.bg/data/resourceView/x')
})

test('getDziTable is null without ДЗИ results', () => {
  assert.equal(getDziTable([{ exam_type: 'nvo_7', metric: 'average_score', year: 2025, subject: 'math', value: 60 }]), null)
})

test('ДЗИ rows never become an NVO exam tab', () => {
  const results = [
    dzi(2026, 'math_pp', 5.1),
    { exam_type: 'nvo_10', metric: 'average_score', year: 2026, subject: 'math', value: 60 },
  ]
  assert.deepEqual(getAvailableExamTypes(results), ['nvo_10'])
})
