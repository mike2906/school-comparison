import { useTranslation } from 'react-i18next'
import { formatRange } from '../../admission/calculator'

/** The criteria that give (or may give) points, one per line. */
function PointsList({ items }) {
  const { t } = useTranslation()
  const shown = items.filter(item => item.points.max > 0)
  if (shown.length === 0) return <p className="text-sm text-neutral-600">{t('admissionCalc.noPointItems')}</p>
  return (
    <dl className="divide-y divide-neutral-100 text-sm">
      {shown.map(item => (
        <div key={item.id} className="flex items-baseline justify-between gap-4 py-1.5">
          <dt className="text-neutral-700">{t(`admissionCalc.items.${item.id}`)}</dt>
          <dd className={`shrink-0 font-medium tabular-nums ${item.points.min === item.points.max ? 'text-neutral-900' : 'text-amber-800'}`}>
            {formatRange(item.points)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export default PointsList
