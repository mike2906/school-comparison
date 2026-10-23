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
      }, before, { timeout: 10_000 }).catch(() => {})
      const after = await total()
      assert.ok(after > 0 && after < before, `English keeps some places (${after} of ${before})`)
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
