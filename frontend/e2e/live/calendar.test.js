/**
 * Live flow for the admission calendar (/calendar): a parent picks the child's birth year,
 * sees the next step's dates, and the subscribe links point at a feed that is really there.
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, test } from 'node:test'

import { calendarBirthYears, feedPath } from '../../src/admission/calendar.js'
import { sofiaToday } from '../../src/admission/calculator.js'
import { planningYear } from '../../src/utils/ageSelection.js'
import { PHONE, SITE, open, page, prefix, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

function ruleFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? ruleFiles(path) : name.endsWith('.json') ? [path] : []
  })
}
const ruleSets = ruleFiles(fileURLToPath(new URL('../../src/admission/rules', import.meta.url)))
  .map(file => JSON.parse(readFileSync(file, 'utf8')))

describe('admission calendar', () => {
  for (const lang of ['bg', 'en']) {
    test(`a parent of a future kindergartner gets the dates and a working calendar link (${lang})`, async () => {
      const birthYear = planningYear() - 3
      await open(lang, '/calendar')
      await page.getByLabel(t(lang, 'calendar.birthYear')).selectOption(String(birthYear))
      const heading = t(lang, 'calendar.stepTitle', { step: t(lang, 'calendar.steps.kindergarten'), year: birthYear + 3 })
      await page.getByRole('heading', { name: heading }).waitFor()
      assert.match(page.url(), new RegExp(`born=${birthYear}`))

      const href = await page.getByRole('link', { name: t(lang, 'calendar.download') }).getAttribute('href')
      assert.equal(href, `${new URL(SITE).origin}${prefix(lang)}${feedPath(birthYear)}`)
      const google = await page.getByRole('link', { name: t(lang, 'calendar.google') }).getAttribute('href')
      assert.ok(google.includes(encodeURIComponent(href.replace(/^https?:/, 'webcal:'))))

      const response = await page.request.get(href)
      assert.equal(response.status(), 200)
      assert.match(response.headers()['content-type'] || '', /text\/calendar/)
      const feed = await response.text()
      assert.match(feed, /^BEGIN:VCALENDAR\r\n/)
      assert.match(feed.replace(/\r\n /g, ''), new RegExp(`UID:born${birthYear}-kindergarten-${birthYear + 3}-applications_lock@`))
    })
  }

  test('every birth year the page offers has a feed in both languages', async () => {
    for (const birthYear of calendarBirthYears(ruleSets, sofiaToday())) {
      for (const lang of ['bg', 'en']) {
        const response = await fetch(`${SITE}${prefix(lang)}${feedPath(birthYear)}`, { method: 'HEAD' })
        assert.equal(response.status, 200, `${lang} ${birthYear}`)
      }
    }
  })
})
