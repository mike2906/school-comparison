import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { catchmentUrl, indexAddresses, indexCatchments } from './catchments.js'
import { evaluateKindergarten, GRADE1_INITIAL, KINDERGARTEN_INITIAL } from './inputs.js'
import { ruleSetFor, stageById } from './calculator.js'
import { compareRows, kindergartenDistrict, KINDERGARTEN_ASSUMED, savedVerdictFor } from './schoolVerdict.js'

function ruleFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? ruleFiles(path) : name.endsWith('.json') ? [path] : []
  })
}
const ruleSets = ruleFiles(fileURLToPath(new URL('./rules', import.meta.url))).map(file => JSON.parse(readFileSync(file, 'utf8')))
const snapshot = file => JSON.parse(readFileSync(fileURLToPath(new URL(`../../public${catchmentUrl('2026/2027', file)}`, import.meta.url)), 'utf8'))
const districts = snapshot('districts.json')
const catchments = indexCatchments(snapshot('catchments.json'))
const streets = indexAddresses(snapshot('addresses.json'))
const today = '2026-10-15'
const data = { ruleSets, districts, catchments, today }

const home = districts.features[0].properties
const elsewhere = districts.features[1].properties
const kgAnswers = {
  ...KINDERGARTEN_INITIAL,
  birthYear: 2023,
  inSofia: true,
  addressSince: '2019-01-01',
  parentChildSameAddress: true,
  homeDistrict: home.code,
  parentActivities: ['working', 'working'],
  nurseryAttended: false,
}
const kindergarten = (district, cutoffSummary = []) => ({
  id: 1,
  school_type: 'state',
  education_level: 'kindergarten',
  country_code: 'bg',
  admission_info: { admin_district: district.name, cutoff_summary: cutoffSummary },
})
const kgRow = points => ({ stage: 'kindergarten', age_group: 'first', birth_year: 2023, rounds: 1, first: { last_admitted_points: points } })

test('a saved kindergarten result is compared with each kindergarten, counting its district', () => {
  const saved = { kindergarten: { startYear: 2026, answers: kgAnswers } }
  const ruleSet = ruleSetFor(ruleSets, stageById('kindergarten'), 2026, today).ruleSet
  const total = place => evaluateKindergarten(ruleSet, kgAnswers, 2026, { ...KINDERGARTEN_ASSUMED, ...place }, today).total
  const near = total({ district: home.code })
  const far = total({ district: elsewhere.code })
  assert.equal(near.min, near.max, 'every answer given: one number')
  assert.equal(near.min - far.min, ruleSet.params.same_district.points)

  const above = savedVerdictFor(kindergarten(home, [kgRow(near.min - 1)]), saved, null, data)
  assert.equal(above.stage, 'kindergarten')
  assert.equal(above.verdict, 'above')
  assert.deepEqual(above.yours.points, near)
  // The same cut-off in another district: 3 points fewer.
  assert.equal(savedVerdictFor(kindergarten(elsewhere, [kgRow(near.min - 1)]), saved, null, data).verdict, 'below')
  assert.equal(savedVerdictFor(kindergarten(home, []), saved, null, data).verdict, 'noCutoff')
  // Another birth year's row ranks other children.
  assert.equal(savedVerdictFor(kindergarten(home, [{ ...kgRow(1), birth_year: 2022 }]), saved, null, data).verdict, 'noCutoff')
})

test('saved answers show only where they apply', () => {
  const saved = { kindergarten: { startYear: 2026, answers: kgAnswers } }
  assert.equal(savedVerdictFor(kindergarten(home), saved, 'grade_1_4', data), null, 'a first-grade search')
  assert.ok(savedVerdictFor(kindergarten(home), saved, 'first', data))
  assert.equal(savedVerdictFor({ ...kindergarten(home), school_type: 'private' }, saved, null, data), null)
  assert.equal(savedVerdictFor(kindergarten(home), {}, null, data), null)
})

test('a kindergarten without a named district is placed by its pin', () => {
  const feature = districts.features[2]
  const ring = feature.geometry.type === 'Polygon' ? feature.geometry.coordinates[0] : feature.geometry.coordinates[0][0]
  // A vertex nudged towards the ring's average is inside for these convex-ish districts;
  // the test only needs a point the polygon test agrees is inside.
  const [lng, lat] = ring.reduce(([x, y], [a, b]) => [x + a / ring.length, y + b / ring.length], [0, 0])
  const school = { locations: [{ is_primary: true, lat, lng }], admission_info: {} }
  const code = kindergartenDistrict(school, districts)
  assert.ok(code, 'some district holds the point')
})

// A plain city street with numbers in at least two catchments.
const street = streets.find(s => s.settlement === 'ГР.СОФИЯ' && new Set(s.numbers.values()).size >= 2)
const [number, ownId] = [...street.numbers.entries()][0]
const otherId = [...catchments.keys()].find(id => id !== ownId && !catchments.get(ownId).properties.neighbours.includes(id) &&
  !catchments.get(id).properties.neighbours.includes(ownId))
const g1School = (id, summary) => ({ id: 2, school_type: 'state', admission_info: { catchment_ids: { 'bg-sofia-gis': [id] }, cutoff_summary: summary } })
const g1Row = { stage: 'grade_1', rounds: 1, first: { last_admitted_group: 2, last_admitted_points: 3 } }

test('a saved first-grade address is placed in each school\'s group', () => {
  const saved = {
    grade_1: {
      startYear: 2026,
      answers: GRADE1_INITIAL,
      addresses: [{ check: { streetId: street.id, number, schoolId: ownId }, since: '2018-01-01' }],
    },
  }
  const own = savedVerdictFor(g1School(ownId, [g1Row]), saved, 'grade_1_4', data)
  assert.equal(own.verdict, 'above')
  assert.equal(own.yours.group.min, 1)
  const other = savedVerdictFor(g1School(otherId, [g1Row]), saved, null, data)
  assert.equal(other.verdict, 'below')
  assert.equal(other.yours.group.min, 4)
  assert.equal(savedVerdictFor(g1School(ownId, []), saved, null, data).verdict, 'noCutoff')
})

test('first grade needs a checked address to compare schools', () => {
  const saved = { grade_1: { startYear: 2026, answers: GRADE1_INITIAL, addresses: [{ check: null, inCatchment: true, since: '2018-01-01' }] } }
  assert.equal(savedVerdictFor(g1School(ownId, [g1Row]), saved, null, data).verdict, 'needsAddress')
  assert.equal(savedVerdictFor({ school_type: 'state', admission_info: {} }, saved, null, data), null, 'no catchment, no first grade')
})

test('rows that disagree read as mixed', () => {
  const yours = { points: { min: 10, max: 10 } }
  assert.equal(compareRows([{ first: { last_admitted_points: 8 } }, { first: { last_admitted_points: 12 } }], yours, null).verdict, 'mixed')
  assert.equal(compareRows([{ first: { last_admitted_points: 8 } }], yours, null).verdict, 'above')
  assert.equal(compareRows([{ first: {} }], yours, null).verdict, 'noCutoff')
})

test('a school with a preparatory group and first grade gets both saved results', async () => {
  const { savedVerdictsFor } = await import('./schoolVerdict.js')
  const saved = {
    kindergarten: { startYear: 2026, answers: { ...kgAnswers, birthYear: 2020, additional: 'sen' } },
    grade_1: { startYear: 2026, answers: GRADE1_INITIAL, addresses: [{ check: { streetId: street.id, number, schoolId: ownId }, since: '2018-01-01' }] },
  }
  const school = g1School(ownId, [g1Row, { stage: 'preparatory', age_group: 'preschool', rounds: 1, first: { last_admitted_points: 5 } }])
  const verdicts = savedVerdictsFor(school, saved, null, data)
  assert.deepEqual(verdicts.map(v => v.stage), ['kindergarten', 'grade_1'])
  assert.equal(verdicts[0].specialPlaces, 'sen', 'SEN places are a separate path, kept beside the verdict')
})
