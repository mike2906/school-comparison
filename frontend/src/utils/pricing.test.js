import test from 'node:test'
import assert from 'node:assert/strict'

import {
  selectPricingCohort,
  groupPricingByAcademicYear,
  monthlyEquivalent,
  isInstallmentPlan,
  YEAR_STATUS,
  toEur,
  formatPriceLabel,
  displayPrice,
  statedTuition,
  tuitionSortValue,
  priceAgeGroupLabel,
  groupRowsByAgeGroup,
  sortByCategory,
  intakeYearStart,
} from './pricing.js'

const row = (overrides = {}) => ({
  id: 1,
  category: 'tuition',
  amount: 500,
  currency: 'EUR',
  period: 'yearly',
  academic_year: null,
  academic_year_canonical: null,
  year_status: YEAR_STATUS.NOT_STATED,
  ...overrides,
})

const current = (overrides = {}) =>
  row({
    academic_year: '2026/2027',
    academic_year_canonical: '2026/2027',
    year_status: YEAR_STATUS.CURRENT,
    ...overrides,
  })

const dated = (year, overrides = {}) =>
  row({
    academic_year: year,
    academic_year_canonical: year,
    year_status: YEAR_STATUS.DATED_OTHER,
    ...overrides,
  })

test('selectPricingCohort returns null when there is nothing to show', () => {
  assert.equal(selectPricingCohort([]), null)
  assert.equal(selectPricingCohort(undefined), null)
})

test('selectPricingCohort prefers the current year and excludes older rows', () => {
  // The school 609 shape: fees stored for both the current and the previous year.
  // The headline price must not be drawn from the cheaper historical row.
  const cohort = selectPricingCohort([
    dated('2025/2026', { id: 1, amount: 400 }),
    current({ id: 2, amount: 600 }),
  ])

  assert.equal(cohort.yearStatus, YEAR_STATUS.CURRENT)
  assert.equal(cohort.academicYear, '2026/2027')
  assert.deepEqual(cohort.rows.map(r => r.id), [2])
})

test('selectPricingCohort falls back to undated rows when no current-year price exists', () => {
  const cohort = selectPricingCohort([
    dated('2025/2026', { id: 1 }),
    row({ id: 2, amount: 700 }),
  ])

  assert.equal(cohort.yearStatus, YEAR_STATUS.NOT_STATED)
  assert.equal(cohort.academicYear, null)
  assert.deepEqual(cohort.rows.map(r => r.id), [2])
})

test('selectPricingCohort falls back to the most recent dated year as a last resort', () => {
  const cohort = selectPricingCohort([
    dated('2024/2025', { id: 1 }),
    dated('2025/2026', { id: 2 }),
    dated('2025/2026', { id: 3, category: 'food' }),
  ])

  assert.equal(cohort.yearStatus, YEAR_STATUS.DATED_OTHER)
  assert.equal(cohort.academicYear, '2025/2026')
  assert.deepEqual(cohort.rows.map(r => r.id), [2, 3])
})

test('selectPricingCohort never mixes years within one cohort', () => {
  const cohort = selectPricingCohort([
    current({ id: 1 }),
    dated('2025/2026', { id: 2 }),
    row({ id: 3 }),
  ])

  const years = new Set(cohort.rows.map(r => r.academic_year_canonical))
  assert.equal(years.size, 1)
})

test('groupPricingByAcademicYear groups by year, newest first, undated last', () => {
  const groups = groupPricingByAcademicYear([
    row({ id: 1 }),
    dated('2024/2025', { id: 2 }),
    current({ id: 3 }),
    dated('2024/2025', { id: 4 }),
  ])

  assert.deepEqual(groups.map(g => g.academicYear), ['2026/2027', '2024/2025', null])
  assert.deepEqual(groups[1].rows.map(r => r.id), [2, 4])
  assert.equal(groups[2].yearStatus, YEAR_STATUS.NOT_STATED)
})

test('groupPricingByAcademicYear gives each group its own status', () => {
  const groups = groupPricingByAcademicYear([current({ id: 1 }), dated('2025/2026', { id: 2 })])

  assert.deepEqual(groups.map(g => g.yearStatus), [
    YEAR_STATUS.CURRENT,
    YEAR_STATUS.DATED_OTHER,
  ])
})

test('groupPricingByAcademicYear returns an empty list for no pricing', () => {
  assert.deepEqual(groupPricingByAcademicYear([]), [])
})

test('historical cohort is used only when no current and no undated cohort exists', () => {
  // With a current cohort present, history is not selected.
  assert.equal(
    selectPricingCohort([current({ id: 1 }), dated('2025/2026', { id: 2 })]).yearStatus,
    YEAR_STATUS.CURRENT,
  )
  // With undated prices present, history is still not selected.
  assert.equal(
    selectPricingCohort([row({ id: 1 }), dated('2025/2026', { id: 2 })]).yearStatus,
    YEAR_STATUS.NOT_STATED,
  )
  // Only when neither exists does the historical cohort become the fallback.
  assert.equal(
    selectPricingCohort([dated('2025/2026', { id: 1 })]).yearStatus,
    YEAR_STATUS.DATED_OTHER,
  )
})

test('historical fallback carries its real academic year for labelling', () => {
  // The card renders this value, so a fallback price is never shown bare.
  const cohort = selectPricingCohort([
    dated('2024/2025', { id: 1, amount: 300 }),
    dated('2025/2026', { id: 2, amount: 650 }),
  ])

  assert.equal(cohort.yearStatus, YEAR_STATUS.DATED_OTHER)
  assert.equal(cohort.academicYear, '2025/2026')
  assert.notEqual(cohort.academicYear, null)
  // The headline price comes from that same year, not the older one.
  assert.deepEqual(cohort.rows.map(r => r.amount), [650])
})

test('monthlyEquivalent converts only an explicit monthly, yearly or quarterly period', () => {
  assert.equal(monthlyEquivalent({ amount: 500, period: 'monthly' }), 500)
  assert.equal(monthlyEquivalent({ amount: 6000, period: 'yearly' }), 500)
  assert.equal(monthlyEquivalent({ amount: 1500, period: 'quarter' }), 500)
})

test('monthlyEquivalent does not fall through to monthly for other periods', () => {
  // Regression: SearchPage used to return the raw amount for anything that was not
  // yearly or quarterly, so a EUR 4,725 semester fee ranked as EUR 4,725 a month.
  assert.equal(monthlyEquivalent({ amount: 4725, period: 'semester' }), null)
  assert.equal(monthlyEquivalent({ amount: 6300, period: 'term' }), null)
  assert.equal(monthlyEquivalent({ amount: 1102, period: 'one_time' }), null)
})

test('monthlyEquivalent does not invent a period the school did not state', () => {
  assert.equal(monthlyEquivalent({ amount: 88, period: null }), null)
  assert.equal(monthlyEquivalent({ amount: 88 }), null)
})

test('monthlyEquivalent uses amount_min for a range and ignores rows with no amount', () => {
  assert.equal(monthlyEquivalent({ amount_min: 400, amount_max: 700, period: 'monthly' }), 400)
  assert.equal(monthlyEquivalent({ period: 'monthly' }), null)
})

test('isInstallmentPlan recognises instalment plan names, as it did in SchoolCard', () => {
  assert.equal(isInstallmentPlan({ plan_name: '2 installments' }), true)
  assert.equal(isInstallmentPlan({ plan_name: 'на 9 вноски' }), true)
  assert.equal(isInstallmentPlan({ plan_name: '9 вноски' }), true)
  assert.equal(isInstallmentPlan({ plan_name: 'Standard' }), false)
  assert.equal(isInstallmentPlan({}), false)
})

test('toEur converts BGN at the fixed euro rate and rejects other currencies', () => {
  assert.equal(toEur(100, 'EUR'), 100)
  assert.equal(toEur(195.583, 'BGN'), 100)
  assert.equal(toEur(100, 'USD'), null)
  assert.equal(toEur(null, 'EUR'), null)
})

test('statedTuition keeps the period the school states and never annualises', () => {
  const tuition = statedTuition([
    row({ id: 1, amount: 650, period: 'monthly', year_status: 'current', academic_year_canonical: '2026/2027' }),
    row({ id: 2, amount: 900, period: 'monthly', year_status: 'current', academic_year_canonical: '2026/2027' }),
    row({ id: 3, amount: 8000, period: 'yearly', year_status: 'current', academic_year_canonical: '2026/2027' }),
    row({ id: 4, amount: 75, category: 'registration', period: 'one_time', year_status: 'current' }),
  ])
  assert.deepEqual(
    [tuition.min, tuition.max, tuition.period, tuition.currency, tuition.academicYear],
    [650, 900, 'monthly', 'EUR', '2026/2027']
  )
})

test('statedTuition converts BGN, skips instalments, and falls back to a yearly fee', () => {
  const tuition = statedTuition([
    row({ id: 1, amount: 1955.83, currency: 'BGN', period: 'yearly', year_status: 'current' }),
    row({ id: 2, amount: 50, period: 'monthly', plan_name: '10 installments', year_status: 'current' }),
  ])
  assert.deepEqual([Math.round(tuition.min), tuition.period, tuition.currency], [1000, 'yearly', 'EUR'])
})

test('statedTuition keeps a currency without a euro rate and reports an unstated period', () => {
  const usd = statedTuition([row({ currency: 'USD', period: 'monthly', amount: 1000 })])
  assert.deepEqual([usd.min, usd.period, usd.currency], [1000, 'monthly', 'USD'])
  const unstated = statedTuition([row({ period: null, amount: 4000 })])
  assert.deepEqual([unstated.min, unstated.period], [4000, null])
  assert.equal(statedTuition([row({ category: 'food', amount: 100 })]), null)
  assert.equal(statedTuition(undefined), null)
})

test('statedTuition ignores rows outside the headline cohort', () => {
  const tuition = statedTuition([
    row({ id: 1, amount: 9000, period: 'yearly', year_status: 'current' }),
    row({ id: 2, amount: 300, period: 'monthly', year_status: 'dated_other', academic_year_canonical: '2023/2024' }),
  ])
  assert.deepEqual([tuition.min, tuition.period], [9000, 'yearly'])
})

test('tuitionSortValue orders by the cheapest headline tuition per month, in euro', () => {
  assert.equal(tuitionSortValue([row({ amount: 6000, period: 'yearly', year_status: 'current' })]), 500)
  assert.equal(tuitionSortValue([
    row({ id: 1, amount: 400, period: 'monthly', year_status: 'current' }),
    row({ id: 2, amount: 100, period: 'monthly', plan_name: '10 installments', year_status: 'current' }),
    row({ id: 3, amount: 100, period: 'monthly', year_status: 'dated_other', academic_year_canonical: '2023/2024' }),
  ]), 400)
  assert.equal(tuitionSortValue([row({ amount: 4000, period: 'semester', year_status: 'current' })]), null)
  assert.equal(tuitionSortValue([]), null)
})

test('statedTuition leads with the period of the cheapest fee per month, as the sort does', () => {
  const tuition = statedTuition([
    row({ id: 1, amount: 900, period: 'monthly', year_status: 'current' }),
    row({ id: 2, amount: 3000, period: 'yearly', year_status: 'current' }),
  ])
  assert.deepEqual([tuition.min, tuition.period], [3000, 'yearly'])
})

test('displayPrice shows lev in euro and keeps other currencies', () => {
  assert.deepEqual(displayPrice(195.583, 'BGN'), { value: 100, currency: 'EUR' })
  assert.deepEqual(displayPrice(100, 'USD'), { value: 100, currency: 'USD' })
  assert.deepEqual(displayPrice(null, null), { value: null, currency: 'EUR' })
})

test('formatPriceLabel shows BGN rows in euro and falls back to price on request', () => {
  const t = (key) => key
  assert.equal(formatPriceLabel({ amount: 1955.83, currency: 'BGN' }, 'en-GB', t), '€1,000')
  assert.equal(formatPriceLabel({ amount: 500, currency: 'EUR' }, 'en-GB', t), '€500')
  assert.equal(formatPriceLabel({ amount_min: 391.166, amount_max: 782.332, currency: 'BGN' }, 'en-GB', t), '€200–€400')
  assert.equal(formatPriceLabel({ amount_min: 500, amount_max: 500, currency: 'EUR' }, 'en-GB', t), '€500')
  assert.equal(formatPriceLabel({ currency: 'EUR' }, 'en-GB', t), 'pricing.priceOnRequest')
})

// A `t` that knows the grade labels and one ageGroups key, and echoes any other key.
const EN = {
  'ageGroups.preschool': 'Pre-school',
  'pricing.grades.preschool': 'Preschool',
  'pricing.grades.preschoolTo': 'Preschool to grade {{to}}',
  'pricing.grades.one': 'Grade {{n}}',
  'pricing.grades.range': 'Grades {{from}}–{{to}}',
  'educationLevels.kindergarten': 'Kindergarten',
}
const tEn = (key, vars = {}) =>
  key in EN ? EN[key].replace(/\{\{(\w+)\}\}/g, (_, name) => String(vars[name])) : key

test('priceAgeGroupLabel: known key translated, grades and stages as our label, other text as written', () => {
  assert.equal(priceAgeGroupLabel({ age_group: 'preschool' }, tEn), 'Pre-school')
  assert.equal(priceAgeGroupLabel({ age_group: 'ПК' }, tEn), 'Preschool')
  assert.equal(priceAgeGroupLabel({ age_group: 'VIII - XII клас' }, tEn), 'Grades 8–12')
  assert.equal(priceAgeGroupLabel({ age_group: 'Grade 1', plan_name: 'Grade 10 tuition' }, tEn), 'Grade 1')
  assert.equal(priceAgeGroupLabel({ age_group: 'Детска градина' }, tEn), 'Kindergarten')
  assert.equal(priceAgeGroupLabel({ age_group: 'Подготвителен курс' }, tEn), 'Подготвителен курс')
  assert.equal(priceAgeGroupLabel({ age_group: 'Grade 0', plan_name: 'Grade 0 / Plan 1' }, tEn), 'Preschool')
  assert.equal(priceAgeGroupLabel({ age_group: 'preschool', plan_name: 'Preschool fees' }, tEn), 'Pre-school')
  assert.equal(priceAgeGroupLabel({ age_group: 'grade 2 - 4', plan_name: 'Grade 2 - 4 / Plan 1' }, tEn), null)
  assert.equal(priceAgeGroupLabel({ age_group: ' ' }, tEn), null)
  assert.equal(priceAgeGroupLabel({}, tEn), null)
})

test('priceAgeGroupLabel: a plan name naming the same grades is not repeated, a wider one widens', () => {
  assert.equal(priceAgeGroupLabel({ age_group: 'VIII - XII клас', plan_name: '8–12: full payment' }, tEn), null)
  assert.equal(
    priceAgeGroupLabel({ age_group: 'ПК', plan_name: 'PK–7: full payment, enrolment after 20.02.2026' }, tEn),
    'Preschool to grade 7',
  )
  // ПК in the plan name is still explained, as it is for Latin PK.
  assert.equal(priceAgeGroupLabel({ age_group: 'ПК', plan_name: 'ПК-4: такса' }, tEn), 'Preschool to grade 4')
  // An instalment count is not a grade range: the grade stays, unwidened.
  assert.equal(priceAgeGroupLabel({ age_group: '1 клас', plan_name: 'Вноски 1-2: по 1500' }, tEn), 'Grade 1')
  assert.equal(priceAgeGroupLabel({ age_group: 'ПК', plan_name: 'Plan 1-2: monthly' }, tEn), 'Preschool')
})

test('groupRowsByAgeGroup: one band per age group, in page order, unnamed rows first', () => {
  const rows = [
    row({ id: 1, age_group: 'ПК' }),
    row({ id: 2, age_group: 'VIII - XII клас' }),
    row({ id: 3, age_group: 'пк ' }),
    row({ id: 4, age_group: null }),
    row({ id: 5, age_group: 'Детска градина' }),
  ]
  const bands = groupRowsByAgeGroup(rows, tEn)
  assert.deepEqual(bands.map(band => band.label), [null, 'Preschool', 'Grades 8–12', 'Kindergarten'])
  assert.deepEqual(bands.map(band => band.rows.map(r => r.id)), [[4], [1, 3], [2], [5]])
})

test('groupRowsByAgeGroup: a row whose plan name covers more grades is not hidden under the narrower band', () => {
  // 171: the PK–7 fee is filed under "ПК".
  const rows = [
    row({ id: 2896, age_group: 'ПК', plan_name: 'Full payment with Christmas discount' }),
    row({ id: 2897, age_group: 'ПК', plan_name: 'PK–7: full payment, enrolment after 20.02.2026' }),
    row({ id: 2900, age_group: 'VIII - XII клас', plan_name: '8–12: full payment' }),
  ]
  assert.deepEqual(
    groupRowsByAgeGroup(rows, tEn).map(band => [band.label, band.rows.map(r => r.id)]),
    [['Preschool', [2896]], ['Preschool to grade 7', [2897]], ['Grades 8–12', [2900]]],
  )
})

test('groupRowsByAgeGroup: fewer than two age groups is not split', () => {
  const t = (key) => key
  const rows = [row({ id: 1, age_group: 'Grade 1' }), row({ id: 2, age_group: null })]
  assert.deepEqual(groupRowsByAgeGroup(rows, t), [{ label: null, rows }])
  assert.deepEqual(groupRowsByAgeGroup([], t), [])
})

test('groupRowsByAgeGroup: a known age-group key is shown translated', () => {
  const bands = groupRowsByAgeGroup([row({ id: 1, age_group: 'preschool' }), row({ id: 2, age_group: 'Grade 1' })], tEn)
  assert.deepEqual(bands.map(band => band.label), ['Pre-school', 'Grade 1'])
})

test('statedTuition: a fee whose name states its period is in the range (517 grade 5)', () => {
  const rows = [
    current({ id: 1, age_group: '8. клас', amount: 7000, period: 'yearly' }),
    current({ id: 2, age_group: '5. клас', plan_name: 'ГОДИШНА ТАКСА', amount: 7810, period: null }),
  ]
  assert.deepEqual(
    [statedTuition(rows).min, statedTuition(rows).max, statedTuition(rows).period],
    [7000, 7810, 'yearly'],
  )
})

test('statedTuition and tuitionSortValue: the chosen age group only, never another group', () => {
  const rows = [
    current({ id: 1, age_group: 'Първа група', amount: 240, period: 'monthly' }),
    current({ id: 2, age_group: 'Втора група', amount: 266, period: 'monthly' }),
  ]
  assert.equal(statedTuition(rows, { ageGroup: 'second' }).min, 266)
  assert.equal(statedTuition(rows, { ageGroup: 'second' }).max, 266)
  assert.equal(statedTuition(rows, { ageGroup: 'third' }), null)
  assert.equal(tuitionSortValue(rows, { ageGroup: 'third' }), null)
  assert.equal(tuitionSortValue(rows, { ageGroup: 'second' }), 266)
  assert.equal(statedTuition(rows).min, 240)
})

test('isInstallmentPlan: one instalment named without a count, not the fee paid at once', () => {
  assert.equal(isInstallmentPlan({ plan_name: 'Годишна такса – първа вноска' }), true)
  assert.equal(isInstallmentPlan({ plan_name: 'Годишна такса - I вноска' }), true)
  for (const name of ['1-ва вноска', '2-ра вноска', 'I-ва вноска', 'Вноска 1', 'Вноска № 1', 'Годишна такса – вноска 2']) {
    assert.equal(isInstallmentPlan({ plan_name: name }), true, name)
  }
  assert.equal(isInstallmentPlan({ plan_name: 'Годишна такса / Плащане на една вноска' }), false)
  assert.equal(isInstallmentPlan({ plan_name: 'Годишна такса' }), false)
  // "Месечна вноска" is the monthly fee itself.
  assert.equal(isInstallmentPlan({ plan_name: 'Месечна вноска за обучение' }), false)
  assert.equal(isInstallmentPlan({ plan_name: 'Годишна такса (еднократна вноска)' }), false)
})

test('sortByCategory: tuition first, then the listing order, unknown categories last', () => {
  const rows = [
    row({ id: 1, category: 'food' }),
    row({ id: 2, category: 'tuition' }),
    row({ id: 3, category: 'mystery' }),
    row({ id: 4, category: 'tuition' }),
    row({ id: 5, category: 'registration' }),
  ]
  assert.deepEqual(sortByCategory(rows).map(r => r.id), [2, 4, 5, 1, 3])
})

// Buditel-like: this year's fee and next year's, as the API dates them in October 2026.
const OCTOBER = new Date(2026, 9, 11)
const SEPTEMBER = new Date(2026, 8, 15)
const thisYear = (overrides = {}) => row({
  amount: 8320, academic_year: '2026/2027', academic_year_canonical: '2026/2027',
  year_status: YEAR_STATUS.CURRENT, ...overrides,
})
const nextYear = (overrides = {}) => row({
  id: 2, amount: 8900, academic_year: '2027/2028', academic_year_canonical: '2027/2028',
  year_status: YEAR_STATUS.DATED_OTHER, ...overrides,
})

test('intakeYearStart: the year just starting in September, next September from October', () => {
  assert.equal(intakeYearStart(new Date(2026, 8, 1)), 2026)
  assert.equal(intakeYearStart(new Date(2026, 8, 30)), 2026)
  assert.equal(intakeYearStart(new Date(2026, 9, 1)), 2027)
  assert.equal(intakeYearStart(new Date(2026, 11, 31)), 2027)
  assert.equal(intakeYearStart(new Date(2027, 0, 1)), 2027)
  assert.equal(intakeYearStart(new Date(2027, 7, 31)), 2027)
})

test('from October the headline is next year\'s tuition, with this year\'s alongside', () => {
  const tuition = statedTuition([thisYear(), nextYear()], { today: OCTOBER })
  assert.equal(tuition.min, 8900)
  assert.equal(tuition.academicYear, '2027/2028')
  assert.equal(tuition.yearStatus, YEAR_STATUS.NEXT)
  assert.equal(tuition.currentYear.min, 8320)
  assert.equal(tuition.currentYear.academicYear, '2026/2027')
  assert.equal(tuitionSortValue([thisYear(), nextYear()], { today: OCTOBER }), 8900 / 12)
})

test('in September the headline stays on the year just starting', () => {
  const tuition = statedTuition([thisYear(), nextYear()], { today: SEPTEMBER })
  assert.equal(tuition.min, 8320)
  assert.equal(tuition.yearStatus, YEAR_STATUS.CURRENT)
  assert.equal(tuition.currentYear, undefined)
})

test('next year leads only with its own whole-fee tuition, for the chosen group', () => {
  // Only a registration fee for next year: this year's tuition stays the headline.
  const registrationOnly = [thisYear(), nextYear({ category: 'registration', amount: 490 })]
  assert.equal(statedTuition(registrationOnly, { today: OCTOBER }).min, 8320)
  // Next year's fee for another group only: this year's fee for the chosen one.
  const otherGroup = [thisYear({ age_group: 'first' }), nextYear({ age_group: 'second' })]
  const tuition = statedTuition(otherGroup, { ageGroup: 'first', today: OCTOBER })
  assert.equal(tuition.min, 8320)
  assert.equal(tuition.yearStatus, YEAR_STATUS.CURRENT)
  // Next year alone: no current-year line.
  assert.equal(statedTuition([nextYear()], { today: OCTOBER }).currentYear, null)
})

test('a year after next is still another year, never the intake one', () => {
  const later = nextYear({ academic_year: '2028/2029', academic_year_canonical: '2028/2029' })
  const tuition = statedTuition([thisYear(), later], { today: OCTOBER })
  assert.equal(tuition.min, 8320)
  assert.equal(selectPricingCohort([later], { today: OCTOBER }).yearStatus, YEAR_STATUS.DATED_OTHER)
})

test('groupPricingByAcademicYear marks the intake year as next, not as another year', () => {
  const groups = groupPricingByAcademicYear([thisYear(), nextYear()], { today: OCTOBER })
  assert.deepEqual(groups.map(group => [group.academicYear, group.yearStatus]), [
    ['2027/2028', YEAR_STATUS.NEXT],
    ['2026/2027', YEAR_STATUS.CURRENT],
  ])
  assert.equal(groupPricingByAcademicYear([nextYear()], { today: SEPTEMBER })[0].yearStatus, YEAR_STATUS.DATED_OTHER)
})

test('with no group chosen, next year leads only when it prices every group this year does', () => {
  const partial = [
    thisYear({ id: 1, age_group: 'Grade 1', amount: 5000 }),
    thisYear({ id: 2, age_group: 'Grade 12', amount: 9000 }),
    nextYear({ id: 3, age_group: 'Grade 1', amount: 6000 }),
  ]
  const tuition = statedTuition(partial, { today: OCTOBER })
  assert.equal(tuition.yearStatus, YEAR_STATUS.CURRENT)
  assert.deepEqual([tuition.min, tuition.max], [5000, 9000])
  // The parent chose grades 1–4: next year's grade 1 fee leads.
  assert.equal(statedTuition(partial, { ageGroup: 'grade_1_4', today: OCTOBER }).min, 6000)
})

test('price sort falls back to this year when next year\'s fee has no monthly figure', () => {
  const pricing = [thisYear({ period: 'monthly', amount: 700 }), nextYear({ period: 'semester' })]
  assert.equal(tuitionSortValue(pricing, { today: OCTOBER }), 700)
})

test('statedTuition: tuition paid "на 1 вноска" is the yearly fee, by stage name (610, 630)', () => {
  const today = new Date('2026-06-01')
  const otkrivatel = [
    current({ id: 1, age_group: 'Подготвителен клас', plan_name: 'Подготвителен клас / ПУК 1 / Плащане на 1 вноска', amount: 7400, period: null }),
    current({ id: 2, age_group: 'Начален и Прогимназиален етап', plan_name: 'Начален и Прогимназиален етап / ПЪК 2 – 1 – 7. клас / Плащане на 1 вноска', amount: 7650, period: null }),
    current({ id: 3, age_group: 'Гимназия', plan_name: 'Гимназия / 8 – 10. клас / Плащане на 1 вноска', amount: 7400, period: null }),
    current({ id: 4, age_group: '11 – 12. клас', plan_name: 'Дипломна програма / 11 – 12. клас / Плащане на 1 вноска', amount: 7800, period: null }),
  ]
  const headline = (rows, ageGroup) => {
    const stated = statedTuition(rows, { ageGroup, today })
    return stated && [stated.min, stated.max, stated.period]
  }
  assert.deepEqual(headline(otkrivatel, null), [7400, 7800, 'yearly'])
  assert.deepEqual(headline(otkrivatel, 'grade_1_4'), [7650, 7650, 'yearly'])
  assert.deepEqual(headline(otkrivatel, 'preschool'), [7400, 7400, 'yearly'])
  assert.deepEqual(groupRowsByAgeGroup(otkrivatel, tEn).map(band => band.label), ['Preschool', 'Grades 1–7', 'Grades 8–10', 'Grades 11–12'])

  const raynov = [
    row({ id: 1, plan_name: 'Предучилищен и от 1-ви до 7-ми клас', amount: 6150 }),
    row({ id: 2, plan_name: 'Гимназия', amount: 6640 }),
  ]
  assert.deepEqual(headline(raynov, 'grade_1_4'), [6150, 6150, 'yearly'])
  assert.deepEqual(headline(raynov, 'grade_8_12'), [6640, 6640, 'yearly'])
})

test('priceAgeGroupLabel: grades inside a whole-sentence age group (595)', () => {
  const price = {
    age_group: 'Годишна такса за 8. клас за учебната 2026/2027 год',
    plan_name: 'Годишна такса за 8. клас за учебната 2026/2027 година',
  }
  assert.equal(priceAgeGroupLabel(price, tEn), 'Grade 8')
})

test('isInstallmentPlan: the whole fee paid in one payment is not an instalment', () => {
  for (const name of ['Плащане на 1 вноска', 'Годишна такса / Плащане на една вноска', 'Payment in one instalment', '1 installment', 'Single installment']) {
    assert.equal(isInstallmentPlan({ plan_name: name }), false, name)
  }
  // A numbered first instalment is still one instalment.
  for (const name of ['Такса 1 вноска до 15.09', 'Цена 1 вноска до 15.09', 'Размер на 1 вноска', 'на 1 вноска от 10', 'Плащане на 2 вноски: 1 вноска до 15 септември', '10 вноски, 1 вноска = 500']) {
    assert.equal(isInstallmentPlan({ plan_name: name }), true, name)
  }
})

test('statedTuition: a ПГУ (preschool group at school) fee is not a grades 1–4 price (569)', () => {
  const rows = [
    row({ id: 1, plan_name: 'ПГУ от 15. 09.2026 г. до 31.05.2027 г. / Годишна такса обучение **', amount: 5900, period: null }),
    row({ id: 2, plan_name: 'ПГУ от 15.09.2026г. до 14.09.2027 г. / Годишна такса обучение **', amount: 8000, period: null }),
    row({ id: 3, age_group: '1-4 клас от 15.09.2026 г. до 31.05.2027 г.', plan_name: '1-4 клас от 15.09.2026 г. до 31.05.2027 г. / Годишна такса обучение **', amount: 5900, period: null }),
  ]
  const stated = statedTuition(rows, { ageGroup: 'grade_1_4', today: new Date('2026-06-01') })
  assert.deepEqual([stated.min, stated.max, stated.period], [5900, 5900, 'yearly'])
  assert.equal(statedTuition(rows, { ageGroup: 'preschool', today: new Date('2026-06-01') }).max, 8000)
})

