import test from 'node:test'
import assert from 'node:assert/strict'
import {
  formatPercent,
  getBenchmarkTooltip,
  getCombinedBenchmark,
  getNvoValueStyle,
  getPerformanceStyle,
  getTrendInfo,
  getTrendTooltip,
} from './nvoDisplay.js'

const t = (key, params) => `${key} ${JSON.stringify(params)}`

const examAverages = {
  by_year: {
    nvo_7: { 2025: { bulgarian: 60, math: 40 } },
  },
}

test('formatPercent rounds to one decimal and passes missing values through as null', () => {
  assert.equal(formatPercent(72.456), '72.5')
  assert.equal(formatPercent(72.456, 0), '72')
  assert.equal(formatPercent(null), null)
  assert.equal(formatPercent(Number.NaN), null)
})

test('performance bands: 75 and up green, 60 and up amber, below red, missing neutral', () => {
  assert.equal(getPerformanceStyle(75).text, 'text-emerald-500')
  assert.equal(getPerformanceStyle(74.9).text, 'text-amber-500')
  assert.equal(getPerformanceStyle(60).text, 'text-amber-500')
  assert.equal(getPerformanceStyle(59.9).text, 'text-red-500')
  assert.equal(getPerformanceStyle(null).text, 'text-neutral-600')
})

test('NVO value colour follows the Sofia average when there is one, else the score band', () => {
  const above = getNvoValueStyle({ value: 66, examType: 'nvo_7', year: 2025, subjectKey: 'bulgarian', examAverages })
  assert.equal(above.benchmark.tone, 'above')
  assert.equal(above.text, 'text-emerald-700')

  const near = getNvoValueStyle({ value: 43, examType: 'nvo_7', year: 2025, subjectKey: 'math', examAverages })
  assert.equal(near.benchmark.tone, 'near')

  const noAverage = getNvoValueStyle({ value: 80, examType: 'nvo_4', year: 2025, subjectKey: 'math', examAverages })
  assert.equal(noAverage.benchmark, null)
  assert.equal(noAverage.text, 'text-emerald-500')
})

test('combined benchmark compares against the mean of the Bulgarian and maths averages', () => {
  const detail = { examType: 'nvo_7' }
  assert.equal(getCombinedBenchmark(detail, 2025, 56, examAverages).nationalValue, 50)
  assert.equal(getCombinedBenchmark(detail, 2025, 56, examAverages).textClass, 'text-emerald-700')
  assert.equal(getCombinedBenchmark(detail, 2025, 46, examAverages).textClass, 'text-amber-700')
  assert.equal(getCombinedBenchmark(detail, 2025, 44.9, examAverages).textClass, 'text-red-700')
  assert.equal(getCombinedBenchmark(detail, 2024, 56, examAverages), null)
  assert.equal(getCombinedBenchmark(detail, 2025, null, examAverages), null)
})

test('trend is up or down from 2 points against the school average', () => {
  assert.equal(getTrendInfo(72, 70).arrow, '↑')
  assert.equal(getTrendInfo(71.9, 70).arrow, '→')
  assert.equal(getTrendInfo(68, 70).arrow, '↓')
  assert.equal(getTrendInfo(null, 70), null)
})

test('tooltips pick the key for the direction and format the numbers', () => {
  const benchmark = { tone: 'below', diff: -7.25, benchmarkValue: 60 }
  assert.equal(
    getBenchmarkTooltip(52.75, benchmark, t),
    'academicPerformance.tooltipBenchmarkBelow {"value":"52.8","diff":"7.3","benchmark":"60.0"}',
  )
  assert.match(getBenchmarkTooltip(61, { tone: 'near', diff: 1, benchmarkValue: 60 }, t), /^academicPerformance.tooltipBenchmarkNear /)
  assert.equal(getBenchmarkTooltip(null, benchmark, t), null)

  const trend = getTrendInfo(65, 70)
  assert.equal(
    getTrendTooltip(65, 70, trend, t),
    'academicPerformance.tooltipTrendDown {"latest":"65.0","average":"70.0","diff":"5.0"}',
  )
  assert.match(getTrendTooltip(70, 70, getTrendInfo(70, 70), t), /^academicPerformance.tooltipTrendFlat /)
  assert.equal(getTrendTooltip(70, null, trend, t), null)
})
