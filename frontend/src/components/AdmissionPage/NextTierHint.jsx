import { useTranslation } from 'react-i18next'
import { nextDurationTier } from '../../admission/core'

/**
 * Under an "address since" date: the day the address reaches the next duration tier, so
 * a parent near an anniversary knows a later ranking or campaign would count it.
 * `kind` is 'kg' (points) or 'g1' (group); no `tiers` when the duration cannot matter.
 */
function NextTierHint({ kind, tiers, since, measuredOn }) {
  const { t } = useTranslation()
  const next = tiers ? nextDurationTier(tiers, since, measuredOn) : null
  if (!next) return null
  const values = kind === 'kg'
    ? { points: next.tier.points, current: next.current?.points ?? 0 }
    : { group: next.tier.group, current: next.current?.group ?? '' }
  return (
    <p className="-mt-2 text-xs text-primary-800" data-testid="next-tier-hint">
      {t(`admissionCalc.${kind}.nextTier`, { date: next.on, ...values })}
    </p>
  )
}

export default NextTierHint
