/**
 * Live flows for the district pages: from the A–Z list to a district, a municipal
 * kindergarten there with the same-district points note and its address, on to its page;
 * a school with a building in another district listed there too; and the English page.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { catchmentUrl, districtLabel } from '../../src/admission/catchments.js'
import { CATCHMENT_CAMPAIGNS } from '../../src/admission/loadCatchments.js'
import { districtGroups } from '../../src/utils/districtPages.js'
import { PHONE, SITE, allSchools, fetchJson, open, page, schoolName, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const districts = () => fetchJson(`${SITE}${catchmentUrl(CATCHMENT_CAMPAIGNS[0], 'districts.json')}`)

describe('district pages', () => {
  test('A–Z list → a district → a municipal kindergarten there → its page', async () => {
    const groups = districtGroups(await allSchools(), await districts(), 'bg')
    const group = groups.find(g => g.sections.some(s => s.key === 'kindergartens' && s.entries.some(e => e.school.school_type === 'state')))
    assert.ok(group, 'a district with a municipal kindergarten')
    const entry = group.sections.find(s => s.key === 'kindergartens').entries.find(e => e.school.school_type === 'state')

    await open('bg', '/schools')
    await page.getByRole('link', { name: t('bg', 'districtPages.title'), exact: true }).click()
    await page.waitForURL(url => url.pathname === '/districts')
    await page.getByRole('link', { name: t('bg', 'seo.district', { district: group.label }), exact: true }).click()
    await page.waitForURL(url => url.pathname === `/districts/${group.slug}`)
    await page.getByRole('heading', { level: 1, name: t('bg', 'districtPages.pageTitle', { district: group.label }) }).waitFor()
    await page.getByText(t('bg', 'admissionArea.districtIntro', { district: t('bg', 'seo.district', { district: group.label }) })).waitFor()

    const link = page.locator(`main a[href="/schools/${entry.school.id}"]`)
    await link.click()
    await page.waitForURL(url => url.pathname === `/schools/${entry.school.id}`)
    await page.getByRole('heading', { level: 1, name: entry.name, exact: true }).waitFor()
  })

  test('a school with buildings in two districts is listed in both', async () => {
    const all = await allSchools()
    const groups = districtGroups(all, await districts(), 'bg')
    const counts = new Map()
    for (const g of groups) for (const s of g.sections) for (const e of s.entries) counts.set(e.school.id, [...(counts.get(e.school.id) || []), g])
    const [id, where] = [...counts].find(([, list]) => list.length > 1) || []
    assert.ok(id, 'a school with buildings in two districts')
    for (const group of where.slice(0, 2)) {
      await open('bg', `/districts/${group.slug}`)
      await page.locator(`main a[href="/schools/${id}"]`).waitFor()
    }
  })

  test('the English page names the district in English and links English school pages', async () => {
    const groups = districtGroups(await allSchools(), await districts(), 'en')
    const group = groups[0]
    const entry = group.sections[0].entries[0]
    await open('en', `/districts/${group.slug}`)
    await page.getByRole('heading', { level: 1, name: t('en', 'districtPages.pageTitle', { district: districtLabel(group.name, 'en') }) }).waitFor()
    await page.locator(`main a[href="/en/schools/${entry.school.id}"]`, { hasText: schoolName(entry.school, 'en') }).waitFor()
  })
})
