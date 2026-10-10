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
