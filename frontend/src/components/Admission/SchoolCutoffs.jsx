import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import CutoffTable from './CutoffTable'
import { calculatorLinksFor } from '../../admission/calculator'
import { useSavedVerdicts } from '../../hooks/useSavedAdmission'
import { SavedVerdictPanel } from './SavedVerdict'

/**
 * "Who got in before" on a school page: the last admitted result per group or class from
 * the official rankings, and a link to the calculator for this school.
 */
function SchoolCutoffs({ school }) {
  const { t } = useTranslation()
  const rows = school.admission_info?.cutoff_summary || []
  const links = calculatorLinksFor(school)
  const verdicts = useSavedVerdicts(school)
  if (rows.length === 0 && links.length === 0) return null
  // A link says what it calculates when a school has several, and always for a school's
  // preparatory group, which a parent would not expect under the kindergarten rules.
  const label = what => (links.length === 1 && what !== 'preparatory' ? t('cutoffs.calculate') : t(`cutoffs.calculateStage.${what}`))

  return (
    <section className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('cutoffs.title')}</h2>
      <p className="text-sm text-neutral-600">{t('cutoffs.intro')}</p>
      <div className="mt-4">
        {rows.length > 0 ? <CutoffTable rows={rows} /> : <p className="text-sm text-neutral-600">{t('cutoffs.none')}</p>}
      </div>
      {verdicts.map(verdict => <SavedVerdictPanel key={verdict.stage} verdict={verdict} schoolId={school.id} />)}
      {links.length > 0 && (
        <div className="mt-5 flex flex-wrap gap-2">
          {links.map(({ stage, what }) => (
            <Link
              key={stage}
              to={`/admission?stage=${stage}&school=${school.id}`}
              className="inline-flex items-center rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800"
            >
              {label(what)}
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}

export default SchoolCutoffs
