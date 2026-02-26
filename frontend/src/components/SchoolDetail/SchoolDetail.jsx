import { useTranslation } from 'react-i18next'
import { getAddress, getSchoolName, getSummary } from '../../utils/i18n'

function SchoolDetail({ school, onClose }) {
  const { t, i18n } = useTranslation()

  if (!school) return null

  const summary = getSummary(school, i18n.language, 'long')
  const schoolName = getSchoolName(school, i18n.language)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto m-4">
        <div className="p-6">
          <div className="flex justify-between items-start">
            <h2 className="text-xl font-bold text-gray-900">{schoolName}</h2>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600"
            >
              {t('common.close')}
            </button>
          </div>

          <div className="mt-2 flex items-center gap-2">
            <span className={`text-xs px-2 py-0.5 rounded ${
              school.school_type === 'state'
                ? 'bg-green-100 text-green-700'
                : school.school_type === 'private'
                ? 'bg-purple-100 text-purple-700'
                : 'bg-blue-100 text-blue-700'
            }`}>
              {t(`schoolTypes.${school.school_type}`)}
            </span>
            <span className="text-sm text-gray-500">
              {t(`educationLevels.${school.education_level}`)}
            </span>
          </div>

          {summary && (
            <p className="mt-4 text-gray-700">{summary}</p>
          )}

          {school.website_url && (
            <a
              href={school.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-4 inline-block text-primary-600 hover:text-primary-700"
            >
              {t('schools.website')}
            </a>
          )}

          <div className="mt-6">
            <h3 className="font-semibold text-gray-900 mb-2">Locations</h3>
            {school.locations?.map(location => (
              <div key={location.id} className="border-t py-3">
                <p className="text-sm font-medium text-gray-900">
                  {(Array.isArray(location.age_groups) ? location.age_groups : [location.age_group])
                    .filter(Boolean)
                    .map(group => t(`ageGroups.${group}`))
                    .join(', ')}
                </p>
                <p className="text-sm text-gray-600">{getAddress(location, i18n.language)}</p>
                {location.phone && (
                  <p className="text-sm text-gray-500">{location.phone}</p>
                )}
                {(location.age_group_shifts || []).map(item => (
                  <p key={item.age_group} className="text-sm text-gray-500">
                    {t(`ageGroups.${item.age_group}`)}
                    {item.shift ? ` • ${t(`shifts.${item.shift}`)}` : ''}
                    {item.has_organised_groups ? ` • ${t('schools.organisedGroups')}` : ''}
                  </p>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default SchoolDetail
