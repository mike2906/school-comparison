/**
 * Live flow for rush-hour drive times: a parent with a saved location sees a drive time
 * on each school card, the OpenStreetMap credit, and can sort the list by drive time.
 * It also reports how long the times take to appear and the longest main-thread task,
 * since the road map is downloaded and searched in the parent's browser.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { PHONE, number, open, page, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

// Central Sofia (НДК), so every school is within reach.
const HOME = { lat: 42.6847, lng: 23.3188, address: 'НДК, София' }
// The longest a set location may keep the page busy before a parent notices.
const MAX_LONG_TASK_MS = 1000

describe('drive times', () => {
  test('a saved location shows drive times, sorted by drive time', async testContext => {
    await page.addInitScript(home => {
      localStorage.setItem('userLocation', JSON.stringify(home))
      window.__longestTask = 0
      try {
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) window.__longestTask = Math.max(window.__longestTask, entry.duration)
        }).observe({ type: 'longtask', buffered: true })
      } catch {
        // No long-task timing in this browser: the check below then sees 0.
      }
    }, HOME)
    const started = Date.now()
    await open('bg', '/search?sort=drive')

    const badges = page.getByTitle(new RegExp(`^${t('bg', 'driveTime.aria', { minutes: 'MINUTES' }).replace('MINUTES', '\\d+')}$`))
    await badges.first().waitFor({ timeout: 45_000 })
    const shownAfter = Date.now() - started
    await page.getByText(t('bg', 'driveTime.note')).waitFor()
    await page.getByRole('link', { name: t('bg', 'driveTime.attribution') }).waitFor()
    assert.equal(new URL(page.url()).searchParams.get('sort'), 'drive')

    const minutes = (await badges.allTextContents()).map(text => number(text.replace(/[^\d]/g, '')))
    assert.ok(minutes.length >= 5, `only ${minutes.length} cards show a drive time`)
    assert.ok(minutes.every(m => m >= 1 && m < 120), `odd drive times: ${minutes.join(', ')}`)
    assert.deepEqual(minutes, [...minutes].sort((a, b) => a - b), `not sorted by drive time: ${minutes.join(', ')}`)
    // Cards showing the same minutes go nearest first (122 OU at 0.1 km was below 107 OU at 0.6 km).
    const cards = await page.locator('.school-card').evaluateAll(all => all.map(card => [
      card.querySelector('span[title]')?.textContent || '',
      [...card.querySelectorAll('span')].find(span => span.textContent.trim().startsWith('📍'))?.textContent || '',
    ]))
    const pairs = cards
      .filter(([drive, distance]) => /\d/.test(drive) && /\d/.test(distance))
      .map(([drive, distance]) => [number(drive.replace(/[^\d]/g, '')), number(distance.replace(/[^\d.,]/g, ''))])
    pairs.slice(1).forEach(([driveMinutes, km], i) => {
      const [previousMinutes, previousKm] = pairs[i]
      if (driveMinutes === previousMinutes) assert.ok(previousKm <= km, `${previousKm} km listed above ${km} km at ${driveMinutes} min`)
    })

    const longestTask = await page.evaluate(() => Math.round(window.__longestTask))
    testContext.diagnostic(`Drive times shown ${shownAfter} ms after opening the page; longest main-thread task ${longestTask} ms.`)
    assert.ok(longestTask < MAX_LONG_TASK_MS, `the page was blocked for ${longestTask} ms`)
  })
})
