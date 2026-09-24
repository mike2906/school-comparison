/**
 * Display and dial helpers for the free-text phone numbers schools publish.
 *
 * Registry phones arrive in many shapes: "02/824 11 70", "/0884305108",
 * "+359/889243971", "02/0887070435" (a Sofia area code glued onto a mobile number),
 * or two numbers joined by ";". Recognised Bulgarian numbers are regrouped for reading
 * and dialled in international form; anything else is shown as published.
 */

const BG_MOBILE = /^(8[7-9]|9[89])\d{7}$/
const SOFIA_LANDLINE = /^2\d{7}$/

function cleanPart(raw) {
  let text = String(raw).trim().replace(/^\/+/, '').trim()
  // "02/" in front of another full number (mobile, "+359…", or a repeated "02/") is a
  // registry artefact, not part of the number.
  while (/^02\s*\/\s*(?=[0+])/.test(text)) {
    text = text.replace(/^02\s*\/\s*/, '')
  }
  return text
}

/** The national significant number (no leading 0 or country code), or null. */
function bulgarianNsn(text) {
  const digits = text.replace(/\D/g, '')
  const intl = digits.match(/^(?:00|0)?359(\d+)$/)
  if (intl) return { nsn: intl[1].replace(/^0/, ''), international: true }
  if (/^0\d+$/.test(digits)) return { nsn: digits.slice(1), international: false }
  return null
}

function formatBulgarian(nsn, international) {
  if (BG_MOBILE.test(nsn)) {
    // Bulgarians write mobiles as "0888 123 456"; the international form as
    // "+359 88 812 3456".
    return international
      ? `+359 ${nsn.slice(0, 2)} ${nsn.slice(2, 5)} ${nsn.slice(5)}`
      : `0${nsn.slice(0, 3)} ${nsn.slice(3, 6)} ${nsn.slice(6)}`
  }
  if (SOFIA_LANDLINE.test(nsn)) {
    const groups = `${nsn.slice(1, 4)} ${nsn.slice(4)}`
    return international ? `+359 2 ${groups}` : `02 ${groups}`
  }
  return null
}

function parsePhonePart(raw) {
  const text = cleanPart(raw)
  if (!text) return null

  const bg = bulgarianNsn(text)
  const display = bg ? formatBulgarian(bg.nsn, bg.international) : null
  if (display) {
    return { display, href: `tel:+359${bg.nsn}` }
  }

  const digits = text.replace(/\D/g, '')
  const plus = text.startsWith('+') ? '+' : ''
  return {
    display: text,
    href: digits.length >= 6 ? `tel:${plus}${digits}` : null,
  }
}

/**
 * Split a published phone string into display/dial pairs.
 *
 * Returns `[{ display, href }]` (empty for a missing value). `href` is a `tel:` link of
 * digits with an optional leading "+", or null when too few digits remain to dial.
 */
export function parsePhones(raw) {
  if (raw == null) return []
  return String(raw)
    .split(/[;,]/)
    .map(parsePhonePart)
    .filter(Boolean)
}
