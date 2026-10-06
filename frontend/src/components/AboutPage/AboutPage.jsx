import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import Layout, { CONTACT_EMAIL } from '../Layout/Layout'

const SOURCES = [
  { key: 'schools', href: null },
  { key: 'kindergartens', href: 'https://kg.sofia.bg', coverage: true },
  { key: 'nvo', href: 'https://data.egov.bg' },
  { key: 'prices', href: null },
  { key: 'maps', href: 'https://www.openstreetmap.org/copyright' },
]

const PRIVACY = ['account', 'browser', 'lookups', 'hosting', 'contact']

/** Where the data comes from and how to read it: the main trust signal for parents. */
function AboutPage() {
  const { t } = useTranslation()
  const { hash } = useLocation()

  // The page renders after the browser's own jump to the fragment, so repeat it.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash])

  useEffect(() => {
    const previous = document.title
    document.title = `${t('about.title')} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [t])

  return (
    <Layout>
      <div className="mx-auto max-w-3xl px-4 py-8 md:px-6 md:py-12">
        <h1 className="text-3xl font-bold text-neutral-900">{t('about.title')}</h1>
        <p className="mt-3 text-neutral-700">{t('about.intro')}</p>

        <h2 className="mt-10 text-xl font-semibold text-neutral-900">{t('about.sourcesTitle')}</h2>
        <ul className="mt-4 space-y-4">
          {SOURCES.map(source => (
            <li key={source.key} className="rounded-xl border border-neutral-200 bg-white p-4">
              <h3 className="font-semibold text-neutral-900">{t(`about.sources.${source.key}.title`)}</h3>
              <p className="mt-1 text-sm text-neutral-700">{t(`about.sources.${source.key}.body`)}</p>
              {source.coverage && (
                <p className="mt-2 text-sm text-neutral-700">{t(`about.sources.${source.key}.coverage`)}</p>
              )}
              {source.href && (
                <a
                  href={source.href}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block text-sm text-primary-700 underline"
                >
                  {source.href.replace(/^https:\/\//, '')}
                </a>
              )}
            </li>
          ))}
        </ul>

        <h2 className="mt-10 text-xl font-semibold text-neutral-900">{t('about.ageTitle')}</h2>
        <p className="mt-3 text-neutral-700">{t('about.ageBody')}</p>

        <h2 className="mt-10 text-xl font-semibold text-neutral-900">{t('about.limitsTitle')}</h2>
        <p className="mt-3 text-neutral-700">{t('about.limitsBody')}</p>

        <h2 id="privacy" className="mt-10 scroll-mt-20 text-xl font-semibold text-neutral-900">
          {t('about.privacyTitle')}
        </h2>
        {PRIVACY.map(key => (
          <p key={key} className="mt-3 text-neutral-700">
            {t(`about.privacy.${key}`, { email: CONTACT_EMAIL })}
          </p>
        ))}

        <Link
          to="/search"
          className="mt-10 inline-flex h-11 items-center rounded-lg bg-primary-700 px-5 text-sm font-medium text-white hover:bg-primary-800"
        >
          {t('about.cta')}
        </Link>
      </div>
    </Layout>
  )
}

export default AboutPage
