import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getLanguageFocusPairs,
  normalizeSchool,
  normalizeSchoolAttributes,
} from './schoolAttributes.js'

// Merging/parsing now lives in backend/app/utils/school_attributes.py — see
// backend/tests/test_school_attributes.py. These cover locale selection only.

const ATTRIBUTES = { class_size: 16, has_canteen: true, teaching_approach: [] }
const ATTRIBUTES_I18N = {
  bg: { facilities: ['Библиотека'], special_programs: ['Спортна програма'] },
  en: { facilities: ['Library'], special_programs: ['Sports program'] },
}

test('picks the requested locale', () => {
  const normalized = normalizeSchoolAttributes(ATTRIBUTES, ATTRIBUTES_I18N, 'en')

  assert.deepEqual(normalized.facilities, ['Library'])
  assert.equal(normalized.class_size, 16)
  assert.equal(normalized.has_canteen, true)
})

test('defaults to bulgarian', () => {
  const normalized = normalizeSchoolAttributes(ATTRIBUTES, ATTRIBUTES_I18N, 'bg')

  assert.deepEqual(normalized.facilities, ['Библиотека'])
})

test('treats en-US as english', () => {
  const normalized = normalizeSchoolAttributes(ATTRIBUTES, ATTRIBUTES_I18N, 'en-US')

  assert.deepEqual(normalized.facilities, ['Library'])
})

test('falls back to localStorage when no locale is passed', () => {
  const previousStorage = global.localStorage
  global.localStorage = { getItem: () => 'en' }

  const normalized = normalizeSchoolAttributes(ATTRIBUTES, ATTRIBUTES_I18N)

  assert.deepEqual(normalized.facilities, ['Library'])
  global.localStorage = previousStorage
})

test('tolerates a missing attributes_i18n payload', () => {
  const normalized = normalizeSchoolAttributes(ATTRIBUTES, undefined, 'en')

  assert.equal(normalized.class_size, 16)
  assert.equal(normalized.facilities, undefined)
})

test('tolerates missing attributes entirely', () => {
  assert.deepEqual(normalizeSchoolAttributes(null, null, 'bg'), {})
})

test('normalizeSchool flattens attributes_i18n onto the school', () => {
  const school = normalizeSchool(
    { id: 1, attributes: ATTRIBUTES, attributes_i18n: ATTRIBUTES_I18N },
    'en'
  )

  assert.deepEqual(school.attributes.special_programs, ['Sports program'])
  assert.equal(school.id, 1)
})

test('getLanguageFocusPairs includes every attributes_i18n locale for filter counts', () => {
  const pairs = getLanguageFocusPairs({
    attributes: {
      language_focus: [{ language: 'English', level: 'mother_tongue' }],
    },
    attributes_i18n: {
      bg: { language_focus: [{ language: 'Английски', level: 'mother_tongue' }] },
      en: { language_focus: ['German:early_foreign'] },
    },
  })

  assert.deepEqual(
    [...pairs].sort(),
    ['English:mother_tongue', 'German:early_foreign', 'Английски:mother_tongue'].sort()
  )
})
