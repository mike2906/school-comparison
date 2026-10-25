import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { defaultStage, offersStage, rowsForStage, stageForAgeGroup, stageForChild, totalWithNeeds, yearCost } from './yearCost.js'

// The live fee rows of 596 Maple Bear, 172 St. Sofia and 635 Uwekind on 25 Oct 2026, checked by
// hand against their fee pages (2026/27, grade 1).
const SCHOOLS = JSON.parse(readFileSync(new URL('./testdata/yearCostSchools.json', import.meta.url)))
const today = new Date('2026-10-25T12:00:00Z')
const cost = (id, stage = 'grade_1') => yearCost(SCHOOLS[id], stage, { today })
const ids = options => options.map(item => item.row.id)

test('grade 1 requires tuition plus the fees every pupil pays', () => {
  // Maple Bear: 9,050 tuition (plan 1) + 550 materials.
  assert.deepEqual(cost(596).required, { value: { min: 9600, max: 9600, assumed: false }, missing: [] })
  // St. Sofia: the ПУК–III band's 8,900, not grades 4–7's 9,400.
  assert.deepEqual(cost(172).required, { value: { min: 8900, max: 8900, assumed: false }, missing: [] })
  // Uwekind: 7,880 + 600 resources + 450 for the compulsory club, whose period is not stated.
  assert.deepEqual(cost(635).required, { value: { min: 8930, max: 8930, assumed: true }, missing: [] })
})

test('tuition is the stage’s own price, not a payment plan or another grade’s', () => {
  assert.deepEqual(ids(cost(596).tuition.options), [6189])
  assert.deepEqual(ids(cost(596, 'grade_3').tuition.options), [6190])
  assert.deepEqual(ids(cost(172, 'grade_5').tuition.options), [5782])
  assert.equal(cost(172, 'grade_9').tuition.options.length, 0)
})

test('the same fee read from two PDFs is counted once, and a secondary-school PDF’s fees stay there', () => {
  // 1,340 food from the secondary PDF is inside the primary PDF's 1,290–1,340.
  assert.deepEqual(ids(cost(596).lines.food.options), [6193])
  // The 200 application fee is on the grades 8–12 PDF only.
  assert.deepEqual(ids(cost(596).firstYear.options), [6192])
  assert.deepEqual(ids(cost(596, 'grade_9').firstYear.options), [6202])
})

test('a bus with a one-way price beside it is the two-way one', () => {
  assert.deepEqual(ids(cost(596).lines.transport.options), [6194])
  assert.deepEqual(ids(cost(635).lines.transport.options), [5678])
})

test('ticked needs are added only when they come to one yearly price', () => {
  const all = ['food', 'extended_day', 'transport']
  // Maple Bear states each one per year; its food is a range.
  assert.deepEqual(totalWithNeeds(cost(596), all), { value: { min: 14140, max: 14190, assumed: false }, missing: [] })
  // Uwekind has lunch at two campuses and snacks: the parent's choice, so not added.
  assert.deepEqual(totalWithNeeds(cost(635), all).missing, [
    { line: 'food', reason: 'severalOptions' },
    { line: 'extended_day', reason: 'notStated' },
  ])
  // Unticked lines are never added.
  assert.deepEqual(totalWithNeeds(cost(596), []), cost(596).required)
})

test('a line the tuition says it includes or leaves out', () => {
  // "The annual tuition fee includes food" is on the grades 4–7 row only.
  assert.equal(cost(172, 'grade_5').lines.food.state, 'included')
  assert.equal(cost(172).lines.food.state, 'none')
  const excluded = [{
    id: 1, category: 'tuition', amount: 5000, currency: 'EUR', period: 'yearly', year_status: 'not_stated',
    pricing_context: { notes: 'В таксата не е включена храна.' },
  }]
  assert.equal(yearCost(excluded, 'grade_1', { today }).lines.food.state, 'excluded')
})

test('a monthly tuition is never made into a year', () => {
  const monthly = [
    { id: 1, category: 'tuition', amount: 650, currency: 'EUR', period: 'monthly', year_status: 'not_stated' },
    { id: 2, category: 'food', amount: 1200, currency: 'EUR', period: null, year_status: 'not_stated' },
  ]
  const result = yearCost(monthly, 'first', { today })
  assert.equal(result.required, null)
  // Nor is a fee without a period read as yearly on a monthly page.
  assert.equal(result.lines.food.options[0].perYear, null)
})

test('deposits and uniforms are listed apart from the first-year sum', () => {
  const rows = [
    { id: 1, category: 'tuition', amount: 9000, currency: 'EUR', period: 'yearly', year_status: 'not_stated' },
    { id: 2, category: 'registration', amount: 500, currency: 'EUR', period: 'one_time', year_status: 'not_stated' },
    { id: 3, category: 'registration', amount: 1000, currency: 'EUR', period: 'one_time', year_status: 'not_stated', pricing_context: { deposit: true } },
  ]
  const { firstYear } = yearCost(rows, 'grade_1', { today })
  assert.deepEqual(ids(firstYear.options), [2])
  assert.deepEqual(ids(firstYear.deposits), [3])
  assert.deepEqual(firstYear.value, { min: 500, max: 500, assumed: false })
})

test('a kindergarten group never gets a school’s fee', () => {
  // Uwekind's "Училище" tuition and the pupils' food at Lozen are not a kindergarten's.
  assert.equal(cost(635, 'first').tuition.options.length, 0)
  assert.deepEqual(rowsForStage([{ id: 1, category: 'food', age_group: 'Ученици в Лозен' }], 'second'), [])
  // Its preschool class fee is not grade 1's.
  assert.deepEqual(ids(cost(635, 'preschool').tuition.options), [5682])
  assert.ok(!ids(cost(635).lines.food.options).includes(5684))
})

test('the stage comes from the saved child or the search', () => {
  assert.equal(stageForChild(2020, 2027), 'grade_1')
  assert.equal(stageForChild(2021, 2027), 'preschool')
  assert.equal(stageForChild(2023, 2027), 'second')
  assert.equal(stageForChild(2026, 2027), 'nursery')
  assert.equal(stageForChild(null, 2027), null)
  assert.equal(stageForAgeGroup('grade_5_7'), 'grade_5')
  assert.equal(stageForAgeGroup('third'), 'third')
  assert.equal(stageForAgeGroup(null), null)
  // The child wins when it fits the search; a search for other grades starts at their first.
  assert.equal(defaultStage({ birthYear: 2020, startYear: 2027, ageGroup: 'grade_1_4', planningYear: 2027 }), 'grade_1')
  assert.equal(defaultStage({ birthYear: 2023, startYear: 2027, ageGroup: 'grade_5_7', planningYear: 2027 }), 'grade_5')
  // A start year already past plans for the coming one.
  assert.equal(defaultStage({ birthYear: 2019, startYear: 2025, planningYear: 2027 }), 'grade_2')
  assert.equal(defaultStage({ planningYear: 2027 }), 'grade_1')
})

test('the tuition’s words decide a line by clause and by whole word', () => {
  const tuition = notes => [{ id: 1, category: 'tuition', amount: 5000, currency: 'EUR', period: 'yearly', year_status: 'not_stated', pricing_context: { notes } }]
  const lines = notes => yearCost(tuition(notes), 'grade_1', { today }).lines
  const mixed = lines('Таксата включва учебни материали, но не включва храна.')
  assert.equal(mixed.materials.state, 'included')
  assert.equal(mixed.food.state, 'excluded')
  assert.equal(lines('The fee includes the full syllabus.').transport.state, 'none')
  assert.equal(lines('Включва ползване на материалната база.').materials.state, 'none')
  assert.equal(lines('Price not including lunch.').food.state, 'excluded')
  const list = lines('Tuition includes meals, transport and textbooks.')
  assert.deepEqual([list.food.state, list.transport.state, list.materials.state], ['included', 'included', 'included'])
})

test('a fee read as yearly from its notes only when they name no other period', () => {
  const rows = notes => [
    { id: 1, category: 'tuition', amount: 650, currency: 'EUR', period: 'monthly', year_status: 'not_stated' },
    { id: 2, category: 'food', amount: 150, currency: 'EUR', period: null, year_status: 'not_stated', pricing_context: { notes } },
  ]
  assert.equal(yearCost(rows('Изчислена за една учебна година.'), 'first', { today }).lines.food.options[0].perYear.assumed, false)
  assert.equal(yearCost(rows('150 € per month during the school year'), 'first', { today }).lines.food.options[0].perYear, null)
  assert.equal(yearCost(rows('за 4-годишни деца'), 'first', { today }).lines.food.options[0].perYear, null)
  assert.equal(yearCost(rows('paid per term, three terms a year'), 'first', { today }).lines.food.options[0].perYear, null)
})

test('a school with no kindergarten does not offer a kindergarten group', () => {
  const school = { locations: [{ age_groups: ['grade_1_4', 'grade_5_7'] }] }
  assert.equal(offersStage(school, 'first'), false)
  assert.equal(offersStage(school, 'grade_9'), true)
  assert.equal(offersStage({ locations: [{ age_groups: ['first', 'second'] }] }, 'grade_1'), false)
  assert.equal(offersStage({ locations: [] }, 'first'), true)
})
