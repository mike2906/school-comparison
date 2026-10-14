import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { EVALUATORS } from '../../admission/evaluators'
import { formatRange } from '../../admission/calculator'
import { Field, INPUT_CLASS, NumberField, Section, YesNo } from './fields'

const GRADE_SUBJECTS = ['first', 'second']

/**
 * Eighth-grade score (бал). Every class sets its own formula, and the formulas are in
 * each school's admission plan, so the score is shown for each formula the law allows.
 */
function Grade8Calculator({ selected }) {
  const { t } = useTranslation()
  const { ruleSet } = selected
  const params = ruleSet.params
  const [nvo, setNvo] = useState({ bulgarian: null, math: null })
  const [grades, setGrades] = useState({ first: null, second: null })
  const [hasExam, setHasExam] = useState(false)
  const [exam, setExam] = useState(null)

  const gradeValues = Object.keys(params.grade_points).map(Number).sort((x, y) => y - x)
  // A class with an aptitude exam counts each НВО once and the exam twice (чл. 58 ал. 1).
  // When the parent is not sure whether it has one, both kinds of formula are shown.
  const examFormula = { weights: { bulgarian: 1, math: 1 }, examWeight: 2 }
  const plainFormulas = params.nvo_weight_variants.map(weights => ({ weights, examWeight: 0 }))
  const formulas = hasExam === true ? [examFormula] : hasExam === false ? plainFormulas : [...plainFormulas, examFormula]
  const rows = formulas.map(({ weights, examWeight }) => {
    const result = EVALUATORS[ruleSet.evaluator].evaluate(
      params,
      { nvo, grades, exam },
      { nvo_weights: weights, grade_subjects: GRADE_SUBJECTS, exam_weight: examWeight }
    )
    return { weights, examWeight, result }
  })
  const invalid = rows.find(row => row.result.errors)?.result.errors || []

  return (
    <div className="space-y-6">
      <Section title={t('admissionCalc.g8.nvoTitle')} intro={t('admissionCalc.g8.nvoIntro', { max: params.nvo_max })}>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            label={t('admissionCalc.g8.bulgarian')}
            value={nvo.bulgarian}
            min={0}
            max={params.nvo_max}
            onChange={v => setNvo({ ...nvo, bulgarian: v })}
          />
          <NumberField
            label={t('admissionCalc.g8.math')}
            value={nvo.math}
            min={0}
            max={params.nvo_max}
            onChange={v => setNvo({ ...nvo, math: v })}
          />
        </div>
      </Section>

      <Section title={t('admissionCalc.g8.gradesTitle')} intro={t('admissionCalc.g8.gradesIntro')}>
        <div className="grid gap-4 sm:grid-cols-2">
          {GRADE_SUBJECTS.map(subject => (
            <Field key={subject} label={t(`admissionCalc.g8.grade.${subject}`)}>
              {id => (
                <select
                  id={id}
                  value={grades[subject] ?? ''}
                  onChange={event => setGrades({ ...grades, [subject]: event.target.value ? Number(event.target.value) : null })}
                  className={INPUT_CLASS}
                >
                  <option value="">{t('admissionCalc.notSure')}</option>
                  {gradeValues.map(grade => (
                    <option key={grade} value={grade}>
                      {t('admissionCalc.g8.gradeOption', { grade, points: params.grade_points[String(grade)] })}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          ))}
        </div>
        <YesNo label={t('admissionCalc.g8.hasExam')} hint={t('admissionCalc.g8.hasExamHint')} value={hasExam} onChange={setHasExam} />
        {hasExam !== false && (
          <NumberField
            label={t('admissionCalc.g8.exam', { max: params.exam_max })}
            value={exam}
            min={0}
            max={params.exam_max}
            onChange={setExam}
          />
        )}
      </Section>

      <section className="rounded-2xl border border-neutral-200 bg-white p-5 md:p-6" aria-live="polite">
        <h2 className="text-lg font-semibold text-neutral-900">{t('admissionCalc.g8.resultTitle')}</h2>
        <p className="mt-1 text-sm text-neutral-600">{t('admissionCalc.g8.resultIntro')}</p>
        {invalid.length > 0 ? (
          <p className="mt-4 text-sm text-red-700">{t('admissionCalc.g8.invalid')}</p>
        ) : (
          <table className="mt-4 w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th className="py-2 pr-3 font-medium">{t('admissionCalc.g8.formula')}</th>
                <th className="py-2 font-medium text-right">{t('admissionCalc.g8.score')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {rows.map(({ weights, examWeight, result }) => (
                <tr key={`${weights.bulgarian}-${weights.math}-${examWeight}`}>
                  <td className="py-2 pr-3 text-neutral-800">
                    {t('admissionCalc.g8.formulaText', { bg: weights.bulgarian, math: weights.math })}
                    {examWeight > 0 && ` + ${t('admissionCalc.g8.examPart')}`}
                  </td>
                  <td className="py-2 text-right font-medium tabular-nums text-neutral-900">{formatRange(result.score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-4 text-xs text-neutral-500">{t('admissionCalc.g8.tieBreak')}</p>
      </section>
    </div>
  )
}

export default Grade8Calculator
