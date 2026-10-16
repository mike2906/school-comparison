import assert from 'node:assert/strict'
import test from 'node:test'

import { schoolMatchesQuery } from './schoolSearch.js'

const svetaSofia = {
  name_i18n: { bg: '"Частно основно училище Света София" ЕООД' },
  resolved_name_i18n: { bg: 'Частно основно училище „Света София“', en: 'Private Primary School "St. Sofia"' },
}
const smg = { name_i18n: { bg: 'Софийска математическа гимназия "Паисий Хилендарски"' } }

test('Latin saint words and the "Sofia" spelling find Света София', () => {
  for (const query of ['Saint Sofia', 'Sveta Sofia', 'Sveta Sofiya', 'St Sofia', 'Св. София', 'свети софия']) {
    assert.equal(schoolMatchesQuery(svetaSofia, query), true, query)
  }
})

test('a saint word needs a saint in the name', () => {
  assert.equal(schoolMatchesQuery(smg, 'Saint Paisiy'), false)
  assert.equal(schoolMatchesQuery(smg, 'SMG'), true)
})
