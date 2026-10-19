import assert from 'node:assert/strict'
import test from 'node:test'

import { MATCH_RANK, filterByName, schoolMatchRank, schoolMatchesQuery, sortByNameMatch } from './schoolSearch.js'

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

test('"Saint Sofia" ranks „Света София“ above a saint school that only mentions Sofia', () => {
  const seminary = { id: 1, name_i18n: { bg: 'Софийска духовна семинария „Св. Иван Рилски“' }, resolved_name_i18n: { en: 'Sofia Theological Seminary "St. Ivan Rilski"' } }
  const school = { id: 2, ...svetaSofia }
  assert.equal(schoolMatchRank(school, 'Saint Sofia'), MATCH_RANK.phrase)
  assert.equal(schoolMatchRank(seminary, 'Saint Sofia'), MATCH_RANK.prefix)
  // The seminary is "nearer" as a tie-break, but the better match still wins.
  assert.deepEqual(sortByNameMatch([seminary, school], 'Saint Sofia', (a, b) => a.id - b.id).map(s => s.id), [2, 1])
})
