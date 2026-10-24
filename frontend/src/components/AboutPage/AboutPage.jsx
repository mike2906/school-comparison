import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import Layout, { CONTACT_EMAIL } from '../Layout/Layout'

const SOURCES = [
  { key: 'schools', href: null },
  { key: 'kindergartens', href: 'https://kg.sofia.bg', coverage: true },
  { key: 'nvo', href: 'https://data.egov.bg' },
  { key: 'dzi', href: 'https://data.egov.bg' },
  { key: 'prices', href: null },
  {
    key: 'addresses',
    href: 'https://urbandata.sofia.bg/dataset/address_sofia-zip',
    license: 'https://creativecommons.org/licenses/by/4.0/',
  },
  { key: 'maps', href: 'https://www.openstreetmap.org/copyright' },
]

const SOURCE_CODE = 'https://github.com/mike2906/school-comparison'

const PRIVACY = ['account', 'browser', 'calculator', 'lookups', 'searches', 'hosting', 'contact']

/** Where the data comes from and how to read it: the main trust signal for parents. */
function AboutPage() {
  const { t } = useTranslation()
  const { hash, key } = useLocation()

  // The page renders after the browser's own jump to the fragment, so repeat it. `key`
  // changes on every navigation, so following the same link again scrolls again.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash, key])

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
              {source.license && (
                <a
                  href={source.license}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 ml-4 inline-block text-sm text-primary-700 underline"
                >
                  {t('about.licenseLink')}
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

        <h2 className="mt-10 text-xl font-semibold text-neutral-900">{t('about.sourceCodeTitle')}</h2>
        <p className="mt-3 text-neutral-700">{t('about.sourceCodeBody')}</p>
        <a
          href={SOURCE_CODE}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-flex items-center gap-1.5 text-sm text-primary-700 underline"
        >
          {/* GitHub mark (Octicons, MIT) */}
          <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4 fill-current">
            <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
          </svg>
          {SOURCE_CODE.replace(/^https:\/\//, '')}
        </a>

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
