import test from 'node:test'
import assert from 'node:assert/strict'

import { canonicalLanguagePair, languageKey, languageLabel } from './languages.js'

test('languageKey recognises English, Bulgarian and code spellings', () => {
  assert.equal(languageKey('English'), 'english')
  assert.equal(languageKey('английски език'), 'english')
  assert.equal(languageKey('Български'), 'bulgarian')
  assert.equal(languageKey('bg'), 'bulgarian')
  assert.equal(languageKey('Information Technology'), null)
})

test('languageLabel translates known languages and keeps unknown values', () => {
  const t = (key) => `T:${key}`
  assert.equal(languageLabel('Bulgarian', t), 'T:languages.bulgarian')
  assert.equal(languageLabel('немски', t), 'T:languages.german')
  assert.equal(languageLabel('latin', t), 'Latin')
  assert.equal(languageLabel('', t), '')
})

test('canonicalLanguagePair merges spellings and drops non-languages', () => {
  assert.equal(canonicalLanguagePair('английски език:intensive'), 'english:intensive')
  assert.equal(canonicalLanguagePair('English'), 'english')
  assert.equal(canonicalLanguagePair('Information Technology:enrichment'), null)
  assert.equal(canonicalLanguagePair(''), null)
})
