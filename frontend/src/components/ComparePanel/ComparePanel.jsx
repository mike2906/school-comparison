import { useTranslation } from 'react-i18next'

function ComparePanel({ schools, onRemove }) {
  const { t, i18n } = useTranslation()

  if (schools.length === 0) {
    return (
      <div className="p-4 text-center text-gray-500">
        {t('compare.empty')}
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b">
            <th className="p-2 text-left"></th>
            {schools.map(school => (
              <th key={school.id} className="p-2 text-left">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{school.name}</span>
                  <button
                    onClick={() => onRemove(school.id)}
                    className="text-red-500 hover:text-red-700 text-sm"
                  >
                    {t('compare.remove')}
                  </button>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="border-b">
            <td className="p-2 text-gray-600">{t('filters.schoolType')}</td>
            {schools.map(school => (
              <td key={school.id} className="p-2">
                {t(`schoolTypes.${school.school_type}`)}
              </td>
            ))}
          </tr>
          <tr className="border-b">
            <td className="p-2 text-gray-600">{t('schools.address')}</td>
            {schools.map(school => (
              <td key={school.id} className="p-2">
                {school.locations?.[0]?.address || '-'}
              </td>
            ))}
          </tr>
          <tr className="border-b">
            <td className="p-2 text-gray-600">{t('schools.shift')}</td>
            {schools.map(school => (
              <td key={school.id} className="p-2">
                {school.locations?.[0]?.shift
                  ? t(`shifts.${school.locations[0].shift}`)
                  : '-'}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export default ComparePanel
