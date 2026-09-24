import { parsePhones } from '../../utils/phone'

export const PHONE_ICON = 'M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z'

/** Tappable, normalised phone numbers from one published phone field. */
function PhoneLinks({ phone, className = '', showIcon = true }) {
  const phones = parsePhones(phone)
  if (phones.length === 0) return null

  return phones.map((entry, idx) => {
    const content = (
      <>
        {showIcon && (
          <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={PHONE_ICON} />
          </svg>
        )}
        <span className="whitespace-nowrap">{entry.display}</span>
      </>
    )
    return entry.href ? (
      <a key={idx} href={entry.href} className={className}>{content}</a>
    ) : (
      <span key={idx} className={className}>{content}</span>
    )
  })
}

export default PhoneLinks
