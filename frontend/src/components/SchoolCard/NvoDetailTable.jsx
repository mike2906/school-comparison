import { useTranslation } from 'react-i18next'
import {
  formatPercent,
  getBenchmarkTooltip,
  getNvoValueStyle,
  getTrendInfo,
  getTrendTooltip,
} from '../../utils/nvoDisplay'

/** The NVO results table in the card's expanded details. */
function NvoDetailTable({ nvoDetail, examAverages, secondaryLineClass }) {
  const { t } = useTranslation()

  return (
    <div>
      <div className="text-[15px] font-semibold text-neutral-900 border-l-4 border-teal-500 pl-2 mb-2">
        {t('schoolCard.sections.academicPerformance')}
      </div>
      <div className="text-sm text-neutral-600 mb-3">
        {t('schoolCard.nvo.latestTitle', { grade: nvoDetail.gradeLabel })}
      </div>
      {nvoDetail.hasAverage ? (
        <>
          <div className="text-sm text-neutral-700 font-semibold border-b border-neutral-200 pb-2 grid grid-cols-[1.4fr_1fr_1fr_0.8fr] gap-2">
            <span>{t('schoolCard.nvo.subjectHeader')}</span>
            <span className="text-center">{nvoDetail.latestYear || t('schoolCard.nvo.latest')}</span>
            <span className="text-center">{t('schoolCard.nvo.avgHeader')}</span>
            <span className="text-center">{t('schoolCard.nvo.trendHeader')}</span>
          </div>
          {[
            {
              label: t('schoolCard.nvo.subjectMath'),
              latest: nvoDetail.latestMath,
              avg: nvoDetail.mathAvg,
              style: getNvoValueStyle({
                value: nvoDetail.latestMath ?? nvoDetail.mathAvg,
                examType: nvoDetail.examType,
                year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                subjectKey: 'math',
                examAverages,
              }),
            },
            {
              label: t('schoolCard.nvo.subjectBulgarian'),
              latest: nvoDetail.latestBg,
              avg: nvoDetail.bgAvg,
              style: getNvoValueStyle({
                value: nvoDetail.latestBg ?? nvoDetail.bgAvg,
                examType: nvoDetail.examType,
                year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                subjectKey: 'bulgarian',
                examAverages,
              }),
            },
          ].map(item => {
            const latestValue = item.latest ?? item.avg
            const trend = item.latest != null ? getTrendInfo(item.latest, item.avg) : null
            const diff = trend ? Math.abs(trend.diff) : 0
            return (
              <div key={item.label} className="grid grid-cols-[1.4fr_1fr_1fr_0.8fr] gap-2 py-2 border-b border-neutral-100 text-sm">
                <span>{item.label}</span>
                <span
                  className={`text-center font-mono ${item.style.text} ${item.style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(latestValue, item.style.benchmark, t) || undefined}
                >
                  {t('academicPerformance.pointsValue', { value: formatPercent(latestValue, 1) })}
                </span>
                <span className="text-center font-mono text-neutral-700">
                  {t('academicPerformance.pointsValue', { value: formatPercent(item.avg, 1) })}
                </span>
                <span
                  className={`text-center font-mono ${trend ? `${trend.className} cursor-help` : 'text-neutral-400'}`}
                  title={getTrendTooltip(item.latest, item.avg, trend, t) || undefined}
                >
                  {trend ? `${trend.arrow} ${t('academicPerformance.pointsValue', { value: formatPercent(diff, 1) })}` : '—'}
                </span>
              </div>
            )
          })}
          <div className={`mt-2 text-[13px] ${secondaryLineClass}`}>
            {t('schoolCard.nvo.basedOnYears', { start: nvoDetail.minYear, end: nvoDetail.maxYear })}
          </div>
        </>
      ) : (
        <div className="text-sm text-neutral-700">
          <div className="flex items-center justify-between border-b border-neutral-100 py-2">
            <span>{t('schoolCard.nvo.subjectMath')}</span>
            <span
              className={`font-mono ${getNvoValueStyle({
                value: nvoDetail.latestMath,
                examType: nvoDetail.examType,
                year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                subjectKey: 'math',
                examAverages,
              }).text} ${getNvoValueStyle({
                value: nvoDetail.latestMath,
                examType: nvoDetail.examType,
                year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                subjectKey: 'math',
                examAverages,
              }).benchmark ? 'cursor-help' : ''}`}
              title={getBenchmarkTooltip(
                nvoDetail.latestMath,
                getNvoValueStyle({
                  value: nvoDetail.latestMath,
                  examType: nvoDetail.examType,
                  year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                  subjectKey: 'math',
                  examAverages,
                }).benchmark,
                t
              ) || undefined}
            >
              {t('academicPerformance.pointsValue', { value: formatPercent(nvoDetail.latestMath, 1) })}
            </span>
          </div>
          <div className="flex items-center justify-between py-2">
            <span>{t('schoolCard.nvo.subjectBulgarian')}</span>
            <span
              className={`font-mono ${getNvoValueStyle({
                value: nvoDetail.latestBg,
                examType: nvoDetail.examType,
                year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                subjectKey: 'bulgarian',
                examAverages,
              }).text} ${getNvoValueStyle({
                value: nvoDetail.latestBg,
                examType: nvoDetail.examType,
                year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                subjectKey: 'bulgarian',
                examAverages,
              }).benchmark ? 'cursor-help' : ''}`}
              title={getBenchmarkTooltip(
                nvoDetail.latestBg,
                getNvoValueStyle({
                  value: nvoDetail.latestBg,
                  examType: nvoDetail.examType,
                  year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                  subjectKey: 'bulgarian',
                  examAverages,
                }).benchmark,
                t
              ) || undefined}
            >
              {t('academicPerformance.pointsValue', { value: formatPercent(nvoDetail.latestBg, 1) })}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

export default NvoDetailTable
