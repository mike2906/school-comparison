// A fee's other ways to pay, in parts, as the school prints them (the API's
// `pricing_context.payment_options`): "2 × 4,575 EUR (9,150 EUR in total)".
import { displayPrice } from './pricing.js'

// What the option costs in all: the total the school prints, else its parts added up.
function optionTotal(option) {
  if (option.total != null) return option.total
  if (option.payments == null || option.instalment == null) return null
  return Math.round(((option.first_payment || 0) + option.payments * option.instalment) * 100) / 100
}

/**
 * One payment option as text, or null when it says nothing payable. `money(value)` formats
 * an amount already in the display currency.
 */
export function paymentOptionText(option, currency, money, t) {
  const shown = (value) => money(displayPrice(value, currency).value)
  const total = optionTotal(option)
  if (option.payments != null && option.instalment != null) {
    const parts = `${option.payments} × ${shown(option.instalment)}`
    const schedule = option.first_payment != null ? `${shown(option.first_payment)} + ${parts}` : parts
    return total != null ? `${schedule} (${t('pricing.paymentOptionTotal', { total: shown(total) })})` : schedule
  }
  // The total of a plan, said to be one: 170's "4,200 EUR in instalments" read as the size of
  // each instalment.
  if (total == null) return null
  return option.payments != null
    ? t('pricing.paymentOptionPaymentsTotal', { count: option.payments, total: shown(total) })
    : t('pricing.paymentOptionTotal', { total: shown(total) })
}

/** The texts of a price row's payment options, in the school's order. */
export function paymentOptionTexts(price, money, t) {
  const options = price?.pricing_context?.payment_options
  if (!Array.isArray(options)) return []
  return options.map(option => paymentOptionText(option, price.currency, money, t)).filter(Boolean)
}
