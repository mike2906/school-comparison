import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { atAGlanceLines, factsReadDate, getSchoolFacts } from '../../utils/schoolFacts'

// "From the school's website": what the facts' labels add up to (admission, curriculum,
// bus areas ...), then the facts read from the school's own pages, each linking the page
// it came from; the key facts first, the rest behind "show more". Nothing renders when
// the school has none.
function WebsiteFacts({ school }) {
  const { t, i18n } = useTranslation()
  const [showMore, setShowMore] = useState(false)
  const all = getSchoolFacts(school, i18n.language)
  if (all.length === 0) return null
  const key = all.filter(fact => fact.rank === 'key')
  const extra = all.length - key.length
  // A school whose facts are all "more" shows them all rather than an empty box.
  const facts = showMore || key.length === 0 ? all : key
  const glance = atAGlanceLines(school, i18n.language, t)
  const readDate = factsReadDate(school)

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-1">{t('schoolFacts.title')}</h2>
      <p className="text-sm text-neutral-500 mb-5">
        {readDate ? t('schoolFacts.readOn', { date: readDate }) : t('schoolFacts.note')}
      </p>
      {glance.length > 0 && (
        <dl className="mb-6 grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-x-4 gap-y-2 rounded-xl bg-neutral-50 p-4">
          {glance.map(line => (
            <div key={line.key} className="contents">
              <dt className="text-xs font-medium uppercase tracking-wide text-neutral-500 pt-1">
                {t(`schoolFacts.glance.${line.key}`)}
              </dt>
              <dd className="text-neutral-800">{line.value}</dd>
            </div>
          ))}
        </dl>
      )}
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
      {key.length > 0 && extra > 0 && (
        <button
          type="button"
          onClick={() => setShowMore(value => !value)}
          aria-expanded={showMore}
          className="mt-4 text-sm font-medium text-primary-700 hover:text-primary-800 underline underline-offset-2"
        >
          {showMore ? t('schoolFacts.showLess') : t('schoolFacts.showMore', { count: extra })}
        </button>
      )}
    </div>
  )
}

export default WebsiteFacts
