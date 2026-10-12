import { useTranslation } from 'react-i18next'
import { displayPrice, groupPricingByAcademicYear, groupRowsByAgeGroup, priceAgeGroupLabel, YEAR_STATUS } from '../../utils/pricing'
import { sharedPriceSource } from '../../utils/priceSources'
import { groupPricingByCategory } from './helpers'
import { formatAmount } from '../../utils/format'
import PriceSource from './PriceSource'
import { pricePeriod } from '../../utils/priceRows'

/**
 * Every published price, grouped by academic year, then by category, then by the age
 * group it is for. Each year says where its prices came from: once when they share one
 * page, otherwise on each row.
 */
function PricingSection({ pricing }) {
  const { t, i18n } = useTranslation()

  const formatNumber = (value) => formatAmount(value, i18n.language)

  const renderRow = (price, { showAgeGroup, showSource }) => {
    const { currency } = displayPrice(null, price.currency)
    const formatShown = (value) => formatNumber(displayPrice(value, price.currency).value)
    const amountText = price.amount_min != null || price.amount_max != null
      ? `${price.amount_min != null ? formatShown(price.amount_min) : ''}${price.amount_min != null && price.amount_max != null ? '–' : ''}${price.amount_max != null ? formatShown(price.amount_max) : ''} ${currency}`
      : price.amount != null
      ? `${formatShown(price.amount)} ${currency}`
      : t('pricing.priceOnRequest')

    const ageGroupLabel = showAgeGroup ? priceAgeGroupLabel(price, t) : null

    return (
      // Stacked on phones: beside the price, a long fee name was squeezed into a column a
      // few letters wide and broke mid-word.
      <div key={price.id} className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
        <div className="flex-1 min-w-0 space-y-1">
          <div className="font-medium text-neutral-900 break-words">
            {price.plan_name || t(`pricing.${price.category}`)}
          </div>
          {ageGroupLabel && (
            <div className="text-sm text-neutral-500 break-words">{ageGroupLabel}</div>
          )}
          {showSource && (
            <PriceSource source={price.source} url={price.source_url} checkedAt={price.scraped_at} />
          )}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2 sm:block sm:text-right sm:shrink-0">
          <div className="text-lg font-bold text-neutral-900 whitespace-nowrap">{amountText}</div>
          {/* A fee with no period stands out, so it is not read as the monthly fee beside it. */}
          <div className={`text-sm ${pricePeriod(price) ? 'text-neutral-500' : 'text-amber-700'}`}>
            {pricePeriod(price) ? t(`pricing.${pricePeriod(price)}`) : t('pricing.periodNotStated')}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-neutral-900">{t('pricing.title')}</h2>
      </div>

      {/* Grouped by academic year: one shared label across mixed years would
          mis-date every group but one. */}
      <div className="space-y-8">
        {groupPricingByAcademicYear(pricing).map(group => {
          const shared = sharedPriceSource(group.rows)
          return (
            <div key={group.key}>
              <div className="mb-4 space-y-2">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-sm px-3 py-1 rounded-lg border ${
                      group.yearStatus === YEAR_STATUS.CURRENT || group.yearStatus === YEAR_STATUS.NEXT
                        ? 'bg-teal-50 text-teal-700 border-teal-200'
                        : 'bg-neutral-100 text-neutral-600 border-neutral-300'
                    }`}
                  >
                    {group.academicYear || t('pricing.yearNotStated')}
                  </span>
                  {(group.yearStatus === YEAR_STATUS.DATED_OTHER || group.yearStatus === YEAR_STATUS.NEXT) && (
                    <span className="text-xs text-neutral-500">
                      {t(group.yearStatus === YEAR_STATUS.NEXT ? 'pricing.nextYear' : 'pricing.notCurrentYear')}
                    </span>
                  )}
                </div>
                {shared && (
                  <PriceSource source={shared.source} url={shared.url} checkedAt={shared.checkedAt} />
                )}
              </div>

              <div className="space-y-6">
                {Object.entries(groupPricingByCategory(group.rows))
                  .filter(([, items]) => items.length > 0)
                  .map(([category, items]) => (
                    <div key={category}>
                      <h3 className="text-sm font-semibold text-neutral-500 uppercase tracking-wide mb-3">
                        {t(`pricing.${category}`)}
                      </h3>
                      <div className="space-y-4">
                        {groupRowsByAgeGroup(items, t).map(band => (
                          <div key={band.label || ''} className="space-y-3">
                            {band.label && (
                              <h4 className="text-sm font-medium text-neutral-700 break-words">{band.label}</h4>
                            )}
                            {band.rows.map(price => renderRow(price, {
                              showAgeGroup: !band.label,
                              showSource: !shared,
                            }))}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default PricingSection
