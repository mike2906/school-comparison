import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getAdmissionStatusKey,
  getAfterSchoolEvidence,
  getCanonicalAmenityEvidence,
  getCanonicalAmenityFlags,
  getFilterTags,
  getLanguageFocusPairs,
  hasDisplayEvidence,
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

test('getFilterTags prefers canonical tags over localized free text', () => {
  const attributes = {
    facilities: ['Библиотека', 'Училищен транспорт'],
    filter_tags: { facilities: ['library', 'transportation'] },
  }

  assert.deepEqual(getFilterTags(attributes, 'facilities'), ['library', 'transportation'])
})

test('getFilterTags supports legacy canonical payloads and missing groups', () => {
  assert.deepEqual(getFilterTags({ facilities: ['cafeteria'] }, 'facilities'), ['cafeteria'])
  assert.deepEqual(getFilterTags({}, 'facilities'), [])
})

test('canonical amenity flags use filter tags instead of localized free text', () => {
  const attributes = {
    facilities: ['Библиотека', 'Училищен транспорт'],
    special_programs: ['Целодневна организация', 'Осигурено хранене'],
    filter_tags: {
      facilities: ['computer_lab', 'library', 'sports_facilities', 'transportation'],
      special_programs: ['extended_day', 'meals_provided'],
    },
  }

  assert.deepEqual(getCanonicalAmenityFlags(attributes), {
    meals: true,
    transport: true,
    extended: true,
    library: true,
    computerLab: true,
    sportsFacilities: true,
  })
})

test('canonical amenity flags do not treat arbitrary display text as tags', () => {
  const attributes = {
    facilities: ['Library'],
    special_programs: ['Food Revolution Day'],
    filter_tags: { facilities: [], special_programs: [] },
  }

  assert.deepEqual(getCanonicalAmenityFlags(attributes), {
    meals: false,
    transport: false,
    extended: false,
    library: false,
    computerLab: false,
    sportsFacilities: false,
  })
})

test('canonical amenity flags ignore retired unproduced boolean aliases', () => {
  const attributes = {
    transportation_available: true,
    after_school_care: true,
    filter_tags: { facilities: [], special_programs: [] },
  }

  const flags = getCanonicalAmenityFlags(attributes)
  assert.equal(flags.transport, false)
  assert.equal(flags.extended, false)
})

test('amenity evidence distinguishes explicit false from missing data', () => {
  assert.deepEqual(getCanonicalAmenityEvidence({}), {
    meals: null,
    transport: null,
    extended: null,
    accessibility: null,
  })
  assert.deepEqual(
    getCanonicalAmenityEvidence(
      { has_canteen: false, facilities: [], filter_tags: {} },
      false
    ),
    {
      meals: false,
      transport: null,
      extended: false,
      accessibility: null,
    }
  )
})

test('amenity evidence keeps supported positive claims', () => {
  const evidence = getCanonicalAmenityEvidence({
    facilities: ['accessible'],
    filter_tags: {
      facilities: ['transportation'],
      special_programs: ['meals_provided', 'extended_day'],
    },
  })

  assert.deepEqual(evidence, {
    meals: true,
    transport: true,
    extended: true,
    accessibility: true,
  })
})

test('after-school evidence is unknown unless a location records a boolean', () => {
  assert.equal(getAfterSchoolEvidence([]), null)
  assert.equal(getAfterSchoolEvidence([{ age_group_shifts: [{}] }]), null)
  assert.equal(
    getAfterSchoolEvidence([{ age_group_shifts: [{ has_organised_groups: false }] }]),
    false
  )
  assert.equal(
    getAfterSchoolEvidence([
      { age_group_shifts: [{ has_organised_groups: false }] },
      { age_group_shifts: [{ has_organised_groups: true }] },
    ]),
    true
  )
})

test('admission status omits unknown and unrecognized values', () => {
  assert.equal(getAdmissionStatusKey(null), null)
  assert.equal(getAdmissionStatusKey('unknown'), null)
  assert.equal(getAdmissionStatusKey('Accepting applications'), 'accepting')
  assert.equal(getAdmissionStatusKey('waitlist'), 'waitlist')
  assert.equal(getAdmissionStatusKey('closed'), 'full')
})

test('display evidence treats explicit false as evidence but empty values as absent', () => {
  assert.equal(hasDisplayEvidence(false), true)
  assert.equal(hasDisplayEvidence(0), true)
  assert.equal(hasDisplayEvidence('  '), false)
  assert.equal(hasDisplayEvidence([]), false)
  assert.equal(hasDisplayEvidence({ nested: [] }), false)
  assert.equal(hasDisplayEvidence({ nested: ['value'] }), true)
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
