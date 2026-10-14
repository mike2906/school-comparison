import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import CutoffTable from './CutoffTable'
import { schoolCatchmentIds } from '../../admission/catchments'
import { usesSofiaKindergartenSystem } from '../../utils/admission'

/** The calculator tab a school's page links to, or null when no calculator fits it. */
export function calculatorStageFor(school) {
  const summary = school?.admission_info?.cutoff_summary || []
  if (usesSofiaKindergartenSystem(school)) return 'kindergarten'
  if (schoolCatchmentIds(school).length > 0) return 'grade_1'
  if (summary.some(row => row.stage === 'grade_8')) return 'grade_8'
  return null
}

/**
 * "Who got in before" on a school page: the last admitted result per group or class from
 * the official rankings, and a link to the calculator for this school.
 */
function SchoolCutoffs({ school }) {
  const { t } = useTranslation()
  const rows = school.admission_info?.cutoff_summary || []
  const stage = calculatorStageFor(school)
  if (rows.length === 0 && !stage) return null

  return (
    <section className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('cutoffs.title')}</h2>
      <p className="text-sm text-neutral-600">{t('cutoffs.intro')}</p>
      <div className="mt-4">
        {rows.length > 0 ? <CutoffTable rows={rows} /> : <p className="text-sm text-neutral-600">{t('cutoffs.none')}</p>}
      </div>
      {stage && (
        <Link
          to={`/admission?stage=${stage}&school=${school.id}`}
          className="mt-5 inline-flex items-center rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800"
        >
          {t('cutoffs.calculate')}
        </Link>
      )}
    </section>
  )
}

export default SchoolCutoffs
