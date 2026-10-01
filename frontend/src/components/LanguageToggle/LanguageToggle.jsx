import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'
import { useCountry } from '../../context/CountryContext'
import { URL_LANGUAGES, languagePath } from '../../utils/languageUrl'

function LanguageToggle({ compact = false }) {
  const { t, i18n } = useTranslation()
  const { config } = useCountry()
  // Route path (basename already stripped), so it can be re-prefixed for either language.
  const { pathname, search, hash } = useLocation()

  const supportedLanguages = (config?.supported_languages || ['bg', 'en'])
    .filter(lang => URL_LANGUAGES.includes(lang))
  const currentLang = i18n.language.split('-')[0] // Handle 'en-US' -> 'en'

  return (
    <div
      role="group"
      aria-label={t('nav.language')}
      className="inline-flex items-center rounded-lg bg-neutral-100 p-0.5"
    >
      {supportedLanguages.map(lang => {
        const isActive = lang === currentLang
        // A plain <a>, not a router <Link>: the other language lives under a different
        // basename, so switching is a full document navigation.
        return (
          <a
            key={lang}
            href={`${languagePath(pathname, lang)}${search}${hash}`}
            lang={lang}
            hrefLang={lang}
            aria-current={isActive ? 'true' : undefined}
            className={`${compact ? 'min-w-[40px] h-10 px-2' : 'min-w-[40px] h-9 px-3'} inline-flex items-center justify-center rounded-md text-xs font-semibold transition-colors ${
              isActive
                ? 'bg-white text-neutral-900 shadow-sm'
                : 'text-neutral-500 hover:text-neutral-900'
            }`}
          >
            {lang.toUpperCase()}
          </a>
        )
      })}
    </div>
  )
}

export default LanguageToggle
