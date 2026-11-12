import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { fetchSchools, peekSchools } from '../../api/schools'
import { useCountry } from '../../context/CountryContext'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { directorySections } from '../../utils/schoolDirectory'
import Layout from '../Layout/Layout'

/**
 * Every listed school by name, so parents (and crawlers) can reach any school page. Thin
 * pages are listed too: they are `noindex`, but a parent may still want them.
 */
function DirectoryPage() {
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()
  // Coming from the search page, the list is usually loaded already.
  const [schools, setSchools] = useState(() => peekSchools() ?? null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    fetchSchools()
      .then((data) => active && setSchools(data))
      .catch(() => active && setFailed(true))
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    const previous = document.title
    document.title = `${t('directory.title')} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [t])

  const sections = schools ? directorySections(schools, i18n.language) : []

  return (
    <Layout>
      <div className="mx-auto max-w-3xl px-4 py-8 md:px-6 md:py-12">
        <h1 className="text-3xl font-bold text-neutral-900">{t('directory.title')}</h1>
        <p className="mt-3 text-neutral-700">{t('directory.intro')}</p>
        <p className="mt-3">
          <Link to="/districts" className="text-sm font-medium text-primary-700 hover:underline">{t('districtPages.title')}</Link>
        </p>
        {failed && <p className="mt-6 text-neutral-700">{t('common.error')}</p>}
        {!schools && !failed && <p className="mt-6 text-neutral-500">{t('common.loading')}</p>}
        {sections.map((section) => (
          <section key={section.key}>
            <h2 className="mt-10 text-xl font-semibold text-neutral-900">
              {t(`directory.${section.key}`)} ({section.entries.length})
            </h2>
            <ul className="mt-4 space-y-2">
              {section.entries.map(({ school, name }) => (
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
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Layout>
  )
}

export default DirectoryPage
