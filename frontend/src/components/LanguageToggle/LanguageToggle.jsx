import { useTranslation } from 'react-i18next'
import { useCountry } from '../../context/CountryContext'

function LanguageToggle() {
  const { i18n } = useTranslation()
  const { config } = useCountry()

  const supportedLanguages = config?.supported_languages || ['bg', 'en']

  const toggleLanguage = () => {
    const currentLang = i18n.language.split('-')[0] // Handle 'en-US' -> 'en'
    const currentIndex = supportedLanguages.indexOf(currentLang)
    const nextIndex = (currentIndex + 1) % supportedLanguages.length
    const newLang = supportedLanguages[nextIndex]
    i18n.changeLanguage(newLang)
    localStorage.setItem('language', newLang)
  }

  const currentLang = i18n.language.split('-')[0]
  const currentIndex = supportedLanguages.indexOf(currentLang)
  const nextIndex = (currentIndex + 1) % supportedLanguages.length
  const nextLang = supportedLanguages[nextIndex]

  return (
    <button
      onClick={toggleLanguage}
      className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-neutral-600 hover:text-neutral-900 hover:bg-neutral-100 transition-colors"
    >
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
      </svg>
      <span>{nextLang.toUpperCase()}</span>
    </button>
  )
}

export default LanguageToggle
