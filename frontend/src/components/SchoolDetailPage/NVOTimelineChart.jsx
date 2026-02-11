import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { useTranslation } from 'react-i18next'
import { formatPercent, getPerformanceStyle } from './helpers'

/**
 * NVOTimelineChart - Interactive line chart showing NVO exam results over time
 *
 * Props:
 * - examType: 'nvo_4' | 'nvo_7' | 'nvo_10'
 * - data: Array of { year, math, bulgarian, nationalAvg }
 * - latestYear: number (for display)
 */
function NVOTimelineChart({ examType, data, latestYear }) {
  const { t } = useTranslation()

  if (!data || data.length === 0) {
    return null
  }

  // Custom tooltip to show detailed info on hover
  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-white p-3 rounded-lg shadow-lg border border-neutral-200">
          <p className="font-semibold text-neutral-900 mb-2">{label}</p>
          {payload.map((entry, index) => (
            <div key={index} className="flex items-center justify-between gap-4 text-sm">
              <span className="flex items-center gap-2">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: entry.color }}
                />
                {entry.name}:
              </span>
              <span className="font-medium">{formatPercent(entry.value, 1)}%</span>
            </div>
          ))}
        </div>
      )
    }
    return null
  }

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={300}>
        <LineChart
          data={data}
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
            wrapperStyle={{ fontSize: '0.875rem' }}
            iconType="line"
          />

          {/* Math line */}
          <Line
            type="monotone"
            dataKey="math"
            stroke="#10b981"
            strokeWidth={2}
            name={t('schoolCard.nvo.subjectMath')}
            dot={{ fill: '#10b981', r: 4 }}
            activeDot={{ r: 6 }}
          />

          {/* Bulgarian line */}
          <Line
            type="monotone"
            dataKey="bulgarian"
            stroke="#2563eb"
            strokeWidth={2}
            name={t('schoolCard.nvo.subjectBulgarian')}
            dot={{ fill: '#2563eb', r: 4 }}
            activeDot={{ r: 6 }}
          />

          {/* National average (dashed reference line) */}
          <Line
            type="monotone"
            dataKey="nationalAvg"
            stroke="#9ca3af"
            strokeWidth={1}
            strokeDasharray="5 5"
            name={t('academicPerformance.nationalAvg')}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>

      {/* Data source note */}
      <div className="text-xs text-neutral-500 text-center mt-2">
        {t('academicPerformance.dataFrom')} {data[0]?.year} - {data[data.length - 1]?.year}
      </div>
    </div>
  )
}

export default NVOTimelineChart
