import test from 'node:test'
import assert from 'node:assert/strict'

import { gradeRanges, schoolLevelLabel, schoolTypeLabel } from './levelLabel.js'

const t = (key, vars) => (vars ? `${key}(${Object.values(vars).join('|')})` : key)
const school = (groups, level = 'upper_secondary') => ({
  education_level: level,
  locations: [{ age_groups: groups }],
})

test('gradeRanges merges contiguous bands', () => {
  assert.deepEqual(gradeRanges(new Set(['grade_1_4', 'grade_5_7', 'grade_8_12'])), [[1, 12]])
  assert.deepEqual(gradeRanges(new Set(['grade_1_4', 'grade_8_12'])), [[1, 4], [8, 12]])
  assert.deepEqual(gradeRanges(new Set(['first'])), [])
})

test('schoolLevelLabel describes what is offered, not the stored category', () => {
  assert.equal(schoolLevelLabel(school(['grade_1_4', 'grade_5_7', 'grade_8_12']), t), 'levels.grades(1–12)')
  assert.equal(schoolLevelLabel(school(['grade_1_4', 'grade_5_7'], 'lower_secondary'), t), 'levels.grades(1–7)')
  assert.equal(schoolLevelLabel(school(['nursery', 'first', 'preschool'], 'kindergarten'), t), 'educationLevels.kindergarten')
  assert.equal(
    schoolLevelLabel(school(['first', 'preschool', 'grade_1_4']), t),
    'educationLevels.kindergarten · levels.grades(1–4)'
  )
  assert.equal(schoolLevelLabel(school(['preschool'], 'primary'), t), 'ageGroups.preschool')
  assert.equal(schoolLevelLabel({ education_level: 'primary', locations: [] }, t), 'educationLevels.primary')
})

test('schoolLevelLabel uses the country config for groups and grade numbers', () => {
  const config = {
    education_config: {
      age_groups: [
        { key: 'kita', category: 'kindergarten', min_diff: 1, max_diff: 5 },
        { key: 'vorschule', category: ['kindergarten', 'school'], min_diff: 6, max_diff: 6 },
        { key: 'grundschule', category: 'school', min_diff: 6, max_diff: 9 },
        { key: 'gymnasium', category: 'school', min_diff: 10, max_diff: 17 },
      ],
    },
  }
  assert.equal(schoolLevelLabel(school(['grundschule', 'gymnasium']), t, config), 'levels.grades(1–12)')
  assert.equal(schoolLevelLabel(school(['kita']), t, config), 'educationLevels.kindergarten')
  // Bulgarian keys mean nothing in this config: fall back to the stored level.
  assert.equal(schoolLevelLabel(school(['grade_1_4']), t, config), 'educationLevels.upper_secondary')
})

test('schoolTypeLabel uses kindergarten forms for kindergartens', () => {
  assert.equal(schoolTypeLabel({ school_type: 'state', education_level: 'kindergarten' }, t), 'schoolTypesKindergarten.state')
  assert.equal(schoolTypeLabel({ school_type: 'state', education_level: 'primary' }, t), 'schoolTypes.state')
  assert.equal(schoolTypeLabel({ school_type: 'international', education_level: 'kindergarten' }, t, 'private'), 'schoolTypesKindergarten.private')
  assert.equal(schoolTypeLabel({}, t), '')
})

test('schoolTypeLabel treats nurseries like kindergartens', () => {
  assert.equal(schoolTypeLabel({ school_type: 'state', education_level: 'nursery' }, t), 'schoolTypesKindergarten.state')
})
