/**
 * Education system utilities.
 *
 * These functions delegate to countryConfig.js when a config is provided.
 * When called without config, they fall back to the default Bulgaria logic
 * for backwards compatibility.
 */
import { calculateAgeGroup as configCalculateAgeGroup } from './countryConfig.js'

// Default Bulgaria age groups for backwards compatibility
const BG_AGE_GROUPS = [
  { key: 'nursery', min_diff: 0, max_diff: 2 },
  { key: 'first', min_diff: 3, max_diff: 3 },
  { key: 'second', min_diff: 4, max_diff: 4 },
  { key: 'third', min_diff: 5, max_diff: 5 },
  { key: 'preschool', min_diff: 6, max_diff: 6 },
  { key: 'grade_1_4', min_diff: 7, max_diff: 10 },
  { key: 'grade_5_7', min_diff: 11, max_diff: 13 },
  { key: 'grade_8_12', min_diff: 14, max_diff: 18 },
]

const BG_FALLBACK_CONFIG = {
  education_config: {
    age_groups: BG_AGE_GROUPS,
  },
}

/**
 * Calculate age group based on target admission year and birth year.
 *
 * @param {number} targetYear
 * @param {number} birthYear
 * @param {object} [config] - Country config from useCountry(). Uses Bulgaria default if omitted.
 * @returns {string|null}
 */
export function calculateAgeGroup(targetYear, birthYear, config = null) {
  return configCalculateAgeGroup(config || BG_FALLBACK_CONFIG, targetYear, birthYear)
}

/**
 * Age group keys for iteration.
 * @type {string[]}
 */
export const AGE_GROUP_KEYS = BG_AGE_GROUPS.map(ag => ag.key)
