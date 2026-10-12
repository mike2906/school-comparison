import { useTranslation } from 'react-i18next'
import { dziGradeKey, dziSubjectLabel, getDziTable } from '../../utils/dzi'

// Phones show the newest year and the Sofia figure; earlier years from the small breakpoint up.
const olderYearClass = index => (index > 0 ? 'hidden sm:table-cell' : '')

/**
 * ДЗИ (matura) results: average grade per subject on the 2–6 scale, newest years first,
 * with the Sofia schools' average for the newest year. Kept apart from the NVO section:
 * grades and NVO points are different measures.
 */
function DziResults({ examResults, examAverages }) {
  const { t, i18n } = useTranslation()
  const table = getDziTable(examResults, examAverages)
  if (!table) return null

  const locale = i18n.language?.startsWith('bg') ? 'bg-BG' : 'en-GB'
  const formatGrade = value => new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('dzi.title')}</h2>
      <p className="text-sm text-neutral-600 mb-5">{t('dzi.intro')}</p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-neutral-500">
              <th scope="col" className="py-2 pr-4 font-medium">{t('dzi.subject')}</th>
              {table.years.map((year, index) => (
                <th key={year} scope="col" className={`py-2 px-3 font-medium text-right ${olderYearClass(index)}`}>{year}</th>
              ))}
              <th scope="col" className="py-2 pl-3 font-medium text-right">
                {t('dzi.sofiaAverage', { year: table.latestYear })}
              </th>
            </tr>
          </thead>
          <tbody>
            {table.subjects.map(row => (
              <tr
                key={row.subject}
                className={`border-b border-neutral-100 last:border-0 ${row.values[table.latestYear] == null ? 'hidden sm:table-row' : ''}`}
              >
                <th scope="row" className="py-2.5 pr-4 font-medium text-neutral-800 text-left">
                  {dziSubjectLabel(row.subject, t)}
                </th>
                {table.years.map((year, index) => {
                  const value = row.values[year]
                  return (
                    <td key={year} className={`py-2.5 px-3 text-right whitespace-nowrap ${olderYearClass(index)}`}>
                      {value == null ? (
                        <span className="text-neutral-400">–</span>
                      ) : (
                        <>
                          <span className="font-semibold text-neutral-900">{formatGrade(value)}</span>
                          <span className="block text-xs text-neutral-500">{t(`dzi.grades.${dziGradeKey(value)}`)}</span>
                        </>
                      )}
                    </td>
                  )
                })}
                <td className="py-2.5 pl-3 text-right text-neutral-600 whitespace-nowrap">
                  {row.benchmark == null ? '–' : formatGrade(row.benchmark)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-xs text-neutral-500">
        {t('dzi.footnote')}
        {table.sourceUrl && (
          <>
            {' '}
            <a href={table.sourceUrl} target="_blank" rel="noopener noreferrer" className="underline hover:text-neutral-700">
              {t('dzi.source')}
            </a>
          </>
        )}
      </p>
    </div>
  )
}

export default DziResults
