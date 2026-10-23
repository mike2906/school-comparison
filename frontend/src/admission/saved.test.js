import test from 'node:test'
import assert from 'node:assert/strict'

import { parseSaved } from './saved.js'

test('anything unreadable or from another version counts as nothing saved', () => {
  const empty = { version: 1, child: null, home: null, homePoint: null, preferences: null, kindergarten: null, grade_1: null }
  for (const text of [null, '', 'not json', '{"version":2,"kindergarten":{"startYear":2026,"answers":{}}}', '[]']) {
    assert.deepEqual(parseSaved(text), empty, String(text))
  }
})

test('well-formed entries are kept and malformed ones dropped', () => {
  const kindergarten = { startYear: 2026, answers: { birthYear: 2023 }, savedOn: '2026-10-15' }
  const parsed = parseSaved(JSON.stringify({
    version: 1,
    home: { streetId: '68134:1', number: '5' },
    kindergarten,
    grade_1: { startYear: 2026, answers: {} },
  }))
  assert.deepEqual(parsed.home, { streetId: '68134:1', number: '5' })
  assert.deepEqual(parsed.kindergarten, kindergarten)
  assert.equal(parsed.grade_1, null, 'first grade without addresses')
  assert.equal(parseSaved(JSON.stringify({ version: 1, home: { streetId: 5 } })).home, null)
})

test('forgetting first-grade answers forgets the home address they saved', async () => {
  const { forgetSaved, getSaved, resetSavedForTests, saveStage } = await import('./saved.js')
  resetSavedForTests()
  saveStage('grade_1', { startYear: 2026, addresses: [], answers: {} }, { home: { streetId: '1:2', number: '5' } })
  saveStage('kindergarten', { startYear: 2026, answers: {} })
  forgetSaved('grade_1')
  assert.equal(getSaved().grade_1, null)
  assert.equal(getSaved().home, null)
  assert.ok(getSaved().kindergarten)
})

test('the child is kept when well-formed, and every saved page shares its birth year', async () => {
  assert.deepEqual(parseSaved(JSON.stringify({ version: 1, child: { birthYear: 2023 } })).child, { birthYear: 2023 })
  assert.equal(parseSaved(JSON.stringify({ version: 1, child: { birthYear: '2023' } })).child, null)

  const { childBirthYear, forgetSaved, getSaved, resetSavedForTests, saveChild, saveStage } = await import('./saved.js')
  resetSavedForTests()
  forgetSaved()
  saveStage('grade_1', { startYear: 2027, addresses: [], answers: {} })
  assert.equal(childBirthYear(), null, 'never guessed from first grade')
  saveStage('kindergarten', { startYear: 2027, answers: { birthYear: 2024 } })
  assert.deepEqual(getSaved().child, { birthYear: 2024 }, 'kindergarten answers give the child')
  saveChild({ birthYear: 2019 })
  assert.equal(childBirthYear(), 2019)
  assert.equal(getSaved().kindergarten.answers.birthYear, 2024, 'saved answers keep their own year')
  forgetSaved('kindergarten')
  assert.equal(childBirthYear(), 2019, 'forgetting one tab keeps the child')
  forgetSaved('grade_1')
  assert.equal(getSaved().child, null, 'forgetting the last tab forgets the child')
  saveChild({ birthYear: 2022 })
  forgetSaved()
  assert.equal(getSaved().child, null)
})

test('answers saved before the child existed give it from the kindergarten answers', () => {
  const saved = parseSaved(JSON.stringify({ version: 1, kindergarten: { startYear: 2027, answers: { birthYear: 2024 } } }))
  assert.deepEqual(saved.child, { birthYear: 2024 })
})

test('the home point is kept apart from the address-table home, and trips start from it', async () => {
  const { forgetSaved, getSaved, resetSavedForTests, saveHomePoint, tripOrigin } = await import('./saved.js')
  resetSavedForTests()
  forgetSaved()
  assert.equal(tripOrigin(), null)
  saveHomePoint({ lat: 42.69, lng: 23.32, label: 'ул. Шипка 5' })
  assert.deepEqual(tripOrigin(), { lat: 42.69, lng: 23.32, label: 'ул. Шипка 5', isHome: true }, 'no location of its own: the home')
  assert.equal(getSaved().home, null)
  saveHomePoint({ lat: 'x', lng: 1 })
  assert.equal(getSaved().homePoint.lat, 42.69, 'a malformed point is ignored')
  forgetSaved()
  assert.equal(getSaved().homePoint, null)
  assert.equal(parseSaved(JSON.stringify({ version: 1, homePoint: { lat: 1 } })).homePoint, null)
})

test('the start year is kept with the birth year', async () => {
  const { childStartYear, resetSavedForTests, forgetSaved, saveChild } = await import('./saved.js')
  resetSavedForTests()
  forgetSaved()
  saveChild({ birthYear: 2022, startYear: 2027 })
  assert.equal(childStartYear(), 2027)
  saveChild({ birthYear: 2022 })
  assert.equal(childStartYear(), 2027, 'the same child keeps its start year')
  saveChild({ birthYear: 2023 })
  assert.equal(childStartYear(), null)
})

test('a point looked up from the saved address goes when the address changes or is forgotten', async () => {
  const { forgetSaved, getSaved, resetSavedForTests, saveHome, saveHomePoint, saveStage } = await import('./saved.js')
  resetSavedForTests()
  forgetSaved()
  saveStage('grade_1', { startYear: 2027, addresses: [], answers: {} }, { home: { streetId: '1:2', number: '5' } })
  saveHomePoint({ lat: 42.7, lng: 23.3, fromHome: '1:2|5' })
  saveHome({ streetId: '1:2', number: '5' })
  assert.ok(getSaved().homePoint, 'same address: kept')
  saveHome({ streetId: '1:2', number: '7' })
  assert.equal(getSaved().homePoint, null, 'another address: dropped')

  saveHome({ streetId: '1:2', number: '5' })
  saveHomePoint({ lat: 42.7, lng: 23.3 })
  saveHome({ streetId: '1:2', number: '5' })
  assert.ok(getSaved().homePoint, 'a home set on the map stays while the address is the same')
  saveHome({ streetId: '9:9', number: '1' })
  assert.equal(getSaved().homePoint, null, 'a newer home address wins over it')
  saveHomePoint({ lat: 42.7, lng: 23.3 })
  forgetSaved('grade_1')
  assert.ok(getSaved().homePoint, 'forgetting the address keeps a home set on the map')
  saveStage('grade_1', { startYear: 2027, addresses: [], answers: {} }, { home: { streetId: '9:9', number: '1' } })
  saveHomePoint({ lat: 42.7, lng: 23.3, fromHome: '9:9|1' })
  forgetSaved('grade_1')
  assert.equal(getSaved().homePoint, null, 'forgetting the address forgets its point')
})

test('a calculator birth year that differs starts a new plan without the old start year', async () => {
  const { forgetSaved, getSaved, resetSavedForTests, saveChild, saveStage } = await import('./saved.js')
  resetSavedForTests()
  forgetSaved()
  saveChild({ birthYear: 2022, startYear: 2027 })
  saveStage('kindergarten', { startYear: 2027, answers: { birthYear: 2022 } })
  assert.deepEqual(getSaved().child, { birthYear: 2022, startYear: 2027 }, 'same year: kept')
  saveStage('kindergarten', { startYear: 2027, answers: { birthYear: 2020 } })
  assert.deepEqual(getSaved().child, { birthYear: 2020 })
})

test('preferences keep only languages and a positive budget', () => {
  assert.deepEqual(parseSaved(JSON.stringify({ version: 1, preferences: { languages: ['english', 3], monthlyBudget: 600 } })).preferences, { languages: ['english'], monthlyBudget: 600 })
  assert.equal(parseSaved(JSON.stringify({ version: 1, preferences: { languages: [], monthlyBudget: -1 } })).preferences, null)
})
