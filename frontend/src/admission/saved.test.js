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
