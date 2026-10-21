import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { displayPrice, groupPricingByAcademicYear, groupRowsByAgeGroup, isSingleInstalment, priceAgeGroupLabel, YEAR_STATUS } from '../../utils/pricing'
import { sharedPriceSource, sourceSiteName } from '../../utils/priceSources'
import { groupPricingByCategory } from './helpers'
import { formatAmount } from '../../utils/format'
import PriceSource from './PriceSource'
import { priceCategory, pricePeriod, periodUnknown, rowAgeGroupKeys, splitRowsForAgeGroup } from '../../utils/priceRows'
import { planNameBeside, textLang } from '../../utils/priceLabels'
import { paymentOptionTexts } from '../../utils/paymentOptions'
import { feeDiscountLines } from '../../utils/feeDiscounts'

/**
 * Every published price, grouped by academic year, then by category, then by the age
 * group it is for. Each year says where its prices came from: once when they share one
 * page, otherwise on each row.
 * With the parent's `ageGroup` (from the search they came from), fees that name only other
 * ages fold under "Fees for other ages"; fees naming no age stay in view, since they may
 * apply. With the `targetYear` the group was worked out for, the line above the fees says
 * so: a child born in 2022 is in the third group in 2027/28, but in the second in a
 * 2026/27 fee table.
 */
function PricingSection({ pricing, discounts, ageGroup = null, targetYear = null }) {
  const { t, i18n } = useTranslation()
  const { forGroup, otherAges, namesGroup, otherCount } = useMemo(() => {
    const split = splitRowsForAgeGroup(pricing, ageGroup)
    return {
      ...split,
      // Fees naming no age are no fee *for* the age: with only those, the school names none.
      namesGroup: split.forGroup.some(row => rowAgeGroupKeys(row).has(ageGroup)),
      // As many as the fold shows: a past year's fee repeated later is left out there too.
      otherCount: groupPricingByAcademicYear(split.otherAges).reduce((sum, group) => sum + group.rows.length, 0),
    }
  }, [pricing, ageGroup])
  // Mid-sentence: "fees for grades 1-4", "таксите за първа група".
  const groupLabel = otherAges.length > 0 ? t(`ageGroups.${ageGroup}`) : null
  const groupName = groupLabel && groupLabel.charAt(0).toLocaleLowerCase(i18n.language) + groupLabel.slice(1)

  const discountLines = feeDiscountLines(discounts, i18n.language)

  const formatNumber = (value) => formatAmount(value, i18n.language)

  const renderRow = (price, { showAgeGroup, showSource, bandLabel }) => {
    const { currency } = displayPrice(null, price.currency)
    const formatShown = (value) => formatNumber(displayPrice(value, price.currency).value)
    const amountText = price.amount_min != null || price.amount_max != null
      ? `${price.amount_min != null ? formatShown(price.amount_min) : ''}${price.amount_min != null && price.amount_max != null ? '–' : ''}${price.amount_max != null ? formatShown(price.amount_max) : ''} ${currency}`
      : price.amount != null
      ? `${formatShown(price.amount)} ${currency}`
      : t('pricing.priceOnRequest')

    const ageGroupLabel = showAgeGroup ? priceAgeGroupLabel(price, t, i18n.language) : null
    const options = paymentOptionTexts(price, (value) => `${formatNumber(value)} ${currency}`, t)
    const planName = planNameBeside(price, t, bandLabel || ageGroupLabel, i18n.language)

    return (
      // Stacked on phones: beside the price, a long fee name was squeezed into a column a
      // few letters wide and broke mid-word.
      <div key={price.id} className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
        <div className="flex-1 min-w-0 space-y-1">
          <div className="font-medium text-neutral-900 break-words" lang={textLang(planName, i18n.language)}>
            {planName || t(`pricing.${priceCategory(price)}`)}
          </div>
          {ageGroupLabel && (
            <div className="text-sm text-neutral-500 break-words" lang={textLang(ageGroupLabel, i18n.language)}>{ageGroupLabel}</div>
          )}
          {/* The same fee paid in parts: under it, so a plan is not read as a fee of its own. */}
          {options.length > 0 && (
            <div className="text-sm text-neutral-600 break-words">
              {t('pricing.payInParts')} {options.join(' · ')}
            </div>
          )}
          {showSource && (
            <PriceSource source={price.source} url={price.source_url} checkedAt={price.scraped_at} />
          )}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2 sm:block sm:text-right sm:shrink-0">
          <div className="text-lg font-bold text-neutral-900 whitespace-nowrap">{amountText}</div>
          {/* One instalment of a fee says so, never "per year" (300's "вноска I, до 31.08.26").
              A fee with no period stands out, so it is not read as the monthly fee beside it. */}
          {isSingleInstalment(price) ? (
            <div className="text-sm text-neutral-500">{t('pricing.oneInstalment')}</div>
          ) : pricePeriod(price) ? (
            <div className="text-sm text-neutral-500">{t(`pricing.${pricePeriod(price)}`)}</div>
          ) : periodUnknown(price) && (
            <div className="text-sm text-amber-700">{t('pricing.periodNotStated')}</div>
          )}
        </div>
      </div>
    )
  }

  // Grouped by academic year: one shared label across mixed years would mis-date every
  // group but one.
  const renderYears = (rows) => (
    <div className="space-y-8">
      {groupPricingByAcademicYear(rows).map(group => {
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
                      {groupRowsByAgeGroup(items, t, i18n.language).map(band => (
                        <div key={band.label || ''} className="space-y-3">
                          {band.label && (
                            <h4 className="text-sm font-medium text-neutral-700 break-words" lang={textLang(band.label, i18n.language)}>{band.label}</h4>
                          )}
                          {band.rows.map(price => renderRow(price, {
                            showAgeGroup: !band.label,
                            bandLabel: band.label,
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
  )

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-neutral-900">{t('pricing.title')}</h2>
      </div>

      {/* Extras with no main fee beside them read as the fee itself (532's swimming lessons). */}
      {pricing.length > 0 && !pricing.some(row => row.category === 'tuition') && (
        <p className="mb-6 text-sm text-amber-700">{t('pricing.noMainFee')}</p>
      )}
      {groupName && (
        <p className="mb-6 text-sm text-neutral-600">
          {namesGroup && targetYear
            ? t('pricing.forAgeGroupInYear', { group: groupName, year: `${targetYear}/${targetYear + 1}` })
            : t(namesGroup ? 'pricing.forAgeGroup' : 'pricing.noneForAgeGroup', { group: groupName })}
        </p>
      )}
      {forGroup.length > 0 && renderYears(forGroup)}
      {groupName && (
        <details className="mt-8" open={!namesGroup}>
          <summary className="cursor-pointer select-none text-sm font-semibold text-primary-700 hover:text-primary-800">
            {t('pricing.otherAges', { count: otherCount })}
          </summary>
          <div className="mt-6">{renderYears(otherAges)}</div>
        </details>
      )}

      {discountLines.length > 0 && (
        <div className="mt-8">
          <h3 className="text-sm font-semibold text-neutral-500 uppercase tracking-wide mb-3">{t('pricing.discounts')}</h3>
          <ul className="space-y-2">
            {discountLines.map(line => (
              <li key={line.key} className="text-neutral-900 break-words">
                <span lang={textLang(line.text, i18n.language)}>{line.text}</span>
                {line.url && (
                  <a
                    href={line.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-2 text-xs text-teal-700 hover:text-teal-800 underline break-all"
                    aria-label={t('pricing.sourceLinkLabel', { site: sourceSiteName(line.url) })}
                  >
                    {t('pricing.sourceLink', { site: sourceSiteName(line.url) })}
                    <span aria-hidden="true"> ↗</span>
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default PricingSection
