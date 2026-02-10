import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { useTranslation } from 'react-i18next'

function NVOChart({ data }) {
  const { t } = useTranslation()

  if (!data || data.length === 0) {
    return <div className="text-gray-500 text-center py-8">No data available</div>
  }

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="year" />
          <YAxis domain={[0, 100]} />
          <Tooltip />
          <Legend />
          <Line
            type="monotone"
            dataKey="bulgarian"
            name={t('subjects.bulgarian') || 'Bulgarian'}
            stroke="#0ea5e9"
            strokeWidth={2}
          />
          <Line
            type="monotone"
            dataKey="math"
            name={t('subjects.math') || 'Math'}
            stroke="#8b5cf6"
            strokeWidth={2}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default NVOChart
