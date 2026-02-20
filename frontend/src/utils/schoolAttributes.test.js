import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeSchoolAttributes } from './schoolAttributes.js'

test('class size parses when student/class context exists', () => {
  const normalized = normalizeSchoolAttributes({
    extracted: {
      class_size: 'Up to 16 students per class'
    }
  })

  assert.equal(normalized.class_size, 16)
})

test('class size does not parse unrelated numeric text', () => {
  const normalized = normalizeSchoolAttributes({
    extracted: {
      class_size: 'Grades 1-4 program'
    }
  })

  assert.equal(normalized.class_size, undefined)
})

test('existing numeric class size remains usable', () => {
  const normalized = normalizeSchoolAttributes({
    class_size: 18
  })

  assert.equal(normalized.class_size, 18)
})

test('prefers extracted_i18n values when language is en', () => {
  const previousStorage = global.localStorage
  global.localStorage = { getItem: () => 'en' }

  const normalized = normalizeSchoolAttributes({
    extracted: {
      facilities: ['Библиотека']
    },
    extracted_i18n: {
      en: {
        facilities: ['Library']
      }
    }
  })

  assert.deepEqual(normalized.facilities, ['Library'])
  global.localStorage = previousStorage
})

test('falls back to bg extracted values when extracted_i18n is missing', () => {
  const previousStorage = global.localStorage
  global.localStorage = { getItem: () => 'en' }

  const normalized = normalizeSchoolAttributes({
    extracted: {
      facilities: ['Библиотека']
    },
    extracted_i18n: {}
  })

  assert.deepEqual(normalized.facilities, ['Библиотека'])
  global.localStorage = previousStorage
})
