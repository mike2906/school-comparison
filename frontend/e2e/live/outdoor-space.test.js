/**
 * Live flows for "Outdoor space" on a school page: a school whose website describes its
 * yard shows that line with a link to the page, in Bulgarian and English; a school no
 * source covers says "Not known" and offers to take a photo by email; and a building
 * checked on a visit says so with the date.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { outdoorEntries } from '../../src/utils/outdoorSpace.js'
import { PHONE, findSchool, open, page, schoolDetail, allSchools, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const card = heading => page.getByRole('heading', { name: heading, exact: true }).locator('xpath=ancestor::div[contains(@class, "rounded-2xl")][1]')
const entries = (school, lang = 'bg') => outdoorEntries(school, lang)

// The section ships with the API change; a preview built before that deploy has nothing to show.
async function apiHasOutdoorSpace(testContext) {
  const first = (await allSchools())[0]
  if ('outdoor_space' in (await schoolDetail(first.id))) return true
  testContext.skip('the live API does not serve outdoor_space yet')
  return false
}

describe('outdoor space', () => {
  test('a yard the school website describes, with its page, in BG and EN', async testContext => {
    if (!(await apiHasOutdoorSpace(testContext))) return
    const school = await findSchool(
      'whose website describes its outdoor space',
      s => entries(s).some(e => e.kind === 'grounds' && e.source === 'website'),
      s => s.locations?.length === 1,
      300,
    )
    const entry = entries(school).find(e => e.source === 'website')
    await open('bg', `/schools/${school.id}`)
    const section = card(t('bg', 'outdoor.title'))
    await section.waitFor()
    await section.getByText(t('bg', 'outdoor.websiteSays'), { exact: true }).first().waitFor()
    await section.getByText(entry.facts[0].text, { exact: false }).first().waitFor()
    const links = await section.getByRole('link', { name: t('bg', 'schoolFacts.source') }).evaluateAll(els => els.map(el => el.href))
    assert.ok(links.length > 0, 'the website line links its page')

    const english = entries(school, 'en').find(e => e.source === 'website')
    await open('en', `/schools/${school.id}`)
    await card(t('en', 'outdoor.title')).getByText(english.facts[0].text, { exact: false }).first().waitFor()
  })

  test('a building no source covers says "Not known" and asks for a photo', async testContext => {
    if (!(await apiHasOutdoorSpace(testContext))) return
    const school = await findSchool('with no outdoor answer', s => entries(s).some(e => e.kind === 'unknown'), () => true, 300)
    await open('bg', `/schools/${school.id}`)
    const section = card(t('bg', 'outdoor.title'))
    await section.getByText(t('bg', 'outdoor.kinds.unknown'), { exact: false }).first().waitFor()
    const href = await section.getByRole('link', { name: t('bg', 'outdoor.sendPhoto') }).first().getAttribute('href')
    assert.match(href, /^mailto:/)
  })

  test('a building checked on a visit names the date', async testContext => {
    if (!(await apiHasOutdoorSpace(testContext))) return
    const school = await findSchool('checked on a visit', s => entries(s).some(e => e.source === 'visit'), s => s.school_type !== 'state', 300)
    const entry = entries(school, 'en').find(e => e.source === 'visit')
    await open('en', `/schools/${school.id}`)
    const section = card(t('en', 'outdoor.title'))
    await section.getByText(t('en', 'outdoor.sources.visit', { date: entry.checkedOn })).first().waitFor()
  })
})
