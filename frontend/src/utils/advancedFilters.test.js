import test from 'node:test'
import assert from 'node:assert/strict'

import { matchesAdvancedFilters, matchesSchoolType } from './advancedFilters.js'

const school = {
  attributes: {
    language_focus: [{ language: 'english', level: 'intensive' }],
    filter_tags: { facilities: ['library', 'cafeteria'], special_programs: ['music_program'] },
  },
}

test('matchesAdvancedFilters: any value within a group, all groups', () => {
  assert.equal(matchesAdvancedFilters(school, {}), true)
  assert.equal(matchesAdvancedFilters(school, { facilities: ['library', 'pool'] }), true)
  assert.equal(matchesAdvancedFilters(school, { facilities: ['pool'] }), false)
  assert.equal(matchesAdvancedFilters(school, { facilities: ['library'], specialPrograms: ['sports_program'] }), false)
  assert.equal(matchesAdvancedFilters(school, { languageFocus: ['english:intensive'] }), true)
  assert.equal(matchesAdvancedFilters(school, { languageFocus: ['german:intensive'] }), false)
})

test('matchesAdvancedFilters can ignore one group for per-option counts', () => {
  const selected = { facilities: ['pool'], languageFocus: ['german:intensive'] }
  assert.equal(matchesAdvancedFilters(school, selected, 'facilities'), false)
  assert.equal(matchesAdvancedFilters(school, { facilities: ['pool'] }, 'facilities'), true)
  assert.equal(matchesAdvancedFilters(school, { languageFocus: ['german:intensive'] }, 'languageFocus'), true)
})

test('matchesSchoolType: private includes international schools', () => {
  assert.equal(matchesSchoolType({ school_type: 'international' }, 'private'), true)
  assert.equal(matchesSchoolType({ school_type: 'private' }, 'private'), true)
  assert.equal(matchesSchoolType({ school_type: 'state' }, 'private'), false)
  assert.equal(matchesSchoolType({ school_type: 'international' }, 'state'), false)
  assert.equal(matchesSchoolType({ school_type: 'state' }, null), true)
})
