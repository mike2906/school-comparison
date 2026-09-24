import { useTranslation } from 'react-i18next'
import { useCountry } from '../../context/CountryContext'

function LanguageToggle({ compact = false }) {
  const { t, i18n } = useTranslation()
  const { config } = useCountry()

  const supportedLanguages = config?.supported_languages || ['bg', 'en']
  const currentLang = i18n.language.split('-')[0] // Handle 'en-US' -> 'en'

  const selectLanguage = (lang) => {
    if (lang === currentLang) return
    i18n.changeLanguage(lang)
    localStorage.setItem('language', lang)
  }

  return (
    <div
      role="group"
      aria-label={t('nav.language')}
      className="inline-flex items-center rounded-lg bg-neutral-100 p-0.5"
    >
      {supportedLanguages.map(lang => {
        const isActive = lang === currentLang
        return (
          <button
            key={lang}
            type="button"
            lang={lang}
            onClick={() => selectLanguage(lang)}
            aria-pressed={isActive}
            className={`${compact ? 'min-w-[36px] h-9 px-2' : 'min-w-[40px] h-9 px-3'} rounded-md text-xs font-semibold transition-colors ${
              isActive
                ? 'bg-white text-neutral-900 shadow-sm'
                : 'text-neutral-500 hover:text-neutral-900'
            }`}
          >
            {lang.toUpperCase()}
          </button>
        )
      })}
    </div>
  )
}

export default LanguageToggle
