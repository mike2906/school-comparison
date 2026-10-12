// ДЗИ (12th-grade state matura) results. Grades are on the Bulgarian 2–6 scale, not NVO
// points, so they get their own table and benchmark and are never ranked against NVO.

const DZI_EXAM_TYPE = 'dzi'
const DZI_METRIC = 'average_grade'

// Display order; also the subjects we have labels for. The importer stores
// `<subject>[_<cefr level>][_oop|_pp]`, e.g. "math_pp", "english_b2_pp".
const SUBJECT_ORDER = [
  'bulgarian', 'math', 'english', 'german', 'french', 'spanish', 'italian', 'russian',
  'history', 'geography', 'philosophy', 'biology', 'physics', 'chemistry', 'informatics', 'it',
]
const CURRICULUM_ORDER = { oop: 0, pp: 1 }

/** { base, cefr, curriculum } for a stored ДЗИ subject, or null when we have no label for it. */
export function parseDziSubject(subject) {
  const parts = String(subject || '').toLowerCase().split('_')
  const base = parts[0]
  if (!SUBJECT_ORDER.includes(base)) return null
  const rest = parts.slice(1)
  const curriculum = rest.find(part => part in CURRICULUM_ORDER) || null
  const level = rest.find(part => /^[ab][12]\d?$/.test(part))
  // "b11" is stored for B1.1.
  const cefr = level ? `${level[0].toUpperCase()}${level[1]}${level[2] ? `.${level[2]}` : ''}` : null
  return { base, cefr, curriculum }
}

/** "Maths (specialised)", "English B2 (specialised)". */
export function dziSubjectLabel(subject, t) {
  const parsed = parseDziSubject(subject)
  if (!parsed) return subject
  const name = [t(`dzi.subjects.${parsed.base}`), parsed.cefr].filter(Boolean).join(' ')
  return parsed.curriculum ? t('dzi.subjectWithCurriculum', { subject: name, curriculum: t(`dzi.curriculum.${parsed.curriculum}`) }) : name
}

/** The grade's name parents know from report cards (Bulgarian bands: 3.50 is "Добър"). */
export function dziGradeKey(value) {
  const grade = Number(value)
  if (!Number.isFinite(grade)) return null
  if (grade < 3) return 'poor'
  if (grade < 3.5) return 'fair'
  if (grade < 4.5) return 'good'
  if (grade < 5.5) return 'veryGood'
  return 'excellent'
}

function compareSubjects(a, b) {
  const pa = parseDziSubject(a)
  const pb = parseDziSubject(b)
  return (
    SUBJECT_ORDER.indexOf(pa.base) - SUBJECT_ORDER.indexOf(pb.base) ||
    (CURRICULUM_ORDER[pa.curriculum] ?? -1) - (CURRICULUM_ORDER[pb.curriculum] ?? -1) ||
    String(pa.cefr || '').localeCompare(String(pb.cefr || ''))
  )
}

/**
 * The school's ДЗИ results as a table: the newest `maxYears` years (newest first) and one
 * row per subject sat in any of them, with the Sofia schools' average for the newest year.
 * Null when the school has no ДЗИ results.
 */
export function getDziTable(examResults = [], examAverages = null, { maxYears = 3 } = {}) {
  const rows = (examResults || []).filter(result => (
    result.exam_type === DZI_EXAM_TYPE &&
    result.metric === DZI_METRIC &&
    parseDziSubject(result.subject) &&
    Number.isFinite(Number(result.value))
  ))
  if (rows.length === 0) return null

  const years = [...new Set(rows.map(result => Number(result.year)))]
    .sort((a, b) => b - a)
    .slice(0, maxYears)
  const latestYear = years[0]
  const benchmarks = examAverages?.by_year?.[DZI_EXAM_TYPE]?.[String(latestYear)] || {}

  const bySubject = new Map()
  rows
    .filter(result => years.includes(Number(result.year)))
    .forEach((result) => {
      const entry = bySubject.get(result.subject) || { subject: result.subject, values: {} }
      entry.values[Number(result.year)] = Number(result.value)
      bySubject.set(result.subject, entry)
    })

  const subjects = [...bySubject.values()]
    .sort((a, b) => compareSubjects(a.subject, b.subject))
    .map(entry => ({
      ...entry,
      benchmark: benchmarks[entry.subject] != null ? Number(benchmarks[entry.subject]) : null,
    }))
  const latestRow = rows.find(result => Number(result.year) === latestYear && result.source_url)

  return { years, latestYear, subjects, sourceUrl: latestRow?.source_url || null }
}
