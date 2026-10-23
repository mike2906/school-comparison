/**
 * A child's admission calendar (calendar.js) as an iCalendar feed (RFC 5545). The feed
 * holds dates for a birth year only, never anything a parent entered, so one static file
 * per birth year and language serves everyone (docs/ADMISSION_CALENDAR.md). Event UIDs
 * stay the same from build to build, so a subscribed calendar moves an event when its
 * date changes instead of adding a second one.
 */
import { milestoneEnd } from './calendar.js'

const ROMAN = ['I', 'II', 'III', 'IV', 'V']

/** The milestone's label: a round-specific text (`enrol_1`) wins over the general one. */
export function milestoneLabel(t, step, milestone) {
  const base = `calendar.milestones.${step.ruleStage}.${milestone.key}`
  const values = {
    round: milestone.round ? t(`calendar.rounds.${milestone.round}`) : '',
    stage: milestone.round ? ROMAN[milestone.round - 1] : '',
    time: milestone.time ?? '',
  }
  const specific = milestone.round ? t(`${base}_${milestone.round}`, values) : null
  if (specific && specific !== `${base}_${milestone.round}`) return specific
  return t(base, values)
}

/** The step's heading: "Kindergarten (first group) · September 2027". */
export function stepTitle(t, entry) {
  return t('calendar.stepTitle', { step: t(`calendar.steps.${entry.step.id}`), year: entry.startYear })
}

function escapeText(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n')
}

/** Folds a content line to 75 octets, never inside a UTF-8 character. */
export function foldLine(line) {
  const encoder = new TextEncoder()
  const parts = []
  let current = ''
  let size = 0
  for (const char of line) {
    const charSize = encoder.encode(char).length
    const limit = parts.length === 0 ? 75 : 74
    if (size + charSize > limit) {
      parts.push(current)
      current = ''
      size = 0
    }
    current += char
    size += charSize
  }
  parts.push(current)
  return parts.join('\r\n ')
}

function basicDate(iso) {
  return iso.replace(/-/g, '')
}

function nextDay(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10)
}

/**
 * The feed's text. `pageUrl` is the calendar page for this birth year, `formatDate` turns
 * an ISO date into the feed language's words, and `stamp` is the build time as an
 * iCalendar UTC date-time (`20261023T120000Z`).
 */
export function buildIcs(calendar, { t, formatDate, birthYear, pageUrl, stamp, host = 'schooldecider.com' }) {
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    `PRODID:-//${host}//Admission calendar//EN`,
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    `X-WR-CALNAME:${escapeText(t('calendar.feedName', { year: birthYear }))}`,
    `X-WR-CALDESC:${escapeText(t('calendar.feedDescription', { year: birthYear, url: pageUrl }))}`,
    'X-WR-TIMEZONE:Europe/Sofia',
    'REFRESH-INTERVAL;VALUE=DURATION:P1D',
    'X-PUBLISHED-TTL:P1D',
  ]
  for (const entry of calendar) {
    const title = stepTitle(t, entry)
    const notes = [title]
    if (entry.estimated) {
      notes.push(
        entry.scheduleDue
          ? t('calendar.estimatedUntil', { date: formatDate(entry.scheduleDue) })
          : t('calendar.estimated')
      )
    }
    if (entry.rulesStatus !== 'verified') notes.push(t('calendar.rulesProvisional'))
    if (entry.officialUrl) notes.push(t('calendar.official', { url: entry.officialUrl }))
    notes.push(pageUrl)
    for (const milestone of entry.milestones) {
      const label = milestoneLabel(t, entry.step, milestone)
      // The day the official dates are due is itself known, not estimated.
      const estimated = entry.estimated && milestone.key !== 'schedule_due'
      const summary = `${t(`calendar.stepsShort.${entry.step.id}`)} · ${label}${estimated ? ` (${t('calendar.estimatedShort')})` : ''}`
      const uid = [`born${birthYear}`, entry.step.id, entry.startYear, milestone.key, milestone.round].filter(v => v != null).join('-')
      lines.push(
        'BEGIN:VEVENT',
        `UID:${uid}@${host}`,
        `DTSTAMP:${stamp}`,
        `DTSTART;VALUE=DATE:${basicDate(milestone.date)}`,
        `DTEND;VALUE=DATE:${basicDate(nextDay(milestoneEnd(milestone)))}`,
        `SUMMARY:${escapeText(summary)}`,
        `DESCRIPTION:${escapeText(notes.join('\n'))}`,
        'TRANSP:TRANSPARENT',
      )
      // Reminders at 09:00 a week and a day before the last day to apply, counted from the
      // event's end so a deadline spanning days warns before its last one (apps that
      // honour alarms in subscribed calendars, such as Apple Calendar, show them).
      if (milestone.key === 'applications_lock' || milestone.key === 'applications') {
        for (const trigger of ['-P7DT15H', '-P1DT15H']) {
          lines.push('BEGIN:VALARM', 'ACTION:DISPLAY', `DESCRIPTION:${escapeText(summary)}`, `TRIGGER;RELATED=END:${trigger}`, 'END:VALARM')
        }
      }
      lines.push('END:VEVENT')
    }
  }
  lines.push('END:VCALENDAR')
  return lines.map(foldLine).join('\r\n') + '\r\n'
}
