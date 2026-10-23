import test from 'node:test'
import assert from 'node:assert/strict'

import { parseSaved } from './saved.js'

test('anything unreadable or from another version counts as nothing saved', () => {
  const empty = { version: 1, child: null, home: null, kindergarten: null, grade_1: null }
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
  saveChild({ birthYear: 2023 })
  assert.equal(childBirthYear(), 2023)
  assert.equal(getSaved().kindergarten.answers.birthYear, 2023, 'the kindergarten answers follow')
  saveChild({ birthYear: 2019 })
  assert.equal(getSaved().kindergarten.answers.birthYear, 2023, 'but only to a kindergarten age')
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
