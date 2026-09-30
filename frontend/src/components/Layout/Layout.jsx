import { useTranslation } from 'react-i18next'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { useCompare } from '../../context/CompareContext'
import LanguageToggle from '../LanguageToggle/LanguageToggle'

const CONTACT_EMAIL = 'contact@schooldecider.com'

function Footer({ clearCompareBar }) {
  const { t } = useTranslation()
  const reportHref = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(t('footer.reportSubject'))}`

  return (
    // The fixed CompareBar would otherwise cover the footer links.
    <footer className={`border-t border-neutral-200 bg-white ${clearCompareBar ? 'pb-40' : ''}`}>
      <div className="mx-auto flex max-w-5xl flex-col gap-2 px-6 py-6 text-sm text-neutral-600 sm:flex-row sm:items-center sm:justify-between">
        <p>{t('footer.dataNote')}</p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Link to="/about" className="text-primary-700 underline">
            {t('nav.about')}
          </Link>
          <a href={reportHref} className="text-primary-700 underline">
            {t('footer.reportError')}
          </a>
        </div>
      </div>
    </footer>
  )
}

function Layout({ children, hideNavOnMobile = false, compactNavOnMobile = false, hideFooter = false }) {
  const { t } = useTranslation()
  const { compareList } = useCompare()
  const { pathname } = useLocation()
  // Same condition CompareBar uses to show itself.
  const compareBarVisible = compareList.length > 0 && !pathname.startsWith('/compare')

  return (
    <div className="flex min-h-screen flex-col bg-neutral-100">
      {/* Navigation */}
      <nav className={`bg-white border-b border-neutral-200 sticky top-0 z-50 ${
        hideNavOnMobile ? 'hidden lg:block' : ''
      }`}>
        <div className={`${compactNavOnMobile ? 'px-4 md:px-6' : 'px-6'}`}>
          <div className={`flex justify-between items-center ${compactNavOnMobile ? 'h-12 md:h-16' : 'h-16'}`}>
            {/* Logo */}
            <Link
              to="/search"
              aria-label={t('nav.home')}
              className={`flex items-center gap-3 rounded-lg ${compactNavOnMobile ? 'hidden md:flex' : ''}`}
            >
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm">
                <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <div>
                <div className="text-lg font-semibold text-neutral-900 leading-tight">
                  {t('nav.title')}
                </div>
                <p className="hidden sm:block text-xs text-neutral-500 leading-tight">
                  {t('nav.subtitle')}
                </p>
              </div>
            </Link>

            {/* Right side */}
            <div className="flex items-center gap-1 md:gap-2">
              {compareList.length >= 2 && (
                <NavLink
                  to="/compare"
                  className={({ isActive }) => `hidden sm:inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium ${
                    isActive ? 'bg-primary-50 text-primary-800' : 'text-neutral-700 hover:bg-neutral-100'
                  }`}
                >
                  {t('nav.compare')}
                  <span className="rounded-full bg-primary-100 px-1.5 text-xs text-primary-700">{compareList.length}</span>
                </NavLink>
              )}
              <NavLink
                to="/about"
                className={({ isActive }) => `inline-flex h-9 items-center whitespace-nowrap rounded-lg px-2 sm:px-3 text-sm font-medium ${
                  isActive ? 'bg-primary-50 text-primary-800' : 'text-neutral-700 hover:bg-neutral-100'
                }`}
              >
                {t('nav.about')}
              </NavLink>
              <LanguageToggle />
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1">{children}</main>
      {!hideFooter && <Footer clearCompareBar={compareBarVisible} />}
    </div>
  )
}

export default Layout
