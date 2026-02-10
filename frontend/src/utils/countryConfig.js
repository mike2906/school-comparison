/**
 * Country config utility functions.
 * All functions take a config object from useCountry().config
 */

/**
 * Get the localized label for an age group key.
 */
export function getAgeGroupLabel(config, key, language) {
  if (!config?.education_config?.age_groups) return key
  const ag = config.education_config.age_groups.find(a => a.key === key)
  return ag?.label_i18n?.[language] || ag?.label_i18n?.['en'] || key
}

/**
 * Get age group keys for a category ('kindergarten' or 'school').
 */
export function getAgeGroupsByCategory(config, category) {
  if (!config?.education_config?.age_groups) return []
  return config.education_config.age_groups
    .filter(ag => {
      if (!ag.category) return false
      if (Array.isArray(ag.category)) {
        return ag.category.includes(category)
      }
      return ag.category === category
    })
    .map(ag => ag.key)
}

/**
 * Get all age group keys in order.
 */
export function getAgeGroupKeys(config) {
  if (!config?.education_config?.age_groups) return []
  return config.education_config.age_groups.map(ag => ag.key)
}

/**
 * Calculate age group from target year and birth year using config.
 */
export function calculateAgeGroup(config, targetYear, birthYear) {
  if (!config?.education_config?.age_groups) return null
  const diff = targetYear - birthYear
  for (const ag of config.education_config.age_groups) {
    if (ag.min_diff <= diff && diff <= ag.max_diff) {
      return ag.key
    }
  }
  return null
}

/**
 * Get the localized label for an education level key.
 */
export function getEducationLevelLabel(config, key, language) {
  if (!config?.education_config?.education_levels) return key
  const el = config.education_config.education_levels.find(e => e.key === key)
  return el?.label_i18n?.[language] || el?.label_i18n?.['en'] || key
}

/**
 * Get the localized label for an exam type key.
 */
export function getExamTypeLabel(config, key, language) {
  if (!config?.education_config?.exam_types) return key
  const et = config.education_config.exam_types.find(e => e.key === key)
  return et?.label_i18n?.[language] || et?.label_i18n?.['en'] || key
}

/**
 * Get the localized label for a shift key.
 */
export function getShiftLabel(config, key, language) {
  if (!config?.education_config?.shifts) return key
  const s = config.education_config.shifts.find(s => s.key === key)
  return s?.label_i18n?.[language] || s?.label_i18n?.['en'] || key
}

/**
 * Get the localized label for a school type key.
 */
export function getSchoolTypeLabel(config, key, language) {
  if (!config?.education_config?.school_types) return key
  const st = config.education_config.school_types.find(s => s.key === key)
  return st?.label_i18n?.[language] || st?.label_i18n?.['en'] || key
}

/**
 * Convert a grade to admission points using the country config.
 */
export function gradeToPoints(config, grade) {
  const table = config?.education_config?.grade_to_points_table
  if (!table) return 0
  const thresholds = Object.entries(table)
    .map(([k, v]) => [parseFloat(k), v])
    .sort((a, b) => b[0] - a[0])
  for (const [threshold, points] of thresholds) {
    if (grade >= threshold) return points
  }
  return 0
}

/**
 * Get exam subjects from config.
 */
export function getExamSubjects(config) {
  return config?.education_config?.exam_subjects || []
}
