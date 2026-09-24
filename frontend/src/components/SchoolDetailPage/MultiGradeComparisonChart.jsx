import React from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { useTranslation } from 'react-i18next'
import { formatPercent, getExamTypeLabel, getLineStyleForGrade } from './helpers'

/**
 * MultiGradeComparisonChart - Shows all grade levels on a single chart
 * Allows parents to see performance progression across grades
 *
 * Props:
 * - examResults: Array of all exam result objects
 * - availableExamTypes: Array of exam types to display (e.g., ['nvo_4', 'nvo_7', 'nvo_10'])
 * - selectedSubjects: Array of subjects to display (['math', 'bulgarian'])
 * - examAverages: Object with exam averages data from API
 */
function MultiGradeComparisonChart({ examResults, availableExamTypes, selectedSubjects = ['math', 'bulgarian'], examAverages }) {
  const { t } = useTranslation()

  if (!examResults || examResults.length === 0 || !availableExamTypes || availableExamTypes.length === 0) {
    return null
  }

  const showMath = selectedSubjects.includes('math')
  const showBulgarian = selectedSubjects.includes('bulgarian')

  // Calculate average across all displayed exam types
  const average = examAverages?.overall
    ? availableExamTypes.reduce((sum, type) => {
        const typeAvg = examAverages.overall[type]
        return typeAvg != null ? sum + typeAvg : sum
      }, 0) / availableExamTypes.filter(type => examAverages.overall[type] != null).length
    : null

  // Transform exam results into chart data
  // Group by year, then by exam type and subject
  const dataByYear = {}

  examResults
    .filter(r => r.metric?.includes('average') && availableExamTypes.includes(r.exam_type))
    .forEach(result => {
      const year = result.year
      if (!dataByYear[year]) {
        dataByYear[year] = { year, avg: average }
      }

      // Create keys like: nvo_4_math, nvo_4_bulgarian, nvo_7_math, etc.
      const subjectKey = result.subject?.toLowerCase().includes('math') ? 'math' : 'bulgarian'
      const dataKey = `${result.exam_type}_${subjectKey}`
      dataByYear[year][dataKey] = parseFloat(result.value)
    })

  const chartData = Object.values(dataByYear).sort((a, b) => a.year - b.year)

  if (chartData.length === 0) {
    return null
  }

  // Custom tooltip
  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-white p-3 rounded-lg shadow-lg border border-neutral-200 max-w-xs">
          <p className="font-semibold text-neutral-900 mb-2">{label}</p>
          {payload.map((entry, index) => {
            // Parse the dataKey to get exam type and subject
            const [, examType, subject] = entry.dataKey.match(/^(nvo_\d+)_(math|bulgarian)$/) || []
            if (!examType) return null

            return (
              <div key={index} className="flex items-center justify-between gap-4 text-sm mb-1">
                <span className="flex items-center gap-2">
                  <span
                    className="w-3 h-3 rounded-full"
                    style={{ backgroundColor: entry.color }}
                  />
                  <span className="text-neutral-600">
                    {getExamTypeLabel(examType, t)} {subject === 'math' ? t('schoolCard.nvo.subjectMath') : t('schoolCard.nvo.subjectBulgarian')}:
                  </span>
                </span>
                <span className="font-medium text-neutral-900">{formatPercent(entry.value, 1)}%</span>
              </div>
            )
          })}
          {average != null && (
            <div className="flex items-center justify-between gap-4 text-sm mt-2 pt-2 border-t border-neutral-200">
              <span className="text-neutral-500">{t('academicPerformance.nationalAverage')}:</span>
              <span className="font-medium text-neutral-600">{formatPercent(average, 1)}%</span>
            </div>
          )}
        </div>
      )
    }
    return null
  }

  // Color scheme
  const colors = {
    math: {
      nvo_4: '#3b82f6',    // Blue
      nvo_7: '#2563eb',    // Darker blue
      nvo_10: '#1e40af',   // Even darker blue
    },
    bulgarian: {
      nvo_4: '#a855f7',    // Purple
      nvo_7: '#9333ea',    // Darker purple
      nvo_10: '#7e22ce',   // Even darker purple
    }
  }

  return (
    <div className="w-full">
      {/* Legend explanation */}
      <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
        <p className="text-sm text-blue-900">
          <span className="font-semibold">{t('schools.compareAllGradesInfo')}</span>
          {' '}
          <span className="text-blue-700">
            {t('schools.compareAllGradesHelp')}
          </span>
        </p>
      </div>

      <ResponsiveContainer width="100%" height={450}>
        <LineChart
          data={chartData}
          margin={{ top: 5, right: 30, left: 0, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="year"
            stroke="#6b7280"
            style={{ fontSize: '0.875rem' }}
          />
          <YAxis
            domain={[0, 100]}
            stroke="#6b7280"
            style={{ fontSize: '0.875rem' }}
            tickFormatter={(value) => `${value}%`}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: '0.75rem', paddingTop: '10px' }}
            iconType="line"
          />

          {/* Render lines for each grade × subject combination */}
          {availableExamTypes.map(examType => {
            const lineStyle = getLineStyleForGrade(examType)

            return (
              <React.Fragment key={examType}>
                {/* Math line */}
                {showMath && (
                  <Line
                    type="linear"
                    dataKey={`${examType}_math`}
                    stroke={colors.math[examType]}
                    strokeWidth={lineStyle.strokeWidth}
                    strokeDasharray={lineStyle.strokeDasharray}
                    name={`${getExamTypeLabel(examType, t)} - ${t('schoolCard.nvo.subjectMath')}`}
                    dot={{ fill: colors.math[examType], r: 3 }}
                    activeDot={{ r: 5 }}
                    opacity={lineStyle.opacity}
                    connectNulls
                    isAnimationActive={false}
                  />
                )}

                {/* Bulgarian line */}
                {showBulgarian && (
                  <Line
                    type="linear"
                    dataKey={`${examType}_bulgarian`}
                    stroke={colors.bulgarian[examType]}
                    strokeWidth={lineStyle.strokeWidth}
                    strokeDasharray={lineStyle.strokeDasharray}
                    name={`${getExamTypeLabel(examType, t)} - ${t('schoolCard.nvo.subjectBulgarian')}`}
                    dot={{ fill: colors.bulgarian[examType], r: 3 }}
                    activeDot={{ r: 5 }}
                    opacity={lineStyle.opacity}
                    connectNulls
                    isAnimationActive={false}
                  />
                )}
              </React.Fragment>
            )
          })}

          {/* Average (dashed reference line) */}
          {average != null && (
            <Line
              type="linear"
              dataKey="avg"
              stroke="#9ca3af"
              strokeWidth={1}
              strokeDasharray="5 5"
              name={t('academicPerformance.nationalAverage')}
              dot={false}
              isAnimationActive={false}
            />
          )}
        </LineChart>
      </ResponsiveContainer>

      {/* Legend helper */}
      <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
        {availableExamTypes.map(examType => {
          const lineStyle = getLineStyleForGrade(examType)
          return (
            <div key={examType} className="flex items-center gap-2 p-2 bg-neutral-50 rounded">
              <svg width="40" height="3" className="flex-shrink-0">
                <line
                  x1="0"
                  y1="1.5"
                  x2="40"
                  y2="1.5"
                  stroke="#6b7280"
                  strokeWidth={lineStyle.strokeWidth}
                  strokeDasharray={lineStyle.strokeDasharray}
                />
              </svg>
              <span className="text-neutral-700 font-medium">{getExamTypeLabel(examType, t)}</span>
            </div>
          )
        })}
      </div>

      {/* Data source note */}
      <div className="text-xs text-neutral-500 text-center mt-3">
        {t('academicPerformance.dataFrom')} {chartData[0]?.year} - {chartData[chartData.length - 1]?.year}
      </div>
    </div>
  )
}

export default MultiGradeComparisonChart
