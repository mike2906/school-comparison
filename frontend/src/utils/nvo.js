const EXAM_TYPE_CONFIG = {
  primary: { examType: 'nvo_4', gradeKey: 'schoolCard.nvo.grade4' },
  lower_secondary: { examType: 'nvo_7', gradeKey: 'schoolCard.nvo.grade7' },
  upper_secondary: { examType: 'nvo_10', gradeKey: 'schoolCard.nvo.grade10' },
}

const GRADE_ORDER = { nvo_4: 1, nvo_7: 2, nvo_10: 3 }

// The exam that tells a parent about the stage they are choosing for: grades 5–7 end with
// the 7th-grade NVO (the one that decides 8th-grade admission), 8–12 with the 10th-grade one.
const AGE_GROUP_EXAM_TYPE = { grade_1_4: 'nvo_4', grade_5_7: 'nvo_7', grade_8_12: 'nvo_10' }
const EXAM_CONFIG_BY_TYPE = Object.fromEntries(
  Object.values(EXAM_TYPE_CONFIG).map(config => [config.examType, config])
)

/** The NVO exam type for an age group, or null (kindergarten groups, all ages). */
export function examTypeForAgeGroup(ageGroup) {
  return AGE_GROUP_EXAM_TYPE[ageGroup] || null
}

// Results of different exams are not comparable: the 4th-grade NVO usually scores far
// higher than the 7th or 10th. A ranking or a comparison uses one exam for every school.
const RANKING_EXAM_PREFERENCE = ['nvo_7', 'nvo_10', 'nvo_4']

/**
 * The one exam a list of schools is ranked by: the searched stage's, else the 4th-grade
 * exam for the preschool year (children often continue into 1st grade at that school),
 * else the 7th-grade exam, which decides 8th-grade admission.
 */
export function rankingExamType(ageGroup) {
  return examTypeForAgeGroup(ageGroup) || (ageGroup === 'preschool' ? 'nvo_4' : 'nvo_7')
}

/**
 * The one exam a side-by-side comparison shows: the searched stage's when any of the
 * schools has it, else the exam most
 * of the compared schools have (ties go to the 7th, then 10th, then 4th grade). Null when
 * none of them has results.
 */
export function comparisonExamType(schools = [], ageGroup = null) {
  const counts = Object.fromEntries(RANKING_EXAM_PREFERENCE.map(type => [type, 0]))
  schools.forEach((school) => {
    const types = new Set((school?.exam_results || [])
      .filter(result => isAverageMetric(result.metric))
      .map(result => result.exam_type))
    types.forEach((type) => {
      if (type in counts) counts[type] += 1
    })
  })
  // The searched stage's exam only when one of the schools has it; otherwise every NVO
  // row would be empty.
  const preferred = examTypeForAgeGroup(ageGroup)
  if (preferred && counts[preferred] > 0) return preferred
  const best = RANKING_EXAM_PREFERENCE.reduce((top, type) => (counts[type] > counts[top] ? type : top))
  return counts[best] > 0 ? best : null
}

/** "7th grade" for nvo_7, or the raw type when unknown. */
export function examGradeLabel(examType, t) {
  const config = EXAM_CONFIG_BY_TYPE[examType]
  return config && t ? t(config.gradeKey) : examType
}

function hasExamResults(examResults, examType, requireBothSubjects) {
  const subjects = new Set(
    examResults
      .filter(result => result.exam_type === examType && isAverageMetric(result.metric))
      .map(result => getNvoSubjectKey(result.subject))
      .filter(Boolean)
  )
  return requireBothSubjects ? subjects.has('math') && subjects.has('bulgarian') : subjects.size > 0
}

export function isAverageMetric(metric) {
  return String(metric || '').toLowerCase().includes('average')
}

export function getNvoSubjectKey(subject) {
  const normalized = String(subject || '').toLowerCase()

  if (normalized.includes('math')) {
    return 'math'
  }
  if (normalized.includes('bulgarian') || normalized.includes('bulg')) {
    return 'bulgarian'
  }

  return null
}

export function getExamTypeForEducationLevel(educationLevel) {
  return EXAM_TYPE_CONFIG[educationLevel]?.examType || 'nvo_7'
}

export function getAvailableExamTypes(examResults = []) {
  const types = new Set()

  examResults.forEach((result) => {
    // NVO only: ДЗИ grades (exam_type "dzi") have their own section.
    if (result.exam_type in GRADE_ORDER && isAverageMetric(result.metric)) {
      types.add(result.exam_type)
    }
  })

  return Array.from(types).sort((a, b) => (GRADE_ORDER[a] || 0) - (GRADE_ORDER[b] || 0))
}

export function getExamTypeLabel(examType, t) {
  const labelMap = {
    nvo_4: t ? t('schools.nvo4Label') : '4th Grade NVO',
    nvo_7: t ? t('schools.nvo7Label') : '7th Grade NVO',
    nvo_10: t ? t('schools.nvo10Label') : '10th Grade NVO',
  }

  return labelMap[examType] || examType
}

export function getBenchmarkToneClasses(tone) {
  if (tone === 'above') {
    return {
      textClass: 'text-emerald-700',
      badgeClass: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    }
  }
  if (tone === 'below') {
    return {
      textClass: 'text-red-700',
      badgeClass: 'border-red-200 bg-red-50 text-red-700',
    }
  }

  return {
    textClass: 'text-amber-700',
    badgeClass: 'border-amber-200 bg-amber-50 text-amber-700',
  }
}

export function getBenchmarkComparison({ examType, year, subjectKey, value, examAverages, tolerance = 5 }) {
  if (!examType || !year || !subjectKey || value == null || !examAverages?.by_year?.[examType]) {
    return null
  }

  const benchmarkForYear = (
    examAverages.by_year[examType][String(year)] ||
    examAverages.by_year[examType][year] ||
    null
  )
  const benchmarkValue = benchmarkForYear?.[subjectKey]

  if (benchmarkValue == null) {
    return null
  }

  const diff = Number(value) - Number(benchmarkValue)
  const tone = diff >= tolerance ? 'above' : diff <= -tolerance ? 'below' : 'near'

  return {
    tone,
    diff,
    benchmarkValue: Number(benchmarkValue),
    ...getBenchmarkToneClasses(tone),
  }
}

function computeAverage(items, maxYears) {
  const sorted = [...items].sort((a, b) => b.year - a.year).slice(0, maxYears)
  const values = sorted.map(item => Number(item.value)).filter(value => !Number.isNaN(value))

  if (values.length === 0) {
    return null
  }

  return {
    average: values.reduce((sum, value) => sum + value, 0) / values.length,
    years: sorted.map(item => item.year),
  }
}

function getLatestResult(items) {
  return [...items].sort((a, b) => b.year - a.year)[0] || null
}

export function getNvoDetail(
  school,
  t,
  { minimumYearsForAverage = 3, maxAverageYears = 5, requireCompleteSubjects = false, ageGroup = null, examType = null } = {}
) {
  const examResults = school.exam_results || []
  if (examResults.length === 0) {
    return null
  }

  // A given exam type is used strictly (null without it). Otherwise: the searched age
  // group's exam when the school has it, else its highest stage's.
  const preferredType = examTypeForAgeGroup(ageGroup)
  const examConfig = examType
    ? EXAM_CONFIG_BY_TYPE[examType]
    : preferredType && hasExamResults(examResults, preferredType, requireCompleteSubjects)
      ? EXAM_CONFIG_BY_TYPE[preferredType]
      : EXAM_TYPE_CONFIG[school.education_level]
  if (!examConfig) {
    return null
  }

  const subjects = {
    bulgarian: [],
    math: [],
  }

  examResults.forEach((result) => {
    if (result.exam_type !== examConfig.examType || !isAverageMetric(result.metric)) {
      return
    }

    const subjectKey = getNvoSubjectKey(result.subject)
    if (!subjectKey) {
      return
    }

    subjects[subjectKey].push(result)
  })

  if (
    (subjects.bulgarian.length === 0 && subjects.math.length === 0) ||
    (requireCompleteSubjects && (subjects.bulgarian.length === 0 || subjects.math.length === 0))
  ) {
    return null
  }

  const bulgarianYears = new Set(subjects.bulgarian.map(item => item.year))
  const mathYears = new Set(subjects.math.map(item => item.year))
  const hasAverage = (
    bulgarianYears.size >= minimumYearsForAverage &&
    mathYears.size >= minimumYearsForAverage
  )

  const mathData = hasAverage ? computeAverage(subjects.math, maxAverageYears) : null
  const bgData = hasAverage ? computeAverage(subjects.bulgarian, maxAverageYears) : null

  const latestMathResult = getLatestResult(subjects.math)
  const latestBgResult = getLatestResult(subjects.bulgarian)
  const latestMath = latestMathResult ? Number(latestMathResult.value) : null
  const latestBg = latestBgResult ? Number(latestBgResult.value) : null
  const schoolAverageCombined = (
    mathData?.average != null && bgData?.average != null
      ? (mathData.average + bgData.average) / 2
      : null
  )
  // Combine the two subjects from the same year only: the newest year can be missing a
  // subject, and averaging 2025 maths with 2024 Bulgarian gives a result that never existed.
  const bgByYear = new Map(subjects.bulgarian.map(item => [item.year, Number(item.value)]))
  const latestCommon = [...subjects.math]
    .sort((a, b) => b.year - a.year)
    .find(item => bgByYear.has(item.year))
  const latestCombinedYear = latestCommon?.year ?? null
  const latestCombined = latestCommon
    ? (Number(latestCommon.value) + bgByYear.get(latestCommon.year)) / 2
    : null
  const colorClass = schoolAverageCombined == null
    ? 'text-neutral-600'
    : schoolAverageCombined >= 75
      ? 'text-emerald-500'
      : schoolAverageCombined >= 60
        ? 'text-amber-500'
        : 'text-red-500'

  const yearsUsed = hasAverage ? [...new Set([...(mathData?.years || []), ...(bgData?.years || [])])] : []
  const latestYear = Math.max(latestMathResult?.year || 0, latestBgResult?.year || 0) || null

  return {
    examType: examConfig.examType,
    gradeLabel: examGradeLabel(examConfig.examType, t),
    latestMathYear: latestMathResult?.year || null,
    latestBgYear: latestBgResult?.year || null,
    mathAvg: mathData?.average ?? null,
    bgAvg: bgData?.average ?? null,
    schoolAverageCombined,
    latestMath,
    latestBg,
    latestCombined,
    latestCombinedYear,
    latestYear,
    colorClass,
    minYear: yearsUsed.length > 0 ? Math.min(...yearsUsed) : null,
    maxYear: yearsUsed.length > 0 ? Math.max(...yearsUsed) : null,
    hasAverage,
  }
}

export function getLatestScoreForExamType(examResults = [], examType) {
  const filtered = examResults
    .filter(result => result.exam_type === examType && isAverageMetric(result.metric))
    .sort((a, b) => b.year - a.year)

  if (filtered.length === 0) {
    return null
  }

  const latestYear = filtered[0].year
  const yearResults = filtered.filter(result => result.year === latestYear)

  const mathResult = yearResults.find(result => getNvoSubjectKey(result.subject) === 'math')
  const bgResult = yearResults.find(result => getNvoSubjectKey(result.subject) === 'bulgarian')

  if (!mathResult && !bgResult) {
    return null
  }

  const mathScore = mathResult ? Number(mathResult.value) : null
  const bgScore = bgResult ? Number(bgResult.value) : null

  if (mathScore != null && bgScore != null) {
    return (mathScore + bgScore) / 2
  }

  return mathScore ?? bgScore
}

export function prepareNvoTimelineData(examResults = [], examType, examAverages = null) {
  const byYear = {}

  examResults.forEach((result) => {
    if (result.exam_type !== examType || !isAverageMetric(result.metric)) {
      return
    }

    const subjectKey = getNvoSubjectKey(result.subject)
    if (!subjectKey) {
      return
    }

    const year = Number(result.year)
    if (!byYear[year]) {
      byYear[year] = { year }
    }

    byYear[year][subjectKey] = Number(result.value)
  })

  const chartData = Object.values(byYear)
    .filter(item => item.bulgarian != null || item.math != null)
    .sort((a, b) => a.year - b.year)

  chartData.forEach((item, index) => {
    const benchmarkForYear = (
      examAverages?.by_year?.[examType]?.[String(item.year)] ||
      examAverages?.by_year?.[examType]?.[item.year] ||
      null
    )

    if (benchmarkForYear?.bulgarian != null) {
      item.nationalBulgarian = Number(benchmarkForYear.bulgarian)
    }
    if (benchmarkForYear?.math != null) {
      item.nationalMath = Number(benchmarkForYear.math)
    }

    if (index === 0) {
      return
    }

    const previous = chartData[index - 1]
    if (item.bulgarian != null && previous.bulgarian != null) {
      item.bulgarianPrevious = previous.bulgarian
    }
    if (item.math != null && previous.math != null) {
      item.mathPrevious = previous.math
    }
  })

  return chartData
}
