import { useTranslation } from 'react-i18next'
import { formatCheckedDate, safeSourceUrl, sourceSiteName } from '../../utils/priceSources'
import { getSourceBadgeColor } from './helpers'

/**
 * Where a price came from, so a parent can check it: the kind of source, a link to the
 * page it was taken from (left out when none is stored) and the day we checked it.
 */
function PriceSource({ source, url, checkedAt }) {
  const { t, i18n } = useTranslation()
  const href = safeSourceUrl(url)
  const site = sourceSiteName(url)
  const date = formatCheckedDate(checkedAt, i18n.language)
  if (!source && !href && !date) return null

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-neutral-500">
      {source && (
        <span className={`px-1.5 py-0.5 font-medium rounded border ${getSourceBadgeColor(source)}`}>
          {t(`priceSource.${source}`)}
        </span>
      )}
      {href && (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-teal-700 hover:text-teal-800 underline break-all"
          aria-label={t('pricing.sourceLinkLabel', { site })}
        >
          {t('pricing.sourceLink', { site })}
          <span aria-hidden="true"> ↗</span>
        </a>
      )}
      {date && <span>{t('pricing.checkedOn', { date })}</span>}
    </div>
  )
}

export default PriceSource
