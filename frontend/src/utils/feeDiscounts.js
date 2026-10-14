// A school's fee discounts (the API's `fee_discounts`): "5% off tuition for a second child".
import { safeSourceUrl } from './priceSources.js'

/**
 * One line per discount: its sentence in `language`, else in the other one, and the page it
 * was read from. A discount with no sentence in either language is left out.
 */
export function feeDiscountLines(discounts, language) {
  if (!Array.isArray(discounts)) return []
  const other = language === 'bg' ? 'en' : 'bg'
  return discounts
    .map((discount, index) => {
      const texts = discount?.text_i18n || {}
      const text = [texts[language], texts[other]].find(value => typeof value === 'string' && value.trim())
      return text ? { key: index, text: text.trim(), url: safeSourceUrl(discount.source_url) } : null
    })
    .filter(Boolean)
}
