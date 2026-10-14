/**
 * Bulgarian eighth-grade admission score (бал), national rules (Наредба № 10/2016,
 * чл. 57–58). Each class (паралелка) sets its own formula: weights for the НВО results
 * (Bulgarian and maths, sometimes an extra НВО subject), the two certificate subjects
 * whose grades are converted to points, and, for classes with an aptitude exam, the
 * doubled exam result; a few classes add olympiad points. The rule file holds the
 * national parts (grade conversion, maxima); the class formula comes from the school's
 * admission plan.
 */
import { addRanges, range } from '../../core.js'

function part(value, weight, max) {
  if (value == null) return range(0, weight * max)
  return range(weight * value)
}

function gradePart(grade, gradePoints) {
  const all = Object.values(gradePoints)
  if (grade == null) return range(Math.min(...all), Math.max(...all))
  return range(gradePoints[String(grade)])
}

function round2(value) {
  return Math.round(value * 100) / 100
}

function checkScore(errors, id, value, max) {
  if (value != null && !(typeof value === 'number' && value >= 0 && value <= max)) errors.push(id)
}

/**
 * @param {object} params   rule-set params: { nvo_max, exam_max, olympiad_max, grade_points }
 * @param {object} answers  { nvo: { [subject]: 0..100 }, grades: { [subject]: 3..6 },
 *                          exam, olympiad }
 * @param {object} context  the class formula: { nvo_weights: { [subject]: weight },
 *                          grade_subjects: [subject, subject], exam_weight, olympiad_weight }
 * @returns {object} { score, items } or { errors } when the formula or an answer is invalid
 */
export function evaluate(params, answers = {}, context = {}) {
  const weights = context.nvo_weights || {}
  const subjects = context.grade_subjects || []
  if (Object.keys(weights).length === 0 || subjects.length !== 2) return { errors: ['formula'] }

  const nvo = answers.nvo || {}
  const grades = answers.grades || {}
  const errors = []
  const items = []

  for (const [subject, weight] of Object.entries(weights)) {
    checkScore(errors, `nvo_${subject}`, nvo[subject], params.nvo_max)
    items.push({ id: `nvo_${subject}`, subject, weight, points: part(nvo[subject], weight, params.nvo_max) })
  }
  if (context.exam_weight) {
    checkScore(errors, 'aptitude_exam', answers.exam, params.exam_max)
    items.push({ id: 'aptitude_exam', weight: context.exam_weight, points: part(answers.exam, context.exam_weight, params.exam_max) })
  }
  if (context.olympiad_weight) {
    checkScore(errors, 'olympiad', answers.olympiad, params.olympiad_max)
    items.push({ id: 'olympiad', weight: context.olympiad_weight, points: part(answers.olympiad, context.olympiad_weight, params.olympiad_max) })
  }
  for (const subject of subjects) {
    const grade = grades[subject]
    if (grade != null && params.grade_points[String(grade)] == null) errors.push(`grade_${subject}`)
    items.push({ id: `grade_${subject}`, subject, points: gradePart(grade, params.grade_points) })
  }
  if (errors.length > 0) return { errors }

  const total = addRanges(...items.map(i => i.points))
  return { score: range(round2(total.min), round2(total.max)), items }
}
