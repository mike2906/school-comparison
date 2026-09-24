import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../Layout/Layout'

/**
 * Unknown URLs land here instead of the router's developer page; `variant="error"` is the
 * route error screen (an unexpected crash is not a missing page).
 */
function NotFoundPage({ variant = 'notFound' }) {
  const { t } = useTranslation()
  const isError = variant === 'error'
  const title = isError ? t('routeError.title') : t('notFound.title')

  useEffect(() => {
    const previous = document.title
    document.title = `${title} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [t, title])

  return (
    <Layout>
      <div className="mx-auto max-w-xl px-4 py-16 text-center">
        {!isError && <p className="text-sm font-semibold text-primary-700">404</p>}
        <h1 className="mt-2 text-2xl font-bold text-neutral-900">{title}</h1>
        <p className="mt-3 text-neutral-600">{isError ? t('routeError.body') : t('notFound.body')}</p>
        <Link
          to="/search"
          className="mt-8 inline-flex h-11 items-center rounded-lg bg-primary-700 px-5 text-sm font-medium text-white hover:bg-primary-800"
        >
          {t('notFound.cta')}
        </Link>
      </div>
    </Layout>
  )
}

export default NotFoundPage
