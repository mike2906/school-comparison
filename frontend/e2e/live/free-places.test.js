/**
 * Live flows for today's free places in municipal kindergartens (docs/FREE_PLACES.md): the
 * API has a fresh ИСОДЗ snapshot matched to most municipal kindergartens, a kindergarten
 * page lists its groups' free places, and a search for a child's birth year this school
 * year puts that child's free places on each card. Municipal schools' ПГУ and 1st grade are
 * in the same answer.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { childFreePlaces, schoolFreePlaces, usableFreePlaces } from '../../src/utils/freePlaces.js'
import { usesSofiaKindergartenSystem } from '../../src/utils/admission.js'
import { API, PHONE, allSchools, fetchJson, open, page, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const plural = (lang, key, count) => t(lang, `${key}_${count === 1 ? 'one' : 'other'}`, { count })
const freePlaces = () => fetchJson(`${API}/schools/free-places?country_code=bg&city=sofia`)
// The school year now running: ИСОДЗ's free places are for groups already under way.
const schoolYear = () => {
  const now = new Date()
  return now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1
}

describe('free kindergarten places', () => {
  test('the API has a fresh snapshot for most municipal kindergartens', async () => {
    const data = usableFreePlaces(await freePlaces())
    assert.ok(data, 'a free places snapshot from the last 48 hours')
    const kindergartens = (await allSchools()).filter(usesSofiaKindergartenSystem)
    const withPlaces = kindergartens.filter(school => schoolFreePlaces(data, school.id).length > 0)
    assert.ok(withPlaces.length >= 0.8 * kindergartens.length, `${withPlaces.length} of ${kindergartens.length} municipal kindergartens have groups`)
    const firstGrade = Object.values(data.schools).filter(entries => entries.some(entry => entry.stage === 'grade_1'))
    assert.ok(firstGrade.length >= 100, `${firstGrade.length} schools with 1st-grade places`)
    for (const entries of Object.values(data.schools)) {
      for (const entry of entries) {
        assert.ok(entry.free >= 0 && entry.free <= entry.capacity, JSON.stringify(entry))
      }
    }
  })

  test('a kindergarten page lists free places per group', async () => {
    const data = usableFreePlaces(await freePlaces())
    const school = (await allSchools()).find(s =>
      usesSofiaKindergartenSystem(s) && schoolFreePlaces(data, s.id).some(entry => entry.free > 0))
    assert.ok(school, 'a municipal kindergarten with a free place today')
    const entry = schoolFreePlaces(data, school.id).find(e => e.free > 0)
    await open('bg', `/schools/${school.id}`)
    const section = page.locator('section').filter({ has: page.getByRole('heading', { name: t('bg', 'freePlaces.title'), exact: true }) })
    await section.getByRole('list', { name: t('bg', 'freePlaces.listLabel') }).first().waitFor()
    await section.getByText(plural('bg', 'freePlaces.free', entry.free), { exact: true }).first().waitFor()
    await section.getByRole('link', { name: 'kg.sofia.bg' }).waitFor()
  })

  test('a search for a birth year shows that child\'s free places on the cards', async () => {
    const data = usableFreePlaces(await freePlaces())
    const year = schoolYear()
    const birthYear = year - 3  // the first kindergarten group
    const school = (await allSchools()).find(s =>
      usesSofiaKindergartenSystem(s) && childFreePlaces(schoolFreePlaces(data, s.id), birthYear, year)?.free > 0)
    assert.ok(school, `a kindergarten with a free place for born ${birthYear}`)
    await open('bg', `/search?age_group=first&birth_year=${birthYear}&target_year=${year}&school_type=state`)
    const line = page.getByTestId('free-places-line').first()
    await line.waitFor()
    assert.match(await line.innerText(), new RegExp(String(birthYear)))
  })
})
