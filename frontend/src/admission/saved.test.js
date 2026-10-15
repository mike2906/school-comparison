import test from 'node:test'
import assert from 'node:assert/strict'

import { parseSaved } from './saved.js'

test('anything unreadable or from another version counts as nothing saved', () => {
  const empty = { version: 1, home: null, kindergarten: null, grade_1: null }
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
