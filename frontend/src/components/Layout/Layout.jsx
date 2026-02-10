import { useTranslation } from 'react-i18next'
import LanguageToggle from '../LanguageToggle/LanguageToggle'

function Layout({ children }) {
  const { t } = useTranslation()

  return (
    <div className="min-h-screen bg-neutral-100">
      {/* Navigation */}
      <nav className="bg-white border-b border-neutral-200 sticky top-0 z-50">
        <div className="px-6">
          <div className="flex justify-between items-center h-16">
            {/* Logo */}
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm">
                <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <div>
                <h1 className="text-lg font-semibold text-neutral-900 leading-tight">
                  {t('nav.title')}
                </h1>
                <p className="text-xs text-neutral-500 leading-tight">
                  {t('nav.subtitle')}
                </p>
              </div>
            </div>

            {/* Right side */}
            <div className="flex items-center gap-2">
              <LanguageToggle />
            </div>
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main>{children}</main>
    </div>
  )
}

export default Layout
