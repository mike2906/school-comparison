import { useTranslation } from 'react-i18next'

/**
 * "Only with a free place today": shown when the search has a birth year in the school
 * year now running and today's ИСОДЗ snapshot covers it (docs/FREE_PLACES.md). `count` is
 * how many of the listed schools have a place for the child.
 */
function FreePlacesToggle({ active, count, birthYear, onChange }) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onChange(!active)}
      data-testid="free-places-filter"
      className={`inline-flex h-11 md:h-8 items-center gap-2 rounded-full border px-3 text-sm font-medium transition-colors ${
        active
          ? 'border-teal-600 bg-teal-700 text-white'
          : 'border-neutral-300 bg-white text-neutral-700 hover:border-neutral-400'
      }`}
    >
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${active ? 'bg-white' : 'bg-teal-600'}`} />
      {t('freePlaces.filter', { count, year: birthYear })}
    </button>
  )
}

export default FreePlacesToggle
