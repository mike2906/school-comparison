/**
 * Live flow for the admission calculator on a phone, where the result and its save box sit
 * screens below the questions: the sticky total bar's "Save" button brings the save box
 * into view. (Its own file because a file runs at one viewport; the rest of the admission
 * flows are in admission.test.js, on a desktop.)
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { PHONE, open, page, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

describe('admission calculator on a phone', () => {
  test('the total bar takes the parent to the save button', async () => {
    await open('bg', '/admission?stage=kindergarten')
    const year = Number(await page.getByLabel(t('bg', 'admissionCalc.startYear')).inputValue())
    await page.getByLabel(t('bg', 'admissionCalc.kg.birthYear')).selectOption(String(year - 3))

    const save = page.getByRole('button', { name: t('bg', 'savedAdmission.save'), exact: true })
    assert.ok(await save.evaluate(el => el.getBoundingClientRect().top > window.innerHeight), 'the save button starts below the screen')
    await page.getByRole('button', { name: t('bg', 'savedAdmission.jumpToSave'), exact: true }).click()
    // In view and not under the sticky bar: the button itself is what a tap on its middle hits.
    const tappable = () => save.evaluate(el => {
      const box = el.getBoundingClientRect()
      return box.top >= 0 && el.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2))
    })
    for (let tries = 0; tries < 20 && !(await tappable()); tries++) await page.waitForTimeout(250)
    assert.ok(await tappable(), 'the save button is on screen and can be tapped')
  })
})
