import test from 'node:test'
import assert from 'node:assert/strict'

import { gradeRanges, schoolLevelLabel } from './levelLabel.js'

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
