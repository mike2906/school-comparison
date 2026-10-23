/**
 * Live flows for the parent's location on the search page: an address typed the way
 * Sofia parents write it lands in Sofia, and a place typed into the name search is
 * offered as a location. Both use OpenStreetMap's search, as the page does.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { API, fetchJson, open, page, t, liveBrowser } from './live.js'

liveBrowser()

const storedLocation = () => page.evaluate(() => JSON.parse(localStorage.getItem('userLocation') || 'null'))
const nearChip = () => page.getByRole('button', { name: new RegExp(`^${t('bg', 'location.nearShort', { address: 'X' }).split('X')[0]}`) })

// ж.к. Люлин 5, as OpenStreetMap places it.
const LYULIN_5 = { lat: 42.7152, lng: 23.2387 }
// Places with no school of that name or address, so the list comes up empty.
const AREA_NAMES = ['Студентски град', 'Младост', 'Гео Милев']

describe('location', () => {
  test('"Люлин 5" typed alone is the housing estate in Sofia, not a street elsewhere', async () => {
    await open('bg', '/search')
    await page.getByRole('button', { name: t('bg', 'location.setShort') }).first().click()
    await page.locator('#location-address').fill('Люлин 5')
    await page.getByRole('button', { name: t('bg', 'common.search'), exact: true }).click()
    await nearChip().first().waitFor({ timeout: 30_000 })
    const location = await storedLocation()
    const km = Math.hypot((location.lat - LYULIN_5.lat) * 111, (location.lng - LYULIN_5.lng) * 82)
    assert.ok(km < 2, `"Люлин 5" placed ${km.toFixed(1)} km from ж.к. Люлин 5 (${location.address})`)
  })

  test('a place typed as a school name is offered as the location', async () => {
    let place = null
    for (const name of AREA_NAMES) {
      const hits = await fetchJson(`${API}/schools/search?q=${encodeURIComponent(name)}`)
      if (hits.length === 0) {
        place = name
        break
      }
    }
    assert.ok(place, `every one of ${AREA_NAMES.join(', ')} finds a school now: pick a new place`)
    await open('bg', '/search')
    await page.getByPlaceholder(t('bg', 'nameSearch.placeholderShort')).filter({ visible: true }).first().fill(place)
    await page.waitForURL(url => url.searchParams.get('q') === place)
    await page.getByRole('button', { name: t('bg', 'schools.searchNear', { place }) }).click()
    await nearChip().first().waitFor({ timeout: 30_000 })
    await page.waitForURL(url => !url.searchParams.get('q'))
    assert.ok(await storedLocation(), 'a location was set')
  })

  test('"This is my home" keeps the home after the location is cleared, and "Forget home" drops it', async () => {
    await open('bg', '/search')
    await page.getByRole('button', { name: t('bg', 'location.setShort') }).first().click()
    await page.locator('#location-address').fill('Люлин 5')
    await page.getByRole('button', { name: t('bg', 'common.search'), exact: true }).click()
    await nearChip().first().waitFor({ timeout: 30_000 })
    await nearChip().first().click()
    await page.getByRole('button', { name: t('bg', 'location.saveAsHome') }).click()
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('admissionAnswers') || 'null'))
    assert.ok(saved?.homePoint, 'the home point is saved')

    // A new visit with no location of its own starts from the home.
    await page.evaluate(() => localStorage.removeItem('userLocation'))
    await open('bg', '/search')
    await nearChip().first().waitFor({ timeout: 10_000 })
    await nearChip().first().click()
    await page.getByRole('button', { name: t('bg', 'location.forgetHome') }).click()
    const after = await page.evaluate(() => localStorage.getItem('admissionAnswers'))
    assert.equal(after, null, 'nothing else was saved, so nothing is left')
    assert.equal(await storedLocation(), null, 'the list no longer measures from the forgotten home')
  })
})
