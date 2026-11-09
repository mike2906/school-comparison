/**
 * Live flow for the admission calculator on a phone, where the result and its save box sit
 * screens below the questions: the sticky total bar's "Save" button brings the save box
 * into view. (Its own file because a file runs at one viewport; the rest of the admission
 * flows are in admission.test.js, on a desktop.) Also the search list's note on saved
 * points, which names only what the listed schools show: none for private schools.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { KINDERGARTEN_INITIAL } from '../../src/admission/inputs.js'
import { planningYear } from '../../src/utils/ageSelection.js'
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

describe('the saved points note on the school list', () => {
  test('it shows for state schools with a preschool group, never for a list of private schools', async () => {
    // A child starting the preschool year at six: kindergarten rules rank it, at a kindergarten
    // or at a state school's preparatory group. Private schools admit by their own procedure.
    const startYear = planningYear()
    const answers = {
      ...KINDERGARTEN_INITIAL, birthYear: startYear - 6, inSofia: true, addressSince: '2019-01-01',
      parentChildSameAddress: true, parentActivities: ['working', 'working'], nurseryAttended: false,
    }
    await open('en', '/')
    await page.evaluate(value => localStorage.setItem('admissionAnswers', JSON.stringify(value)), {
      version: 1, child: { birthYear: answers.birthYear, startYear }, kindergarten: { startYear, answers, savedOn: '2026-11-09' },
    })
    const banner = page.getByTestId('saved-admission-banner')
    try {
      await open('en', `/search?age_group=preschool&education_level=primary&school_type=state&target_year=${startYear}`)
      await banner.getByText(t('en', 'savedAdmission.banner.kindergarten_school', { year: answers.birthYear })).waitFor()

      await open('en', `/search?age_group=preschool&education_level=primary&school_type=private&target_year=${startYear}`)
      await page.locator('a[href^="/en/schools/"]').first().waitFor()
      await page.waitForTimeout(2_000) // the rule files load after the list
      assert.equal(await banner.count(), 0, 'no note about the city\'s points over private schools')
    } finally {
      await page.evaluate(() => localStorage.removeItem('admissionAnswers'))
    }
  })
})
