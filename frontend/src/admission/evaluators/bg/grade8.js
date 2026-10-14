/**
 * Bulgarian eighth-grade admission score (бал), national rules. Each class (паралелка)
 * sets its own formula: weights for the НВО results in Bulgarian and maths, the two
 * certificate subjects whose grades are converted to points, and, for classes with an
 * aptitude exam, the exam's weight. The rule file holds the national parts (grade
 * conversion, the maximum); the class formula comes from the school's admission plan.
 */
import { addRanges, range, ZERO } from '../../core.js'

const NVO_SUBJECTS = ['bulgarian', 'math']

function nvoPart(value, weight, nvoMax) {
  if (!weight) return ZERO
  if (value == null) return range(0, weight * nvoMax)
  return range(weight * value)
}

function gradePart(grade, gradePoints) {
  const all = Object.values(gradePoints)
  if (grade == null) return range(Math.min(...all), Math.max(...all))
  return range(gradePoints[String(grade)] ?? 0)
}

function round2(value) {
  return Math.round(value * 100) / 100
}

/**
 * @param {object} params    rule-set params: { nvo_max, exam_max, grade_points }
 * @param {object} answers   { nvo: { bulgarian, math }, grades: { [subject]: 3..6 }, exam }
 * @param {object} context   the class formula: { nvo_weights: { bulgarian, math },
 *                           grade_subjects: [subject, subject], exam_weight }
 */
export function evaluate(params, answers = {}, context = {}) {
  const weights = context.nvo_weights || {}
  const nvo = answers.nvo || {}
  const grades = answers.grades || {}
  const items = []

  for (const subject of NVO_SUBJECTS) {
    const weight = weights[subject] || 0
    if (!weight) continue
    items.push({ id: `nvo_${subject}`, weight, points: nvoPart(nvo[subject], weight, params.nvo_max) })
  }
  if (context.exam_weight) {
    items.push({ id: 'aptitude_exam', weight: context.exam_weight, points: nvoPart(answers.exam, context.exam_weight, params.exam_max) })
  }
  for (const subject of context.grade_subjects || []) {
    items.push({ id: `grade_${subject}`, subject, points: gradePart(grades[subject], params.grade_points) })
  }

  const total = addRanges(...items.map(i => i.points))
  return { score: range(round2(total.min), round2(total.max)), items }
}

/** The highest score the class formula allows (500 for the usual 2+2 or 3+1 formulas). */
export function maxScore(params, context) {
  return evaluate(params, {}, context).score.max
}
