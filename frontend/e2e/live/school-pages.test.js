/**
 * Live flows for what a parent reads on a school: finding it by name, the facts read
 * from its website (with open days), its Facebook and Instagram links, fees with their
 * payment plans, comparing fees side by side, and no pin for a school whose location is
 * only approximate.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { PHONE, allSchools, findSchool, open, page, schoolName, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const hasCoordinates = location => location.lat != null && location.lng != null
const isPrivate = school => ['private', 'international'].includes(school.school_type)
// The white card a section heading opens.
const card = heading => page.getByRole('heading', { name: heading, exact: true }).locator('xpath=ancestor::div[contains(@class, "rounded-2xl")][1]')
const planRow = school => (school.pricing || []).find(row => (row.pricing_context?.payment_options || []).length > 0)

describe('finding a school', () => {
  test('search by name, open the school, switch to English', async () => {
    const school = (await allSchools()).find(s => isPrivate(s) && schoolName(s, 'bg').length > 6)
    const name = schoolName(school, 'bg')
    await open('bg', '/search')
    // Phones and desktops get their own search box; use the one on screen.
    await page.getByPlaceholder(t('bg', 'landing.searchPlaceholder')).filter({ visible: true }).first().fill(name)
    await page.waitForURL(url => url.searchParams.get('q') === name)
    await page.locator(`a[href="/schools/${school.id}"]`).first().click()
    await page.waitForURL(url => url.pathname === `/schools/${school.id}`)
    await page.getByRole('heading', { level: 1, name, exact: true }).waitFor()

    await page.getByRole('link', { name: 'EN', exact: true }).first().click()
    await page.waitForURL(url => url.pathname === `/en/schools/${school.id}`)
    await page.getByRole('heading', { level: 1, name: schoolName(school, 'en'), exact: true }).waitFor()
  })
})

describe('a school page', () => {
  test('facts from the school website link their pages, open days included', async () => {
    const school = await findSchool(
      'with facts and an open day',
      s => (s.school_facts?.at_a_glance?.open_days || []).length > 0,
      isPrivate,
      200,
    )
    await open('bg', `/schools/${school.id}`)
    const facts = card(t('bg', 'schoolFacts.title'))
    await facts.waitFor()
    const line = facts.locator('dt', { hasText: t('bg', 'schoolFacts.glance.openDays') })
    await line.waitFor()
    const days = school.school_facts.at_a_glance.open_days
    const expected = days.find(day => day.register_url)?.register_url || days[0].source_url
    const href = await line.locator('xpath=following-sibling::dd[1]//a').getAttribute('href')
    assert.equal(href, expected, 'the open day links its sign-up or announcement page')
    // Every fact names the page it came from.
    const sources = await facts.locator('li a').evaluateAll(links => links.map(a => a.href))
    assert.ok(sources.length > 0 && sources.every(href => /^https?:\/\//.test(href)))
  })

  test("the school's own Facebook or Instagram is linked", async () => {
    const school = await findSchool('with social links', s => Boolean(s.social_links?.facebook || s.social_links?.instagram), () => true, 200)
    await open('bg', `/schools/${school.id}`)
    for (const network of ['facebook', 'instagram']) {
      const url = school.social_links[network]
      if (!url) continue
      const link = page.getByRole('link', { name: t('bg', `schools.${network}`), exact: true }).first()
      assert.equal(await link.getAttribute('href'), url)
      assert.equal(await link.getAttribute('target'), '_blank')
    }
  })

  test('a fee with a payment plan shows the plan under it, in euro', async () => {
    const listed = (await allSchools()).find(s => isPrivate(s) && planRow(s))
    assert.ok(listed, 'a private school with a payment plan')
    await open('bg', `/schools/${listed.id}`)
    const section = card(t('bg', 'pricing.title'))
    await section.getByText(t('bg', 'pricing.payInParts')).first().waitFor()
    const text = await section.innerText()
    assert.match(text, /EUR|€/, 'fees are shown in euro')
    assert.doesNotMatch(text, /лв|BGN/, 'no lev prices')
  })

  test('two private schools with fees compare side by side', async () => {
    const withFees = (await allSchools()).filter(s => isPrivate(s) && (s.pricing || []).some(row => row.amount != null || row.amount_min != null))
    assert.ok(withFees.length >= 2, 'two private schools with fees')
    const pair = withFees.slice(0, 2)
    await open('bg', `/compare?ids=${pair.map(s => s.id).join(',')}`)
    await page.getByRole('heading', { level: 1, name: t('bg', 'compare.title') }).waitFor()
    for (const school of pair) await page.getByText(schoolName(school, 'bg'), { exact: true }).first().waitFor()
    assert.match(await page.locator('body').innerText(), /EUR|€/)
  })

  test('a school with only an approximate location has no pin but keeps its address', async () => {
    const listed = (await allSchools()).find(s => (s.locations || []).length > 0 && !(s.locations || []).some(hasCoordinates) &&
      (s.locations || []).some(location => location.address_i18n?.bg || location.resolved_address_i18n?.bg))
    assert.ok(listed, 'a listed school without a published point')
    await open('bg', `/schools/${listed.id}`)
    await page.getByRole('heading', { name: t('bg', 'schools.locationsAndEnrollment') }).waitFor()
    assert.equal(await page.locator('a[href*="google.com/maps/dir"]').count(), 0, 'no directions to a guessed point')
    const location = listed.locations.find(l => l.resolved_address_i18n?.bg || l.address_i18n?.bg)
    const address = location.resolved_address_i18n?.bg || location.address_i18n.bg
    await page.getByText(address, { exact: false }).first().waitFor()
  })
})
