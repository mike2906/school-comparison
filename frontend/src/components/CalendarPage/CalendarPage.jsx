import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import Layout from '../Layout/Layout'
import { RULE_SETS } from '../../admission/ruleSets'
import { formatRankingDay, sofiaToday } from '../../admission/calculator'
import { calendarBirthYears, childCalendar, DEADLINES, feedPath, upcoming } from '../../admission/calendar'
import { milestoneLabel, stepTitle } from '../../admission/ics'
import { childBirthYear, getSaved, saveChild, subscribeSaved } from '../../admission/saved'
import { intlLocale } from '../../utils/format'
import { languagePath } from '../../utils/languageUrl'
import { Field, INPUT_CLASS } from '../AdmissionPage/fields'

function useDaySpan(language) {
  return useMemo(() => {
    const locale = intlLocale(language)
    const day = new Intl.DateTimeFormat(locale, { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
    const asDate = iso => new Date(`${iso}T00:00:00Z`)
    return m => (m.end ? day.formatRange(asDate(m.date), asDate(m.end)) : day.format(asDate(m.date)))
  }, [language])
}

function Subscribe({ birthYear, language }) {
  const { t } = useTranslation()
  const feedUrl = `${window.location.origin}${languagePath(feedPath(birthYear), language)}`
  const webcal = feedUrl.replace(/^https?:/, 'webcal:')
  const google = `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(webcal)}`
  const button = 'inline-flex items-center rounded-lg px-4 py-2 text-sm font-medium'
  return (
    <section className="mt-6 rounded-2xl border border-neutral-200 bg-white p-5 md:p-6">
      <h2 className="text-lg font-semibold text-neutral-900">{t('calendar.subscribeTitle')}</h2>
      <div className="mt-3 flex flex-wrap gap-2">
        <a href={google} target="_blank" rel="noopener noreferrer" className={`${button} bg-primary-700 text-white hover:bg-primary-800`}>
          {t('calendar.google')}
        </a>
        <a href={webcal} className={`${button} bg-primary-700 text-white hover:bg-primary-800`}>
          {t('calendar.apple')}
        </a>
        <a href={feedUrl} download className={`${button} border border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50`}>
          {t('calendar.download')}
        </a>
      </div>
      <p className="mt-3 text-sm text-neutral-600">{t('calendar.subscribeNote')}</p>
    </section>
  )
}

function Step({ entry, today }) {
  const { t, i18n } = useTranslation()
  const span = useDaySpan(i18n.language)
  const id = entry.step.id
  return (
    <section className="rounded-2xl border border-neutral-200 bg-white p-5 md:p-6" data-step={id}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-neutral-900">{stepTitle(t, entry)}</h2>
        {entry.step.optional && (
          <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700">{t('calendar.optional')}</span>
        )}
        <span className={`rounded-full px-2 py-0.5 text-xs ${entry.estimated ? 'bg-amber-50 text-amber-800' : 'bg-green-50 text-green-800'}`}>
          {t(entry.estimated ? 'calendar.estimatedBadge' : 'calendar.officialBadge')}
        </span>
      </div>
      <p className="mt-1 text-sm text-neutral-600">{t(`calendar.stepNotes.${id}`)}</p>
      {entry.estimated && (
        <p className="mt-1 text-sm text-amber-800">
          {entry.scheduleDue
            ? t('calendar.estimatedUntil', { date: formatRankingDay(entry.scheduleDue, i18n.language) })
            : t('calendar.estimated')}
        </p>
      )}
      {entry.rulesStatus !== 'verified' && <p className="mt-1 text-sm text-neutral-600">{t('calendar.rulesProvisional')}</p>}
      <ol className="mt-4 space-y-2">
        {upcoming(entry, today).map(milestone => {
          const deadline = DEADLINES.has(milestone.key)
          return (
            <li key={`${milestone.key}-${milestone.round ?? ''}`} className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
              <span className="text-sm text-neutral-500 sm:w-64 sm:flex-shrink-0">{span(milestone)}</span>
              <span className={`text-sm ${deadline ? 'font-semibold text-neutral-900' : 'text-neutral-800'}`}>
                {milestoneLabel(t, entry.step, milestone)}
              </span>
            </li>
          )
        })}
      </ol>
      <p className="mt-4 text-sm">
        <Link to={`/admission?stage=${entry.step.ruleStage}`} className="font-medium text-primary-700 underline">
          {t('calendar.calculatorLink')}
        </Link>
      </p>
    </section>
  )
}

function CalendarPage() {
  const { t, i18n } = useTranslation()
  const [params, setParams] = useSearchParams()
  const saved = useSyncExternalStore(subscribeSaved, getSaved, getSaved)
  const today = sofiaToday()
  const years = useMemo(() => calendarBirthYears(RULE_SETS, today), [today])
  const savedYear = childBirthYear(saved)
  const born = Number(params.get('born'))
  const fromUrl = Number.isInteger(born) && born > 1900 ? born : null
  const birthYear = fromUrl ?? savedYear
  const calendar = useMemo(() => (birthYear ? childCalendar(RULE_SETS, birthYear, today) : []), [birthYear, today])

  useEffect(() => {
    const previous = document.title
    document.title = `${t('calendar.title')} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [t])

  const choose = year => {
    const next = new URLSearchParams(params)
    if (year) next.set('born', String(year))
    else next.delete('born')
    setParams(next, { replace: true })
  }

  return (
    <Layout>
      <div className="mx-auto max-w-5xl px-4 py-8 md:px-6 md:py-10">
        <h1 className="text-3xl font-bold text-neutral-900">{t('calendar.title')}</h1>
        <p className="mt-2 max-w-3xl text-neutral-700">{t('calendar.intro')}</p>

        <div className="mt-6">
          <Field label={t('calendar.birthYear')} hint={savedYear && birthYear === savedYear ? t('calendar.fromSaved') : null}>
            {id => (
              <select
                id={id}
                value={birthYear ?? ''}
                onChange={event => choose(Number(event.target.value) || null)}
                className={INPUT_CLASS}
              >
                <option value="">{t('calendar.pickYear')}</option>
                {birthYear && !years.includes(birthYear) && <option value={birthYear}>{birthYear}</option>}
                {years.map(year => (
                  <option key={year} value={year}>{year}</option>
                ))}
              </select>
            )}
          </Field>
          {birthYear && birthYear !== savedYear && (
            <button
              type="button"
              onClick={() => saveChild({ birthYear })}
              className="mt-2 text-sm font-medium text-primary-700 underline"
            >
              {t('calendar.saveYear')}
            </button>
          )}
        </div>

        {birthYear && calendar.length === 0 && (
          <p className="mt-6 rounded-lg bg-neutral-50 p-4 text-sm text-neutral-700">{t('calendar.empty', { year: birthYear })}</p>
        )}
        {calendar.length > 0 && (
          <>
            {/* The build writes a feed for each year the picker offers, and only those. */}
            {years.includes(birthYear) && <Subscribe birthYear={birthYear} language={i18n.language} />}
            <div className="mt-6 space-y-4">
              {calendar.map(entry => (
                <Step key={`${entry.step.id}-${entry.startYear}`} entry={entry} today={today} />
              ))}
            </div>
          </>
        )}
      </div>
    </Layout>
  )
}

export default CalendarPage
