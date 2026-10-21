import assert from 'node:assert/strict'
import test from 'node:test'

import { planningYear } from './ageSelection.js'
import {
  filterSearchParams,
  nextFilterState,
  normalizeAvailableFilters,
  readFiltersFromUrl,
} from './searchFilters.js'

test('readFiltersFromUrl reads repeated list params', () => {
  const filters = readFiltersFromUrl(new URLSearchParams(
    'age_group=preschool&school_type=international&target_year=2027&birth_year=2022'
    + '&include_crossover=true&special_programs=music_program&special_programs=arts_program&facilities=library&facilities=cafeteria'
  ))
  assert.equal(filters.ageGroup, 'preschool')
  assert.equal(filters.schoolType, 'private')
  assert.equal(filters.targetYear, 2027)
  assert.equal(filters.birthYear, 2022)
  assert.equal(filters.includeCrossover, true)
  assert.deepEqual(filters.specialPrograms, ['music_program', 'arts_program'])
  assert.deepEqual(filters.facilities, ['library', 'cafeteria'])
})

test('readFiltersFromUrl defaults the target year to the September being planned for', () => {
  const filters = readFiltersFromUrl(new URLSearchParams(''))
  assert.equal(filters.targetYear, planningYear())
  assert.equal(filters.birthYear, null)
  assert.deepEqual(filters.languageFocus, [])
})

test('nextFilterState drops the education level outside preschool', () => {
  const start = { ageGroup: 'preschool', educationLevel: 'kindergarten', includeCrossover: false }
  assert.deepEqual(nextFilterState(start, { ageGroup: 'primary' }), {
    ageGroup: 'primary', educationLevel: null, includeCrossover: false,
  })
  assert.equal(nextFilterState(start, { includeCrossover: true }).educationLevel, null)
  assert.equal(nextFilterState(start, { schoolType: 'state' }).educationLevel, 'kindergarten')
})

test('filterSearchParams rewrites only the changed filters', () => {
  const current = new URLSearchParams('age_group=preschool&education_level=kindergarten&view=map-only&facilities=library')
  const filters = { ageGroup: 'preschool', educationLevel: 'kindergarten', includeCrossover: false }
  const newFilters = { ageGroup: 'primary' }
  const params = filterSearchParams(current, newFilters, nextFilterState(filters, newFilters))
  assert.equal(params.get('age_group'), 'primary')
  assert.equal(params.has('education_level'), false)
  assert.equal(params.get('view'), 'map-only')
  assert.deepEqual(params.getAll('facilities'), ['library'])
  assert.equal(current.get('age_group'), 'preschool')
})

test('normalizeAvailableFilters fills missing groups', () => {
  const result = normalizeAvailableFilters({ facilities: ['library'] })
  assert.deepEqual(result.facilities, ['library'])
  assert.deepEqual(result.special_programs, [])
  assert.deepEqual(result.language_focus_pairs, [])
})
