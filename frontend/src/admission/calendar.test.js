import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { calendarBirthYears, CALENDAR_STEPS, campaignDates, childCalendar, milestoneEnd, shiftToYear } from './calendar.js'
import { buildIcs, foldLine } from './ics.js'
import { createTranslator } from '../prerender/pages.js'
import { calendarFeeds } from '../prerender/calendars.js'

const RULES_DIR = new URL('./rules', import.meta.url).pathname

function ruleFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? ruleFiles(full) : name.endsWith('.json') ? [full] : []
  })
}

const RULE_SETS = ruleFiles(RULES_DIR).map(file => JSON.parse(readFileSync(file, 'utf8')))
const dictionary = language => JSON.parse(readFileSync(new URL(`../i18n/${language}.json`, import.meta.url), 'utf8'))
const step = id => CALENDAR_STEPS.find(s => s.id === id)

test('shiftToYear keeps the weekday or the date', () => {
  assert.equal(shiftToYear('2026-05-22', 1), '2027-05-21') // Friday to Friday
  assert.equal(shiftToYear('2026-05-22', 0), '2026-05-22')
  assert.equal(shiftToYear('2026-06-02', 1, 'date'), '2027-06-02')
  assert.equal(new Date(shiftToYear('2026-07-14', 5)).getUTCDay(), new Date('2026-07-14').getUTCDay())
})

test('milestones are well formed and in date order', () => {
  for (const ruleSet of RULE_SETS) {
    const milestones = ruleSet.schedule?.milestones
    if (!milestones) continue
    assert.ok(['weekday', 'date'].includes(ruleSet.schedule.estimate_by), `${ruleSet.id}: estimate_by`)
    assert.ok(ruleSet.sources.some(s => s.id === ruleSet.schedule.milestones_source), `${ruleSet.id}: milestones_source`)
    const start = Number(ruleSet.campaign.slice(0, 4))
    for (const m of milestones) {
      assert.match(m.date, /^\d{4}-\d{2}-\d{2}$/)
      assert.equal(Number(m.date.slice(0, 4)), start, `${ruleSet.id} ${m.key}: in the campaign's year`)
      if (m.end) assert.ok(m.end >= m.date, `${ruleSet.id} ${m.key}: end before start`)
    }
    const dates = milestones.map(m => m.date)
    assert.deepEqual(dates, [...dates].sort(), `${ruleSet.id}: milestones out of order`)
  }
})

test("milestones agree with the schedule keys the calculator reads", () => {
  for (const ruleSet of RULE_SETS) {
    const milestones = ruleSet.schedule?.milestones
    if (!milestones) continue
    const rankings = milestones.filter(m => m.key === 'ranking').map(m => m.date)
    const { first_general_ranking: firstGeneral, first_ranking: first, general_rankings: general, rankings: listed } = ruleSet.schedule
    if (firstGeneral) assert.equal(rankings[0], firstGeneral, ruleSet.id)
    if (first) assert.equal(rankings[0], first, ruleSet.id)
    if (general) assert.deepEqual(rankings, general, ruleSet.id)
    if (listed) assert.deepEqual(rankings, listed, ruleSet.id)
  }
})

test('estimated campaigns land on the dates their own rule files estimate', () => {
  for (const ruleSet of RULE_SETS.filter(r => r.schedule?.estimated && !r.schedule.milestones)) {
    const calendarStep = CALENDAR_STEPS.find(s => s.ruleStage === ruleSet.stage && s.city === (ruleSet.city ?? null))
    const dates = campaignDates(RULE_SETS, calendarStep, Number(ruleSet.campaign.slice(0, 4)))
    assert.equal(dates.estimated, true)
    const firstRanking = dates.milestones.find(m => m.key === 'ranking').date
    const expected = ruleSet.schedule.first_general_ranking || ruleSet.schedule.first_ranking
    if (expected) assert.equal(firstRanking, expected, ruleSet.id)
  }
})

test('a child born in 2024 gets every step ahead, estimated where dates are not out', () => {
  const calendar = childCalendar(RULE_SETS, 2024, '2026-10-23')
  assert.deepEqual(calendar.map(e => [e.step.id, e.startYear]), [
    ['kindergarten', 2027],
    ['preparatory', 2029],
    ['grade_1', 2031],
    ['grade_8', 2038],
  ])
  const kindergarten = calendar[0]
  assert.equal(kindergarten.estimated, true)
  assert.equal(kindergarten.rulesStatus, 'provisional')
  assert.deepEqual(kindergarten.milestones[0], { key: 'schedule_due', date: '2027-01-05' })
  assert.equal(kindergarten.milestones.find(m => m.key === 'applications_lock').date, '2027-05-20')
  // No rule file for 2029 yet: the rules are not written down either.
  assert.equal(calendar[1].rulesStatus, null)
  // Eighth grade has no publication date to point at.
  assert.ok(!calendar[3].milestones.some(m => m.key === 'schedule_due'))
})

test('a campaign still ranking stays; an ended one goes', () => {
  // Born 2023: the 2026 kindergarten campaign ranks free places until 27 November.
  const running = childCalendar(RULE_SETS, 2023, '2026-10-23')
  assert.equal(running[0].step.id, 'kindergarten')
  assert.equal(running[0].startYear, 2026)
  assert.equal(running[0].estimated, false)
  assert.equal(running[0].rulesStatus, 'verified')
  // Only what is left of it: the autumn rankings for free places.
  assert.deepEqual(running[0].milestones.map(m => m.key), ['current_rankings'])
  const after = childCalendar(RULE_SETS, 2023, '2026-11-28')
  assert.notEqual(after[0].startYear, 2026)
  // Born 2012: eighth grade in 2026 has ended, nothing is left.
  assert.deepEqual(childCalendar(RULE_SETS, 2012, '2026-10-23'), [])
})

test('feeds exist for 8th-graders-to-be up to next year’s babies', () => {
  const years = calendarBirthYears(RULE_SETS, '2026-10-23')
  assert.equal(years[0], 2027)
  assert.ok(years.includes(2013))
  assert.ok(!years.includes(2012))
})

function feedFor(language, birthYear = 2024, today = '2026-10-23') {
  const t = createTranslator(dictionary(language))
  return calendarFeeds(RULE_SETS, {
    t,
    language,
    origin: 'https://schooldecider.com',
    today,
    now: new Date('2026-10-23T12:00:00Z'),
  }).find(feed => feed.file.endsWith(`sofia-born-${birthYear}.ics`))
}

for (const language of ['bg', 'en']) {
  test(`the ${language} feed is valid iCalendar with every text translated`, () => {
    const feed = feedFor(language)
    assert.equal(feed.file, language === 'bg' ? 'calendar/sofia-born-2024.ics' : 'en/calendar/sofia-born-2024.ics')
    const text = feed.content
    assert.ok(text.startsWith('BEGIN:VCALENDAR\r\n') && text.endsWith('END:VCALENDAR\r\n'))
    for (const line of text.split('\r\n')) assert.ok(new TextEncoder().encode(line).length <= 75, line)
    assert.doesNotMatch(text, /calendar\.\w/, 'an untranslated key')
    assert.doesNotMatch(text, /\{\{/, 'an unfilled value')
    const unfolded = text.replace(/\r\n /g, '')
    const uids = [...unfolded.matchAll(/^UID:(.+)$/gm)].map(m => m[1])
    assert.equal(new Set(uids).size, uids.length)
    assert.ok(uids.includes('born2024-kindergarten-2027-applications_lock@schooldecider.com'))
    assert.equal((unfolded.match(/BEGIN:VEVENT/g) || []).length, uids.length)
    assert.match(unfolded, /DTSTART;VALUE=DATE:20270520\r\nDTEND;VALUE=DATE:20270521/)
    assert.match(unfolded, /\?born=2024/)
    // Two reminders per application deadline: kindergarten, preparatory group, 1st grade
    // and 8th grade's first stage (later rounds of an estimated campaign are left out).
    assert.equal((unfolded.match(/BEGIN:VALARM/g) || []).length, 2 * 4)
    assert.doesNotMatch(unfolded, /UID:born2024-grade_8-2038-ranking-2@/)
  })
}

test('a range event ends the day after its last day', () => {
  const unfolded = feedFor('en', 2023, '2026-05-01').content.replace(/\r\n /g, '')
  // 2026 kindergarten enrolment after the first ranking: 26 May to 5 June.
  assert.match(unfolded, /UID:born2023-kindergarten-2026-enrol-1@schooldecider.com\r\nDTSTAMP:20261023T120000Z\r\nDTSTART;VALUE=DATE:20260526\r\nDTEND;VALUE=DATE:20260606/)
})

test('foldLine never splits a character', () => {
  const line = `SUMMARY:${'Детска градина '.repeat(10)}`
  const folded = foldLine(line)
  assert.equal(folded.replace(/\r\n /g, ''), line)
  for (const part of folded.split('\r\n')) assert.ok(new TextEncoder().encode(part).length <= 75)
})

test('buildIcs escapes text', () => {
  const t = key => (key === 'calendar.feedName' ? 'a, b; c' : key)
  const text = buildIcs([], { t, formatDate: d => d, birthYear: 2024, pageUrl: 'x', stamp: '20261023T120000Z' })
  assert.match(text, /X-WR-CALNAME:a\\, b\\; c/)
})

test('every milestone the files use has a label in both languages', () => {
  for (const language of ['bg', 'en']) {
    const labels = dictionary(language).calendar.milestones
    for (const ruleSet of RULE_SETS) {
      for (const m of ruleSet.schedule?.milestones || []) assert.ok(labels[ruleSet.stage]?.[m.key], `${language} ${ruleSet.stage}.${m.key}`)
    }
    for (const s of CALENDAR_STEPS) assert.ok(labels[s.ruleStage].schedule_due)
  }
  assert.equal(milestoneEnd({ date: '2026-01-01' }), '2026-01-01')
})

test('the step list is in start-year order', () => {
  const offsets = CALENDAR_STEPS.map(s => s.offset)
  assert.deepEqual(offsets, [...offsets].sort((a, b) => a - b))
  assert.ok(step('grade_1'))
})
