import assert from 'node:assert/strict'
import test from 'node:test'

import { calculateAgeGroup } from './education.js'
import { calculateAgeGroup as calculateAgeGroupFromConfig } from './countryConfig.js'

test('Bulgarian fallback maps calendar-year differences 0-2 to nursery', () => {
  assert.equal(calculateAgeGroup(2025, 2025), 'nursery')
  assert.equal(calculateAgeGroup(2025, 2024), 'nursery')
  assert.equal(calculateAgeGroup(2025, 2023), 'nursery')
  assert.equal(calculateAgeGroup(2025, 2022), 'first')
})

test('country education config maps a two-year difference to nursery', () => {
  const config = {
    education_config: {
      age_groups: [
        { key: 'nursery', min_diff: 0, max_diff: 2 },
        { key: 'first', min_diff: 3, max_diff: 3 },
      ],
    },
  }

  assert.equal(calculateAgeGroupFromConfig(config, 2025, 2023), 'nursery')
})
