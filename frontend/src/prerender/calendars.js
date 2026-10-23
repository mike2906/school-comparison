/**
 * The admission calendar feeds the build writes: one static .ics per birth year and
 * language (docs/ADMISSION_CALENDAR.md). They carry no parent's data, so a deploy that
 * changes a rule file's dates updates every subscribed calendar the next time it checks.
 */
import { calendarBirthYears, childCalendar, feedPath } from '../admission/calendar.js'
import { buildIcs } from '../admission/ics.js'
import { formatRankingDay } from '../admission/calculator.js'
import { languagePath } from '../utils/languageUrl.js'

/** The calendar page for a birth year, as an absolute URL. */
export function calendarPageUrl(origin, language, birthYear) {
  return `${origin}${languagePath('/calendar', language)}?born=${birthYear}`
}

/**
 * `[{ file, content }]` for every birth year with something still to come on `today`.
 * `now` gives the feeds' DTSTAMP.
 */
export function calendarFeeds(ruleSets, { t, language, origin, today, now }) {
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  const host = new URL(origin).hostname
  return calendarBirthYears(ruleSets, today).map(birthYear => ({
    file: languagePath(feedPath(birthYear), language).replace(/^\//, ''),
    content: buildIcs(childCalendar(ruleSets, birthYear, today), {
      t,
      formatDate: iso => formatRankingDay(iso, language),
      birthYear,
      pageUrl: calendarPageUrl(origin, language, birthYear),
      stamp,
      host,
    }),
  }))
}
