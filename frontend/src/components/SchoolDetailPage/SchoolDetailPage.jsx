import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Layout from '../Layout/Layout'
import { fetchSchool } from '../../api/schools'
import { getSchoolName, getAddress, getSummary } from '../../utils/i18n'

function SchoolDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const [school, setSchool] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const formatAmount = (value) => {
    if (value == null || Number.isNaN(value)) return null
    return new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 0 }).format(value)
  }

  useEffect(() => {
    const loadSchool = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchSchool(id)
        setSchool(data)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    loadSchool()
  }, [id])

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-8 w-8 text-primary-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-lg text-neutral-600">{t('common.loading')}</span>
          </div>
        </div>
      </Layout>
    )
  }

  if (error || !school) {
    return (
      <Layout>
        <div className="max-w-4xl mx-auto px-6 py-12">
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-red-100 flex items-center justify-center">
              <svg className="w-8 h-8 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('schools.notFound')}</h2>
            <p className="text-neutral-600 mb-6">{error || t('schools.notFoundDesc')}</p>
            <button
              onClick={() => navigate(-1)}
              className="px-6 py-3 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
            >
              {t('common.goBack')}
            </button>
          </div>
        </div>
      </Layout>
    )
  }

  const summary = getSummary(school, i18n.language, 'long')
  const primaryLocation = school.locations?.find(l => l.is_primary) || school.locations?.[0]

  // Get school name based on language
  const schoolName = getSchoolName(school, i18n.language)

  return (
    <Layout>
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Back Button */}
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-2 text-sm text-neutral-600 hover:text-neutral-900 mb-6 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t('common.back')}
        </button>

        {/* Header */}
        <div className="bg-white rounded-2xl shadow-xl border border-neutral-200 p-8 mb-6">
          <h1 className="text-3xl font-bold text-neutral-900 mb-4">{schoolName}</h1>

          <div className="flex flex-wrap items-center gap-3 mb-6">
            <span className={`
              px-3 py-1 rounded-lg text-sm font-medium
              ${school.school_type === 'state'
                ? 'bg-primary-50 text-primary-700 border border-primary-200'
                : school.school_type === 'private'
                ? 'bg-violet-50 text-violet-700 border border-violet-200'
                : 'bg-blue-50 text-blue-700 border border-blue-200'
              }
            `}>
              {t(`schoolTypes.${school.school_type}`)}
            </span>
            <span className="px-3 py-1 rounded-lg text-sm bg-neutral-100 text-neutral-700">
              {t(`educationLevels.${school.education_level}`)}
            </span>
          </div>

          {summary && (
            <div className="prose max-w-none">
              <p className="text-neutral-700 leading-relaxed">{summary}</p>
            </div>
          )}

          {school.website_url && (
            <div className="mt-6">
              <a
                href={school.website_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 bg-primary-50 text-primary-700 rounded-lg hover:bg-primary-100 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
                {t('schools.website')}
              </a>
            </div>
          )}
        </div>

        {/* Locations */}
        {school.locations && school.locations.length > 0 && (
          <div className="bg-white rounded-2xl shadow-xl border border-neutral-200 p-8 mb-6">
            <h2 className="text-xl font-bold text-neutral-900 mb-6">{t('schools.locations')}</h2>
            <div className="space-y-6">
              {school.locations.map((location, idx) => (
                <div key={location.id || idx} className="border-l-4 border-primary-500 pl-6 py-2">
                  <div className="flex items-start justify-between gap-4 mb-2">
                    <h3 className="font-semibold text-neutral-900">
                      {t(`ageGroups.${location.age_group}`)}
                    </h3>
                    {location.is_primary && (
                      <span className="px-2 py-0.5 bg-primary-100 text-primary-700 text-xs font-medium rounded">
                        {t('schools.primaryLocation')}
                      </span>
                    )}
                  </div>
                  <div className="space-y-2 text-sm text-neutral-600">
                    <div className="flex items-start gap-2">
                      <svg className="w-4 h-4 text-neutral-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                      </svg>
                      <span>{getAddress(location, i18n.language)}</span>
                    </div>
                    {location.phone && (
                      <div className="flex items-center gap-2">
                        <svg className="w-4 h-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                        </svg>
                        <span>{location.phone}</span>
                      </div>
                    )}
                    {location.shift && (
                      <div className="flex items-center gap-2">
                        <svg className="w-4 h-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        <span>{t(`shifts.${location.shift}`)}</span>
                      </div>
                    )}
                    {location.has_organised_groups && (
                      <div className="flex items-center gap-2 text-primary-600">
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                        <span>{t('schools.organisedGroups')}</span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Pricing (if private school) */}
        {school.school_type === 'private' && school.pricing && school.pricing.length > 0 && (
          <div className="bg-white rounded-2xl shadow-xl border border-neutral-200 p-8">
            <h2 className="text-xl font-bold text-neutral-900 mb-6">{t('pricing.title')}</h2>
            <div className="grid gap-4">
              {school.pricing.map((price, idx) => (
                <div key={idx} className="flex items-center justify-between p-4 bg-neutral-50 rounded-lg">
                  <div>
                    <p className="font-medium text-neutral-900">
                      {t(`pricing.${price.category}`)}
                      {price.plan_name ? ` • ${price.plan_name}` : ''}
                      {price.academic_year ? ` • ${price.academic_year}` : ''}
                    </p>
                    {price.age_group && (
                      <p className="text-sm text-neutral-500">{t(`ageGroups.${price.age_group}`)}</p>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-neutral-900">
                      {price.amount_min != null || price.amount_max != null ? (
                        <>
                          {price.amount_min != null ? formatAmount(price.amount_min) : ''}
                          {price.amount_min != null && price.amount_max != null ? '–' : ''}
                          {price.amount_max != null ? formatAmount(price.amount_max) : ''}
                          {price.amount_min != null || price.amount_max != null ? ` ${price.currency || 'BGN'}` : ''}
                        </>
                      ) : price.amount != null ? (
                        <>
                          {formatAmount(price.amount)} {price.currency || 'BGN'}
                        </>
                      ) : (
                        t('pricing.priceOnRequest')
                      )}
                    </p>
                    <p className="text-sm text-neutral-500">{t(`pricing.${price.period}`)}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}

export default SchoolDetailPage
