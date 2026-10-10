import { useTranslation } from 'react-i18next'
import { displayPrice, groupPricingByAcademicYear, priceAgeGroupLabel, YEAR_STATUS } from '../../utils/pricing'
import { groupPricingByCategory, getSourceBadgeColor } from './helpers'
import { formatAmount } from '../../utils/format'

/** Every published price, grouped by academic year and then by category. */
function PricingSection({ pricing }) {
  const { t, i18n } = useTranslation()

  const formatNumber = (value) => formatAmount(value, i18n.language)

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-neutral-900">{t('pricing.title')}</h2>
      </div>

      {/* Grouped by academic year: one shared label across mixed years would
          mis-date every group but one. */}
      <div className="space-y-8">
        {groupPricingByAcademicYear(pricing).map(group => (
          <div key={group.key}>
            <div className="flex items-center gap-2 mb-4">
              <span
                className={`text-sm px-3 py-1 rounded-lg border ${
                  group.yearStatus === YEAR_STATUS.CURRENT
                    ? 'bg-teal-50 text-teal-700 border-teal-200'
                    : 'bg-neutral-100 text-neutral-600 border-neutral-300'
                }`}
              >
                {group.academicYear || t('pricing.yearNotStated')}
              </span>
              {group.yearStatus === YEAR_STATUS.DATED_OTHER && (
                <span className="text-xs text-neutral-500">
                  {t('pricing.notCurrentYear')}
                </span>
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
                    <div className="space-y-3">
                      {items.map((price, idx) => {
                        const { currency } = displayPrice(null, price.currency)
                        const formatShown = (value) => formatNumber(displayPrice(value, price.currency).value)
                        const amountText = price.amount_min != null || price.amount_max != null
                          ? `${price.amount_min != null ? formatShown(price.amount_min) : ''}${price.amount_min != null && price.amount_max != null ? '–' : ''}${price.amount_max != null ? formatShown(price.amount_max) : ''} ${currency}`
                          : price.amount != null
                          ? `${formatShown(price.amount)} ${currency}`
                          : t('pricing.priceOnRequest')

                        const ageGroupLabel = priceAgeGroupLabel(price, t)

                        return (
                          <div key={idx} className="flex items-center justify-between gap-3 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                            <div className="flex-1 min-w-0">
                              <div className="font-medium text-neutral-900 break-words">
                                {price.plan_name || t(`pricing.${price.category}`)}
                              </div>
                              {ageGroupLabel && (
                                <div className="text-sm text-neutral-500 break-words">{ageGroupLabel}</div>
                              )}
                            </div>
                            <div className="text-right flex flex-col-reverse items-end sm:flex-row sm:items-center gap-2 sm:gap-3">
                              <div>
                                <div className="text-lg font-bold text-neutral-900 whitespace-nowrap">{amountText}</div>
                                <div className="text-sm text-neutral-500">
                                  {price.period ? t(`pricing.${price.period}`) : t('pricing.periodNotStated')}
                                </div>
                              </div>
                              {price.source && (
                                <span className={`px-2 py-1 text-xs font-medium rounded border ${getSourceBadgeColor(price.source)}`}>
                                  {t(`priceSource.${price.source}`)}
                                </span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default PricingSection
