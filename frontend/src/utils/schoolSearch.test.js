import assert from 'node:assert/strict'
import test from 'node:test'

import { filterByName, schoolMatchesQuery } from './schoolSearch.js'

const svetaSofia = {
  name_i18n: { bg: '"Частно основно училище Света София" ЕООД' },
  resolved_name_i18n: { bg: 'Частно основно училище „Света София“', en: 'Private Primary School "St. Sofia"' },
}
const smg = { name_i18n: { bg: 'Софийска математическа гимназия "Паисий Хилендарски"' } }

test('Latin saint words and the "Sofia" spelling find Света София', () => {
  for (const query of ['Sofiy', 'Saints Sofia', 'Saint Sofia', 'Sveta Sofia', 'Sveta Sofiya', 'St Sofia', 'Св. София', 'свети софия']) {
    assert.equal(schoolMatchesQuery(svetaSofia, query), true, query)
  }
})

test('a saint word needs a saint in the name', () => {
  assert.equal(schoolMatchesQuery(smg, 'Saint Paisiy'), false)
  assert.equal(schoolMatchesQuery(smg, 'SMG'), true)
})

test('a W in a Latin brand matches В (UWEKIND for Увекинд)', () => {
  const uvekind = { name_i18n: { bg: '"ЧАСТНО СРЕДНО УЧИЛИЩЕ УВЕКИНД" ООД' } }
  assert.equal(schoolMatchesQuery(uvekind, 'Uwekind'), true)
  assert.equal(schoolMatchesQuery({ name_i18n: { bg: 'ПГ гр. Ст. Загора София' } }, 'Saint Sofia'), false)
})

test('a name no school matches here falls back to the schools the API found', () => {
  const schools = [
    { id: 149, name_i18n: { bg: '"ЧАСТНА ДЕТСКА ГРАДИНА ТАТКОВА ГРАДИНА" ЕООД' } },
    { id: 7, name_i18n: { bg: 'ДГ №7' } },
  ]
  // tatkovagradina.eu: the brand is only in the web address, which the API matches.
  assert.deepEqual(filterByName(schools, 'tatkovagradina', new Set([149])).map(s => s.id), [149])
  assert.deepEqual(filterByName(schools, 'tatkovagradina', new Set()), [])
  // A name that matches here keeps the browser's own list.
  assert.deepEqual(filterByName(schools, 'ДГ №7', new Set([149])).map(s => s.id), [7])
})
