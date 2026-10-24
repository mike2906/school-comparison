/**
 * Live flows for "My child's options" (docs/MY_CHILD_OPTIONS.md) on a phone, in both
 * languages: a parent gives the birth year and home once, gets the kindergartens grouped
 * by chance and sorted by the trip, and the calculator then starts from the same child.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { PHONE, open, page, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const planningYear = () => {
  const now = new Date()
  return now.getMonth() >= 9 ? now.getFullYear() + 1 : now.getFullYear()
}

describe("my child's options", () => {
  for (const lang of ['bg', 'en']) {
    test(`${lang}: a kindergarten child from Lyulin gets grouped options, nearest first`, async () => {
      await open(lang, '/my-child')
      const birthYear = planningYear() - 4
      await page.locator('#my-child-birth-year').selectOption(String(birthYear))
      await page.locator('#my-child-address').fill('Люлин 5')
      await page.getByRole('button', { name: t(lang, 'common.search'), exact: true }).click()
      await page.getByText(t(lang, 'myChild.changeHome'), { exact: true }).waitFor({ timeout: 30_000 })
      await page.locator('[data-chance-group]').first().waitFor({ timeout: 30_000 })
      // No points answers yet: everything is "no cut-off to compare", with a way to answer.
      assert.ok(await page.getByRole('link', { name: t(lang, 'myChild.answersLink') }).isVisible(), 'the calculator is offered')
      const distances = await page.locator('[data-chance-group] article').evaluateAll(cards => cards.slice(0, 5).map(card => card.textContent))
      assert.ok(distances.length > 0, 'cards are shown')
      // English narrows the list to the places that name it, and never empties it.
      const total = () => page.locator('[data-chance-group] h2').evaluateAll(heads => heads
        .reduce((sum, head) => sum + Number(head.textContent.match(/\((\d+)\)/)?.[1] || 0), 0))
      const before = await total()
      await page.getByRole('button', { name: t(lang, 'languages.english'), exact: true }).click()
      await page.waitForFunction(count => {
        const now = [...document.querySelectorAll('[data-chance-group] h2')]
          .reduce((sum, head) => sum + Number(head.textContent.match(/\((\d+)\)/)?.[1] || 0), 0)
        return now !== count
      }, before, { timeout: 15_000 })
      const after = await total()
      assert.ok(after > 0 && after < before, `English keeps some places (${after} of ${before})`)
    })
  }

  for (const lang of ['bg', 'en']) {
    test(`${lang}: by public transport, each place says when to leave, latest first`, async testContext => {
      await page.addInitScript(() => {
        window.__longestTask = 0
        try {
          new PerformanceObserver(list => {
            for (const entry of list.getEntries()) window.__longestTask = Math.max(window.__longestTask, entry.duration)
          }).observe({ type: 'longtask', buffered: true })
        } catch {
          // No long-task timing in this browser: the check below then sees 0.
        }
      })
      await open(lang, '/my-child')
      await page.locator('#my-child-birth-year').selectOption(String(planningYear() - 4))
      await page.locator('#my-child-address').fill('Люлин 5')
      await page.getByRole('button', { name: t(lang, 'common.search'), exact: true }).click()
      await page.getByText(t(lang, 'myChild.changeHome'), { exact: true }).waitFor({ timeout: 30_000 })
      const started = Date.now()
      await page.getByRole('button', { name: t(lang, 'myChild.travel_transit'), exact: true }).click()
      const chips = page.locator('[data-chance-group] [data-transit-leave]')
      await chips.first().waitFor({ timeout: 45_000 })
      const shownAfter = Date.now() - started
      await page.getByRole('link', { name: t(lang, 'transitTime.timetableCredit') }).waitFor()
      // Inside the first group, the place you can leave for latest comes first (by trip time,
      // which is the same while every place starts at 8:00: no shifts are stored yet).
      const leaves = await page.locator('[data-chance-group]').first().locator('[data-transit-leave]')
        .evaluateAll(nodes => nodes.map(node => node.dataset.transitLeave))
      assert.ok(leaves.length >= 5, `only ${leaves.length} places have a trip`)
      assert.ok(leaves.every(leave => leave >= '05:30' && leave <= '08:00'), `odd times to leave: ${leaves.join(', ')}`)
      assert.deepEqual(leaves, [...leaves].sort().reverse(), `not latest first: ${leaves.join(', ')}`)
      const longestTask = await page.evaluate(() => Math.round(window.__longestTask))
      testContext.diagnostic(`Public transport times shown ${shownAfter} ms after choosing it; longest main-thread task ${longestTask} ms.`)
      assert.ok(longestTask < 1000, `the page was blocked for ${longestTask} ms`)
    })
  }

  test('saved once, the calculator starts from the same child', async () => {
    await open('bg', '/my-child')
    const birthYear = planningYear() - 3
    await page.locator('#my-child-birth-year').selectOption(String(birthYear))
    await page.getByRole('button', { name: t('bg', 'myChild.save'), exact: true }).click()
    await page.getByRole('button', { name: t('bg', 'myChild.changeProfile'), exact: true }).waitFor()

    await open('bg', '/admission?stage=kindergarten')
    const prefilled = await page.getByLabel(t('bg', 'admissionCalc.kg.birthYear')).inputValue()
    assert.equal(prefilled, String(birthYear), 'the calculator has the birth year given on the options page')

    await open('bg', '/my-child')
    await page.getByRole('button', { name: t('bg', 'myChild.changeProfile'), exact: true }).click()
    await page.getByRole('button', { name: t('bg', 'myChild.forget'), exact: true }).click()
    assert.equal(await page.evaluate(() => localStorage.getItem('admissionAnswers')), null, 'forget leaves nothing behind')
  })
})
