/**
 * Live flows for the compare page's yearly cost table (docs/YEARLY_COST.md) on a phone, in
 * both languages: a parent comparing private schools for grade 1 sees what each requires a
 * year (food counted), ticks the school bus to add it, and finds the tick kept on the next
 * visit; the list's fees say what they leave out.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { formatCurrency, statedTuition } from '../../src/utils/pricing.js'
import { feeOnTop, offersStage, totalWithNeeds, yearCost } from '../../src/utils/yearCost.js'
import { PHONE, allSchools, open, page, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const money = (value, lang) => (Math.round(value.min) === Math.round(value.max)
  ? formatCurrency(value.min, lang)
  : `${formatCurrency(value.min, lang)}–${formatCurrency(value.max, lang)}`)
// Intl writes narrow and normal no-break spaces; the page text is compared without spaces.
const squash = text => text.replace(/\s/g, '')

/** Two private schools whose grade 1 has a yearly total, one of them with a priced bus. */
async function pickSchools() {
  // The list answer carries the same fee rows as the compare page's.
  const schools = (await allSchools()).filter(school => school.school_type === 'private' && (school.pricing || []).length > 0)
  const usable = schools.filter(school => offersStage(school, 'grade_1') && yearCost(school.pricing, 'grade_1')?.required)
  const withBus = usable.find(school => totalWithNeeds(yearCost(school.pricing, 'grade_1'), ['transport']).missing.length === 0 &&
    yearCost(school.pricing, 'grade_1').lines.transport.options.length > 0)
  assert.ok(withBus, 'no live school has a grade 1 total and a yearly bus price')
  const other = usable.find(school => school.id !== withBus.id)
  assert.ok(other, 'only one live school has a grade 1 total')
  return [withBus, other]
}

describe('yearly cost table', () => {
  for (const lang of ['bg', 'en']) {
    test(`${lang}: grade 1 shows what each school requires a year, and a ticked bus is added and kept`, async () => {
      const schools = await pickSchools()
      await open(lang, `/compare?ids=${schools.map(school => school.id).join(',')}`)
      const section = page.locator('section:visible').filter({ hasText: t(lang, 'yearCost.title') })
      await section.waitFor()
      await section.locator('select').selectOption('grade_1')

      const rowText = async (key) => {
        const heading = section.locator('h3', { hasText: t(lang, key) }).first()
        return squash(await heading.locator('xpath=following-sibling::div[1]').innerText())
      }
      const required = await rowText('yearCost.lines.requiredTotal')
      for (const school of schools) {
        const expected = squash(money(yearCost(school.pricing, 'grade_1').required.value, lang))
        assert.ok(required.includes(expected), `${school.id}: required total ${expected} not in "${required}"`)
        // Food left out of a total is named, never dropped silently.
        const food = yearCost(school.pricing, 'grade_1').required.missing.find(item => item.line === 'food')
        if (food) {
          const named = squash(t(lang, `yearCost.reasons.${food.reason}`, { line: t(lang, 'yearCost.lines.food') }))
          assert.ok(required.includes(named), `${school.id}: food not named as left out`)
        }
      }

      await section.locator('label', { hasText: t(lang, 'yearCost.lines.transport') }).locator('input').check()
      const [withBus] = schools
      const expected = squash(money(totalWithNeeds(yearCost(withBus.pricing, 'grade_1'), ['transport']).value, lang))
      await section.locator('h3', { hasText: t(lang, 'yearCost.lines.withNeeds') }).waitFor()
      assert.ok((await rowText('yearCost.lines.withNeeds')).includes(expected), `${withBus.id}: total with the bus ${expected} not shown`)

      // The tick is the parent's, not this page's: the next visit adds the bus again.
      await page.reload({ waitUntil: 'networkidle' })
      await section.locator('h3', { hasText: t(lang, 'yearCost.lines.withNeeds') }).waitFor()
      assert.ok(await section.locator('label', { hasText: t(lang, 'yearCost.lines.transport') }).locator('input').isChecked(), 'the bus tick was not kept')
    })
  }

  for (const lang of ['bg', 'en']) {
    test(`${lang}: a private school's fee in the list says whether food is in it`, async () => {
      await open(lang, `/search?age_group=grade_1_4&school_type=private`)
      const cards = page.locator('article[data-school-id]')
      await cards.first().waitFor()
      const shown = await cards.evaluateAll(items => items.slice(0, 15).map(item => [Number(item.dataset.schoolId), item.textContent]))
      const byId = new Map((await allSchools()).map(school => [school.id, school]))
      const words = {
        included: t(lang, 'yearCost.card.foodIncluded'),
        extra: t(lang, 'yearCost.card.onTop', { items: t(lang, 'yearCost.card.items.food') }),
        unknown: t(lang, 'yearCost.card.foodUnknown'),
      }
      let checked = 0
      for (const [id, text] of shown) {
        const school = byId.get(id)
        // A school's own printed total may hold food: it gets no marker.
        const onTop = school && !statedTuition(school.pricing, { ageGroup: 'grade_1_4' })?.total && feeOnTop(school, 'grade_1_4')
        if (!onTop || !text.includes('💰')) continue
        assert.ok(text.includes(words[onTop.food]), `${id}: "${words[onTop.food]}" not on its card`)
        checked += 1
      }
      assert.ok(checked >= 3, `only ${checked} priced private cards checked`)
    })
  }
})
