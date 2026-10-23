// Public transport settings and formatting shared by the worker, the hook and the cards
// (docs/TRANSIT_TIMES.md).

/** Whole-city walking graph and one school day of timetable, built monthly by transit.yml. */
export const FOOT_GRAPH_URL = '/routing/sofia-foot.bin.gz'
export const TIMETABLE_URL = '/routing/sofia-timetable.bin.gz'

// When the day starts, by the shift a school runs for the age group (none stored yet: 08:00).
const SHIFT_START = { morning: 7 * 3600 + 30 * 60, afternoon: 13 * 3600 + 30 * 60 }
export const DEFAULT_START = 8 * 3600

/** Seconds after midnight the child should arrive by at this building. */
export function arriveBySeconds(location, ageGroup) {
  const shift = (location?.age_group_shifts || []).find(item => item.age_group === ageGroup)?.shift
  return SHIFT_START[shift] ?? DEFAULT_START
}

/** "07:05" from seconds after midnight. */
export function clock(seconds) {
  const m = Math.floor(seconds / 60)
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

/** Whole minutes of a trip, at least 1. */
export function tripMinutes(trip) {
  return trip ? Math.max(1, Math.round((trip.arrive - trip.leave) / 60)) : null
}

/** "2026-10-27" from the timetable's 20261027. */
export function serviceDay(yyyymmdd) {
  const s = String(yyyymmdd)
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`
}

// A timetable not rebuilt for this long (the monthly job keeps failing) is not shown.
const MAX_AGE_DAYS = 75

/** Whether a timetable for this day, valid until validUntil, is still fit to show today. */
export function timetableFresh(serviceDate, validUntil, today = new Date()) {
  const day = d => Date.UTC(Number(String(d).slice(0, 4)), Number(String(d).slice(4, 6)) - 1, Number(String(d).slice(6, 8)))
  const now = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate())
  return now <= day(validUntil) && now - day(serviceDate) <= MAX_AGE_DAYS * 86400000
}

const MODE = { 0: 'tram', 1: 'metro', 3: 'bus', 11: 'trolleybus' }

/** "bus 94, then M2" for a trip's lines; a metro line's name says what it is. */
export function linesText(lines, t) {
  return lines
    .map(line => (MODE[line.type] === 'metro' ? line.name : `${t(`transitTime.mode.${MODE[line.type] || 'bus'}`)} ${line.name}`))
    .join(t('transitTime.then'))
}
