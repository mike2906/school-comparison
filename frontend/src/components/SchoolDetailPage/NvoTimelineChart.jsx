import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine } from 'recharts'
import { useTranslation } from 'react-i18next'

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
  const { t, i18n } = useTranslation()

  // Filter and transform data for the chart
  const chartData = prepareChartData(examResults, examType)

  const showMath = selectedSubjects.includes('math')
  const showBulgarian = selectedSubjects.includes('bulgarian')

  // Get average for this exam type (overall average across all years)
  const average = examAverages?.overall?.[examType] || null

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
      <div className="bg-neutral-50 rounded-xl p-6 border border-neutral-200">
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
                value: t('academicPerformance.scorePercent'),
                angle: -90,
                position: 'insideLeft',
                style: { fontSize: '14px', fill: '#6b7280' }
              }}
            />

            {/* Average benchmark (if available) */}
            {average != null && (
              <ReferenceLine
                y={average}
                stroke="#9ca3af"
                strokeDasharray="5 5"
                label={{
                  value: t('academicPerformance.average'),
                  position: 'insideTopRight',
                  fill: '#6b7280',
                  fontSize: 12
                }}
              />
            )}

            <Tooltip
              content={<CustomTooltip />}
              cursor={{ stroke: '#d1d5db', strokeWidth: 1 }}
            />

            <Legend
              wrapperStyle={{ paddingTop: '20px' }}
              iconType="line"
            />

            {/* Bulgarian Language line */}
            {showBulgarian && (
              <Line
                type="monotone"
                dataKey="bulgarian"
                name={t('schoolCard.nvo.subjectBulgarian')}
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
                type="monotone"
                dataKey="math"
                name={t('schoolCard.nvo.subjectMath')}
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

      {/* Performance legend */}
      <div className="flex items-center justify-center gap-6 text-xs text-neutral-600">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-emerald-500"></div>
          <span>{t('academicPerformance.excellent')} (&gt;75%)</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-amber-500"></div>
          <span>{t('academicPerformance.good')} (60-75%)</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full bg-red-500"></div>
          <span>{t('academicPerformance.needsImprovement')} (&lt;60%)</span>
        </div>
      </div>
    </div>
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
        const performanceColor = getPerformanceColor(value)

        return (
          <div key={index} className="flex items-center justify-between gap-4 mb-1">
            <div className="flex items-center gap-2">
              <div
                className="w-3 h-3 rounded-full"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-sm text-neutral-700">{entry.name}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={`text-sm font-semibold ${performanceColor}`}>
                {value.toFixed(1)}%
              </span>
              {change != null && (
                <span className={`text-xs font-medium ${getChangeColor(change)}`}>
                  {change > 0 ? '+' : ''}{change.toFixed(1)}% {getChangeArrow(change)}
                </span>
              )}
            </div>
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
  const changePercent = ((change / previousValue) * 100).toFixed(1)
  const arrow = getChangeArrow(change)
  const changeColorClass = getChangeColor(change)

  return (
    <div className="bg-white rounded-xl p-4 border border-neutral-200">
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-2 h-2 rounded-full ${color}`}></div>
        <h4 className="text-sm font-medium text-neutral-700">{subject}</h4>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold text-neutral-900">
          {currentValue.toFixed(1)}%
        </span>
        <span className={`text-sm font-semibold ${changeColorClass}`}>
          {change > 0 ? '+' : ''}{change.toFixed(1)}% {arrow}
        </span>
      </div>
      <p className="text-xs text-neutral-500 mt-1">
        {t('academicPerformance.vsLastYear')}: {previousValue.toFixed(1)}%
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
function prepareChartData(examResults, examType) {
  // Filter relevant results
  const relevant = examResults.filter(result =>
    result.exam_type === examType &&
    result.metric.toLowerCase().includes('average')
  )

  // Group by year
  const byYear = {}
  relevant.forEach(result => {
    const year = result.year
    const subject = result.subject.toLowerCase()

    if (!byYear[year]) {
      byYear[year] = { year }
    }

    if (subject.includes('bulgarian')) {
      byYear[year].bulgarian = parseFloat(result.value)
    } else if (subject.includes('math')) {
      byYear[year].math = parseFloat(result.value)
    }
  })

  // Convert to array and sort by year
  const chartData = Object.values(byYear)
    .filter(item => item.bulgarian != null || item.math != null)
    .sort((a, b) => a.year - b.year)

  // Add previous year values for change calculation
  chartData.forEach((item, index) => {
    if (index > 0) {
      const previous = chartData[index - 1]
      if (item.bulgarian != null && previous.bulgarian != null) {
        item.bulgarianPrevious = previous.bulgarian
      }
      if (item.math != null && previous.math != null) {
        item.mathPrevious = previous.math
      }
    }
  })

  return chartData
}

/**
 * Get color class based on performance threshold
 */
function getPerformanceColor(score) {
  if (score >= 75) return 'text-emerald-600'
  if (score >= 60) return 'text-amber-600'
  return 'text-red-600'
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
