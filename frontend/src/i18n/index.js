import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { languageFromPath } from '../utils/languageUrl.js'

import bg from './bg.json'
import en from './en.json'

// Note: 'en' uses British English spelling (enrolment, organised, etc.)
const resources = {
  bg: { translation: bg },
  en: { translation: en },
}

// The URL decides the language (Bulgarian at `/`, English under `/en/`); there is no
// browser detection and no saved preference.
i18n
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'bg',
    supportedLngs: ['bg', 'en'],
    // One namespace; scraped text used as a key may contain ':' ("Clubs: English").
    nsSeparator: false,
    lng: languageFromPath(window.location.pathname),
    interpolation: {
      escapeValue: false,
    },
    react: {
      useSuspense: false,
    },
  })

export default i18n
