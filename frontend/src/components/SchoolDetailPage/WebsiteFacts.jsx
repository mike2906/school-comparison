import { useTranslation } from 'react-i18next'
import { factsReadDate, getSchoolFacts } from '../../utils/schoolFacts'

// "From the school's website": facts read from the school's own pages, each linking the
// page it came from. Nothing renders when the school has none.
function WebsiteFacts({ school }) {
  const { t, i18n } = useTranslation()
  const facts = getSchoolFacts(school, i18n.language)
  if (facts.length === 0) return null
  const readDate = factsReadDate(school)

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-1">{t('schoolFacts.title')}</h2>
      <p className="text-sm text-neutral-500 mb-5">
        {readDate ? t('schoolFacts.readOn', { date: readDate }) : t('schoolFacts.note')}
      </p>
      <ul className="space-y-3">
        {facts.map((fact, index) => (
          <li key={`${fact.topic}-${index}`} className="flex flex-col sm:flex-row sm:gap-4">
            <span className="sm:w-40 shrink-0 text-xs font-medium uppercase tracking-wide text-neutral-500 pt-1">
              {t(`schoolFacts.topics.${fact.topic}`)}
            </span>
            <span className="text-neutral-800">
              {fact.text}{' '}
              <a
                href={fact.sourceUrl}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="text-sm text-primary-700 hover:text-primary-800 underline underline-offset-2 whitespace-nowrap"
              >
                {t('schoolFacts.source')}
              </a>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default WebsiteFacts
