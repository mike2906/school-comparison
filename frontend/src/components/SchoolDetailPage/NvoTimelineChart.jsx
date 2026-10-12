import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip } from 'recharts'
import { useTranslation } from 'react-i18next'
import { prepareNvoTimelineData } from '../../utils/nvo'

/**
 * NVO Exam Results Timeline Chart Component
 *
 * Displays interactive line chart showing Math & Bulgarian Language exam results over time
 * with average benchmarks and year-over-year change indicators.
 *
 * @param {Object} props
 * @param {Array} props.examResults - Array of exam result objects from API
 * @param {string} props.examType - Type of exam (nvo_4, nvo_7, nvo_10)
 * @param {Object} props.examAverages - Exam averages data from API
 * @param {Array} props.selectedSubjects - Array of subjects to display (['math', 'bulgarian'])
 */
function NvoTimelineChart({ examResults = [], examType, examAverages = null, selectedSubjects = ['math', 'bulgarian'] }) {
  const { t } = useTranslation()

  // Filter and transform data for the chart
  const chartData = prepareChartData(examResults, examType, examAverages)

  const showMath = selectedSubjects.includes('math')
  const showBulgarian = selectedSubjects.includes('bulgarian')
  const showNationalMath = showMath && chartData.some(item => item.nationalMath != null)
  const showNationalBulgarian = showBulgarian && chartData.some(item => item.nationalBulgarian != null)

  if (chartData.length === 0) {
    return (
      <div className="text-center py-12 text-neutral-500">
        <svg className="w-16 h-16 mx-auto mb-4 text-neutral-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
        <p>{t('academicPerformance.noData')}</p>
      </div>
    )
  }

  const hasMultipleYears = chartData.length > 1

  return (
    <div className="space-y-6">
      {/* Year-over-year change indicators (only if multiple years) */}
      {hasMultipleYears && (showBulgarian || showMath) && (
        <div className={`grid ${showBulgarian && showMath ? 'grid-cols-2' : 'grid-cols-1'} gap-4`}>
          {showBulgarian && (
            <YearOverYearBadge
              subject={t('schoolCard.nvo.subjectBulgarian')}
              data={chartData}
              dataKey="bulgarian"
              color="bg-purple-500"
            />
          )}
          {showMath && (
            <YearOverYearBadge
              subject={t('schoolCard.nvo.subjectMath')}
              data={chartData}
              dataKey="math"
              color="bg-blue-500"
            />
          )}
        </div>
      )}

      {/* Interactive line chart */}
      <div className="bg-neutral-50 rounded-xl p-6 border border-neutral-200 space-y-4">
        <ChartLegend
          t={t}
          showMath={showMath}
          showBulgarian={showBulgarian}
          showNationalMath={showNationalMath}
          showNationalBulgarian={showNationalBulgarian}
        />
        <ResponsiveContainer width="100%" height={400}>
          <LineChart
            data={chartData}
            margin={{ top: 5, right: 30, left: 0, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />

            <XAxis
              dataKey="year"
              stroke="#6b7280"
              style={{ fontSize: '14px', fontFamily: 'inherit' }}
            />

            <YAxis
              domain={[0, 100]}
              stroke="#6b7280"
              style={{ fontSize: '14px', fontFamily: 'inherit' }}
              label={{
                value: t('academicPerformance.scorePoints'),
                angle: -90,
                position: 'insideLeft',
                style: { fontSize: '14px', fill: '#6b7280' }
              }}
            />

            <Tooltip
              content={<CustomTooltip />}
              cursor={{ stroke: '#d1d5db', strokeWidth: 1 }}
            />

            {showNationalBulgarian && (
              <Line
                type="linear"
                dataKey="nationalBulgarian"
                name={t('academicPerformance.seriesNational', { subject: t('schoolCard.nvo.subjectBulgarian') })}
                stroke="#c084fc"
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={false}
                isAnimationActive={false}
              />
            )}

            {showNationalMath && (
              <Line
                type="linear"
                dataKey="nationalMath"
                name={t('academicPerformance.seriesNational', { subject: t('schoolCard.nvo.subjectMath') })}
                stroke="#60a5fa"
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={false}
                isAnimationActive={false}
              />
            )}

            {/* Bulgarian Language line */}
            {showBulgarian && (
              <Line
                type="linear"
                dataKey="bulgarian"
                name={t('academicPerformance.seriesSchool', { subject: t('schoolCard.nvo.subjectBulgarian') })}
                stroke="#8b5cf6"
                strokeWidth={3}
                dot={{
                  fill: '#8b5cf6',
                  strokeWidth: 2,
                  r: 5,
                  stroke: '#fff'
                }}
                activeDot={{
                  r: 7,
                  strokeWidth: 2
                }}
                isAnimationActive={false}
              />
            )}

            {/* Mathematics line */}
            {showMath && (
              <Line
                type="linear"
                dataKey="math"
                name={t('academicPerformance.seriesSchool', { subject: t('schoolCard.nvo.subjectMath') })}
                stroke="#3b82f6"
                strokeWidth={3}
                dot={{
                  fill: '#3b82f6',
                  strokeWidth: 2,
                  r: 5,
                  stroke: '#fff'
                }}
                activeDot={{
                  r: 7,
                  strokeWidth: 2
                }}
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>

    </div>
  )
}

function ChartLegend({ t, showMath, showBulgarian, showNationalMath, showNationalBulgarian }) {
  const showNational = showNationalMath || showNationalBulgarian
  return (
    <div className="rounded-lg border border-neutral-200 bg-white/80 px-3 py-2 sm:p-4">
      {/* Phones: subject colours plus one dashed "Sofia average" benchmark key, so the legend
          fits in one or two short lines instead of four long ones. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-neutral-700 sm:hidden">
        {showBulgarian && <LegendLine color="#8b5cf6" label={t('schoolCard.nvo.subjectBulgarian')} />}
        {showMath && <LegendLine color="#3b82f6" label={t('schoolCard.nvo.subjectMath')} />}
        {showNational && (
          <LegendLine color="#9ca3af" dashed label={t('academicPerformance.nationalAverage')} />
        )}
      </div>
      <div className="hidden sm:flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-neutral-700">
        <span className="font-medium text-neutral-900">{t('academicPerformance.seriesLegend')}</span>
        {showBulgarian && (
          <LegendLine
            color="#8b5cf6"
            label={t('academicPerformance.seriesSchool', { subject: t('schoolCard.nvo.subjectBulgarian') })}
          />
        )}
        {showMath && (
          <LegendLine
            color="#3b82f6"
            label={t('academicPerformance.seriesSchool', { subject: t('schoolCard.nvo.subjectMath') })}
          />
        )}
        {showNationalBulgarian && (
          <LegendLine
            color="#c084fc"
            dashed
            label={t('academicPerformance.seriesNational', { subject: t('schoolCard.nvo.subjectBulgarian') })}
          />
        )}
        {showNationalMath && (
          <LegendLine
            color="#60a5fa"
            dashed
            label={t('academicPerformance.seriesNational', { subject: t('schoolCard.nvo.subjectMath') })}
          />
        )}
      </div>
    </div>
  )
}

function LegendLine({ color, label, dashed = false }) {
  return (
    <div className="inline-flex items-center gap-1.5 sm:gap-2 min-w-0">
      <svg width="20" height="10" viewBox="0 0 28 10" aria-hidden="true" className="shrink-0 sm:w-7">
        <line
          x1="1"
          y1="5"
          x2="27"
          y2="5"
          stroke={color}
          strokeWidth={dashed ? 2 : 3}
          strokeDasharray={dashed ? '6 4' : undefined}
          strokeLinecap="round"
        />
      </svg>
      <span>{label}</span>
    </div>
  )
}

function BenchmarkDelta({ benchmarkInfo }) {
  if (!benchmarkInfo) return null

  const sign = benchmarkInfo.diff > 0 ? '+' : ''

  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${benchmarkInfo.badgeClass}`}
    >
      {benchmarkInfo.label} · {sign}{benchmarkInfo.diff.toFixed(1)} {benchmarkInfo.pointsLabel}
    </span>
  )
}

/**
 * Custom tooltip showing year, exact score, and change from previous year
 */
function CustomTooltip({ active, payload, label }) {
  const { t } = useTranslation()

  if (!active || !payload || payload.length === 0) {
    return null
  }

  return (
    <div className="bg-white rounded-lg shadow-lg border border-neutral-200 p-4">
      <p className="font-semibold text-neutral-900 mb-2">{label}</p>
      {payload.map((entry, index) => {
        const value = entry.value
        const previousValue = entry.payload[`${entry.dataKey}Previous`]
        const change = previousValue != null ? value - previousValue : null
        const benchmarkInfo = getBenchmarkInfo(entry.dataKey, entry.payload, t)

        return (
          <div key={index} className="mb-2 last:mb-0">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: entry.color }}
                />
                <span className="text-sm text-neutral-700">{entry.name}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-neutral-900">
                  {t('academicPerformance.pointsValue', { value: value.toFixed(1) })}
                </span>
                {change != null && (
                  <span className={`text-xs font-medium ${getChangeColor(change)}`}>
                    {change > 0 ? '+' : ''}{change.toFixed(1)} {t('academicPerformance.pointsShort')} {getChangeArrow(change)}
                  </span>
                )}
              </div>
            </div>
            {benchmarkInfo && (
              <div className="ml-5 mt-1 text-xs text-neutral-500">
                {t('academicPerformance.nationalBenchmarkValue', { value: benchmarkInfo.benchmarkValue.toFixed(1) })} ·{' '}
                <span className={benchmarkInfo.textClass}>
                  {benchmarkInfo.label}
                </span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Year-over-year change badge component
 */
function YearOverYearBadge({ subject, data, dataKey, color }) {
  const { t } = useTranslation()

  if (data.length < 2) {
    return null
  }

  const latest = data[data.length - 1]
  const previous = data[data.length - 2]

  const currentValue = latest[dataKey]
  const previousValue = previous[dataKey]

  if (currentValue == null || previousValue == null) {
    return null
  }

  const change = currentValue - previousValue
  const arrow = getChangeArrow(change)
  const changeColorClass = getChangeColor(change)
  const benchmarkInfo = getBenchmarkInfo(dataKey, latest, t)

  return (
    <div className="bg-white rounded-xl p-4 border border-neutral-200">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className={`w-2 h-2 rounded-full ${color}`}></div>
        <h4 className="text-sm font-medium text-neutral-700">{subject}</h4>
        {benchmarkInfo && <BenchmarkDelta benchmarkInfo={benchmarkInfo} />}
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-2xl font-bold ${benchmarkInfo?.textClass || 'text-neutral-900'}`}>
          {t('academicPerformance.pointsValue', { value: currentValue.toFixed(1) })}
        </span>
        <span className={`text-sm font-semibold ${changeColorClass}`}>
          {change > 0 ? '+' : ''}{change.toFixed(1)} {t('academicPerformance.pointsShort')} {arrow}
        </span>
      </div>
      <p className="text-xs text-neutral-500 mt-1">
        {t('academicPerformance.vsLastYear')}: {t('academicPerformance.pointsValue', { value: previousValue.toFixed(1) })}
        {benchmarkInfo && (
          <> • {t('academicPerformance.nationalBenchmarkValue', { value: benchmarkInfo.benchmarkValue.toFixed(1) })}</>
        )}
      </p>
    </div>
  )
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Prepare and transform exam results data for Recharts
 */
function prepareChartData(examResults, examType, examAverages) {
  return prepareNvoTimelineData(examResults, examType, examAverages)
}

function getBenchmarkTone(tone) {
  if (tone === 'above') {
    return {
      textClass: 'text-emerald-700',
      badgeClass: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    }
  }
  if (tone === 'below') {
    return {
      textClass: 'text-red-700',
      badgeClass: 'border-red-200 bg-red-50 text-red-700',
    }
  }

  return {
    textClass: 'text-amber-700',
    badgeClass: 'border-amber-200 bg-amber-50 text-amber-700',
  }
}

function getBenchmarkInfo(subjectKey, point, t) {
  const benchmarkKeyMap = {
    bulgarian: 'nationalBulgarian',
    math: 'nationalMath',
  }

  const benchmarkKey = benchmarkKeyMap[subjectKey]
  const value = point?.[subjectKey]
  const benchmarkValue = benchmarkKey ? point?.[benchmarkKey] : null

  if (value == null || benchmarkValue == null) {
    return null
  }

  const diff = value - benchmarkValue
  const tone = diff >= 5 ? 'above' : diff <= -5 ? 'below' : 'near'
  const toneClasses = getBenchmarkTone(tone)
  const labelKey = (
    tone === 'above'
      ? 'academicPerformance.aboveBenchmark'
      : tone === 'below'
        ? 'academicPerformance.belowBenchmark'
        : 'academicPerformance.nearBenchmark'
  )

  return {
    diff,
    benchmarkValue,
    label: t(labelKey),
    pointsLabel: t('academicPerformance.pointsShort'),
    ...toneClasses,
  }
}

/**
 * Get color class for year-over-year change
 */
function getChangeColor(change) {
  if (Math.abs(change) < 1) return 'text-neutral-500'
  if (change >= 2) return 'text-emerald-600'
  if (change <= -2) return 'text-red-600'
  return 'text-neutral-600'
}

/**
 * Get arrow indicator for change direction
 */
function getChangeArrow(change) {
  if (Math.abs(change) < 1) return '→'
  if (change > 0) return '↑'
  return '↓'
}

export default NvoTimelineChart
