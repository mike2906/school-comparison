import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { fetchSchools, peekSchools } from '../../api/schools'
import { loadDistricts } from '../../admission/loadCatchments'
import { useCountry } from '../../context/CountryContext'
import { districtGroups, districtListLine } from '../../utils/districtPages'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import Layout from '../Layout/Layout'
import NotFoundPage from '../NotFoundPage/NotFoundPage'

/**
 * `/districts`: every Sofia district with its count; `/districts/<slug>`: the listed
 * kindergartens and schools in one, each with its headline result. The prerender writes
 * the same lists (src/prerender/pages.js).
 */
function DistrictPage() {
  const { slug } = useParams()
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()
  const [schools, setSchools] = useState(() => peekSchools() ?? null)
  const [districts, setDistricts] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([fetchSchools(), loadDistricts()])
      .then(([schoolData, districtData]) => {
        if (!active) return
        setSchools(schoolData)
        setDistricts(districtData)
      })
      .catch(() => active && setFailed(true))
    return () => {
      active = false
    }
  }, [])

  const groups = useMemo(
    () => (schools && districts ? districtGroups(schools, districts, i18n.language) : null),
    [schools, districts, i18n.language],
  )
  const group = slug && groups ? groups.find((item) => item.slug === slug) : null
  const title = slug
    ? group && t('districtPages.pageTitle', { district: group.label })
    : t('districtPages.title')

  useEffect(() => {
    if (!title) return undefined
    const previous = document.title
    document.title = `${title} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [title, t])

  if (slug && groups && !group) return <NotFoundPage />

  return (
    <Layout>
      <div className="mx-auto max-w-3xl px-4 py-8 md:px-6 md:py-12">
        {slug && (
          <Link to="/districts" className="text-sm text-primary-700 hover:underline">
            {t('districtPages.allDistricts')}
          </Link>
        )}
        {title && <h1 className="mt-2 text-3xl font-bold text-neutral-900">{title}</h1>}
        {failed && <p className="mt-6 text-neutral-700">{t('common.error')}</p>}
        {!groups && !failed && <p className="mt-6 text-neutral-500">{t('common.loading')}</p>}
        {!slug && groups && (
          <>
            <p className="mt-3 text-neutral-700">{t('districtPages.intro')}</p>
            <ul className="mt-6 space-y-2">
              {groups.map((item) => (
                <li key={item.slug} className="text-sm">
                  <Link to={`/districts/${item.slug}`} className="font-medium text-primary-700 hover:underline">
                    {t('seo.district', { district: item.label })}
                  </Link>
                  <span className="text-neutral-500"> · {t('districtPages.count', { count: item.count })}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        {group && (
          <>
            <p className="mt-3 text-neutral-700">{t('districtPages.pageIntro', { district: group.label })}</p>
            {group.sections.map((section) => (
              <section key={section.key}>
                <h2 className="mt-10 text-xl font-semibold text-neutral-900">
                  {t(`directory.${section.key}`)} ({section.entries.length})
                </h2>
                {section.key === 'kindergartens' && section.entries.some(({ school }) => school.school_type === 'state') && (
                  <p className="mt-2 text-sm text-neutral-600">
                    {t('admissionArea.districtIntro', { district: t('seo.district', { district: group.label }) })}
                  </p>
                )}
                <ul className="mt-4 space-y-3">
                  {section.entries.map(({ school, name }) => {
                    const highlight = districtListLine(school, t, i18n.language, { districts, district: group.name })
                    return (
                      <li key={school.id} className="text-sm">
                        <Link to={`/schools/${school.id}`} className="font-medium text-primary-700 hover:underline">
                          {name}
                        </Link>
                        <span className="text-neutral-500">
                          {' · '}
                          {t('seo.schoolFacts', {
                            type: schoolTypeLabel(school, t),
                            level: schoolLevelLabel(school, t, countryConfig),
                          })}
                        </span>
                        {highlight && <div className="text-neutral-700">{highlight}</div>}
                      </li>
                    )
                  })}
                </ul>
              </section>
            ))}
          </>
        )}
      </div>
    </Layout>
  )
}

export default DistrictPage
