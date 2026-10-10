import { getBenchmarkComparison, getBenchmarkToneClasses } from './nvo.js'

// How NVO results are coloured and explained on the card, the compare page and the detail
// page. Kept in one place so the three agree.

export function formatPercent(value, decimals = 1) {
  if (value == null || Number.isNaN(value)) return null
  return Number(value).toFixed(decimals)
}

export function getPerformanceStyle(value) {
  if (value == null) return { text: 'text-neutral-600', bg: 'bg-neutral-300' }
  if (value >= 75) return { text: 'text-emerald-500', bg: 'bg-emerald-500' }
  if (value >= 60) return { text: 'text-amber-500', bg: 'bg-amber-500' }
  return { text: 'text-red-500', bg: 'bg-red-500' }
}

/** Colour against the Sofia average for the same exam and year when known, else by score band. */
export function getNvoValueStyle({ value, examType, year, subjectKey, examAverages }) {
  const benchmark = getBenchmarkComparison({
    examType,
    year,
    subjectKey,
    value,
    examAverages,
  })

  if (benchmark) {
    return { text: benchmark.textClass, benchmark }
  }

  return { text: getPerformanceStyle(value).text, benchmark: null }
}

// Combined (Bulgarian + maths) result against the Sofia schools' average for the same exam and
// year, with the same ±5 pp tolerance as the detail page, so the colours agree.
export function getCombinedBenchmark(nvoDetail, year, value, examAverages) {
  const national = examAverages?.by_year?.[nvoDetail?.examType]?.[String(year)]
  if (value == null || national?.math == null || national?.bulgarian == null) return null
  const nationalValue = (Number(national.math) + Number(national.bulgarian)) / 2
  const diff = value - nationalValue
  const tone = diff >= 5 ? 'above' : diff <= -5 ? 'below' : 'near'
  return { nationalValue, ...getBenchmarkToneClasses(tone) }
}

const BENCHMARK_TOOLTIP_KEYS = {
  above: 'academicPerformance.tooltipBenchmarkAbove',
  below: 'academicPerformance.tooltipBenchmarkBelow',
}

export function getBenchmarkTooltip(value, benchmark, t) {
  if (value == null || !benchmark) return null

  const key = BENCHMARK_TOOLTIP_KEYS[benchmark.tone] || 'academicPerformance.tooltipBenchmarkNear'
  return t(key, {
    value: formatPercent(value, 1),
    diff: Math.abs(benchmark.diff).toFixed(1),
    benchmark: formatPercent(benchmark.benchmarkValue, 1),
  })
}

const TREND_TOOLTIP_KEYS = {
  '↑': 'academicPerformance.tooltipTrendUp',
  '↓': 'academicPerformance.tooltipTrendDown',
}

export function getTrendTooltip(latest, average, trend, t) {
  if (latest == null || average == null || !trend) return null

  const key = TREND_TOOLTIP_KEYS[trend.arrow] || 'academicPerformance.tooltipTrendFlat'
  return t(key, {
    latest: formatPercent(latest, 1),
    average: formatPercent(average, 1),
    diff: Math.abs(trend.diff).toFixed(1),
  })
}

/** Latest result against the school's own average: up or down at 2 points or more. */
export function getTrendInfo(latest, average) {
  if (latest == null || average == null) return null
  const diff = latest - average
  if (diff >= 2) return { arrow: '↑', className: 'text-emerald-500', diff }
  if (diff <= -2) return { arrow: '↓', className: 'text-red-500', diff }
  return { arrow: '→', className: 'text-neutral-400', diff }
}
