import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import CutoffTable from './CutoffTable'
import { calculatorStagesFor, usesSofiaKindergartenSystem } from '../../utils/admission'

/**
 * "Who got in before" on a school page: the last admitted result per group or class from
 * the official rankings, and a link to the calculator for this school.
 */
function SchoolCutoffs({ school }) {
  const { t } = useTranslation()
  const rows = school.admission_info?.cutoff_summary || []
  const stages = calculatorStagesFor(school)
  if (rows.length === 0 && stages.length === 0) return null
  // One link says what it calculates when a school has more than one: a school with a
  // preparatory group links to it as well as to first grade.
  const label = stage => {
    if (stages.length === 1) return t('cutoffs.calculate')
    return t(`cutoffs.calculateStage.${stage === 'kindergarten' && !usesSofiaKindergartenSystem(school) ? 'preparatory' : stage}`)
  }

  return (
    <section className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('cutoffs.title')}</h2>
      <p className="text-sm text-neutral-600">{t('cutoffs.intro')}</p>
      <div className="mt-4">
        {rows.length > 0 ? <CutoffTable rows={rows} /> : <p className="text-sm text-neutral-600">{t('cutoffs.none')}</p>}
      </div>
      {stages.length > 0 && (
        <div className="mt-5 flex flex-wrap gap-2">
          {stages.map(stage => (
            <Link
              key={stage}
              to={`/admission?stage=${stage}&school=${school.id}`}
              className="inline-flex items-center rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800"
            >
              {label(stage)}
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}

export default SchoolCutoffs
