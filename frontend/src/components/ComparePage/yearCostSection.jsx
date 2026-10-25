import { formatCurrency, YEAR_STATUS } from '../../utils/pricing'
import { planNameLabel, textLang } from '../../utils/priceLabels'
import { priceCategory } from '../../utils/priceRows'
import { safeSourceUrl, sourceSiteName } from '../../utils/priceSources'
import { COST_NEEDS, COST_STAGES, offersStage, totalWithNeeds } from '../../utils/yearCost'

// Longer fee names ("задължителен избор на едно занимание в рамките на …") give way to the category.
const NAME_MAX = 40

/**
 * The compare page's "Cost per year" section (docs/YEARLY_COST.md): one row per cost line,
 * the required total, the total with the extras the parent ticked, and first-year fees.
 * `costById` holds each school's yearCost for `stage`.
 */
export function buildYearCostSection({ t, language, schools, costById, stage, needs, onStageChange, onToggleNeed }) {
  const money = ({ min, max }) => (Math.round(min) === Math.round(max)
    ? formatCurrency(min, language)
    : `${formatCurrency(min, language)}–${formatCurrency(max, language)}`)
  const muted = text => <span className="text-sm text-neutral-400">{text}</span>
  const lineName = line => t(`yearCost.lines.${line}`)

  const state = school => {
    if (school.school_type === 'state') return 'state'
    if (!offersStage(school, stage)) return 'notOffered'
    return costById.get(school.id) ? 'cost' : 'none'
  }
  // A cell for a school without a cost table: the same words on every row.
  const fallback = (school) => {
    const kind = state(school)
    if (kind === 'state') return muted(t('yearCost.free'))
    if (kind === 'notOffered') return muted(t('yearCost.notForStage'))
    return muted(t('yearCost.notStated'))
  }

  const source = (row) => {
    const href = safeSourceUrl(row.source_url)
    if (!href) return null
    const site = sourceSiteName(href)
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs text-teal-700 underline hover:text-teal-800"
        aria-label={t('pricing.sourceLinkLabel', { site })}
      >
        {site}<span aria-hidden="true"> ↗</span>
      </a>
    )
  }

  const periodText = (item) => {
    if (item.perYear?.assumed) return ` · ${t('yearCost.periodAssumed')}`
    if (!item.period) return item.perYear ? ` / ${t('pricing.per.yearly')}` : ` · ${t('pricing.periodNotStated')}`
    if (item.period === 'one_time') return ''
    return ` / ${t(`pricing.per.${item.period}`)}`
  }

  const optionLine = (item, { sign = '+ ', tag = null, named = false } = {}) => {
    const name = planNameLabel(item.row, t, language) || (named ? t(`pricing.${priceCategory(item.row)}`) : null)
    return (
      <li key={item.row.id} className="text-sm text-neutral-700">
        <span className="font-medium text-neutral-900">{sign}{money(item.value)}</span>
        {periodText(item)}
        {tag && <span className="text-neutral-500">{` · ${tag}`}</span>}
        {name && name.length <= NAME_MAX && <span className="block text-xs text-neutral-500" lang={textLang(name, language)}>{name}</span>}
        <span className="block">{source(item.row)}</span>
      </li>
    )
  }
  const optionList = (items, options) => (
    <ul className="space-y-1.5">{items.map(item => optionLine(item, options))}</ul>
  )

  const lineCell = (line, school) => {
    const cost = costById.get(school.id)
    if (state(school) !== 'cost') return fallback(school)
    const entry = cost.lines[line]
    if (entry.state === 'included') return <span className="text-sm font-medium text-emerald-700">{t('yearCost.included')}</span>
    if (entry.options.length === 0) return muted(t(entry.state === 'excluded' ? 'yearCost.excluded' : 'yearCost.notStated'))
    return <ul className="space-y-1.5">{entry.options.map(item => optionLine(item, { tag: t(item.required ? 'yearCost.required' : 'yearCost.optional') }))}</ul>
  }
  const lineCompare = line => school => {
    if (state(school) !== 'cost') return state(school)
    const entry = costById.get(school.id)?.lines[line]
    return entry ? [entry.state, ...entry.options.map(item => `${item.value.min}-${item.value.max}`)].join('|') : state(school)
  }

  const missingText = missing => (missing.length > 0
    ? t('yearCost.notAdded', { items: missing.map(({ line, reason }) => t(`yearCost.reasons.${reason}`, { line: lineName(line) })).join(', ') })
    : null)

  const totalCell = (pick, school) => {
    const cost = costById.get(school.id)
    if (state(school) !== 'cost') return fallback(school)
    if (!cost.required) {
      const monthly = cost.tuition.options.some(item => item.period === 'monthly')
      return muted(t(monthly ? 'yearCost.noYearlyTotal' : 'yearCost.noTotal'))
    }
    const total = pick(cost)
    return (
      <div className="space-y-1">
        <div className="text-base font-bold text-neutral-900">
          {money(total.value)}{total.value.assumed ? '*' : ''}
        </div>
        {cost.tuition.statedTotal && <p className="text-xs text-neutral-500">{t('yearCost.statedTotal')}</p>}
        {missingText(total.missing) && <p className="text-xs text-amber-700">{missingText(total.missing)}</p>}
        {total.value.assumed && <p className="text-xs text-neutral-500">{t('yearCost.assumedNote')}</p>}
      </div>
    )
  }
  const totalCompare = pick => school => {
    if (state(school) !== 'cost') return state(school)
    const cost = costById.get(school.id)
    const total = cost?.required ? pick(cost) : null
    return total ? `${total.value.min}-${total.value.max}` : state(school)
  }

  const tuitionCell = (school) => {
    const cost = costById.get(school.id)
    if (state(school) !== 'cost') return fallback(school)
    if (cost.tuition.options.length === 0) return muted(t('yearCost.notStated'))
    const yearLabel = cost.academicYear && [
      cost.academicYear,
      cost.yearStatus === YEAR_STATUS.DATED_OTHER ? t('pricing.notCurrentYear') : null,
      cost.yearStatus === YEAR_STATUS.NEXT ? t('pricing.nextYear') : null,
    ].filter(Boolean).join(' · ')
    const monthly = !cost.tuition.perYear && cost.tuition.options.some(item => item.period === 'monthly')
    return (
      <div className="space-y-1">
        <ul className="space-y-1.5">{cost.tuition.options.map(item => optionLine(item, { sign: '' }))}</ul>
        {monthly && <p className="text-xs text-amber-700">{t('yearCost.billedMonthsNotPublished')}</p>}
        {yearLabel && <p className="text-xs text-neutral-500">{yearLabel}</p>}
      </div>
    )
  }

  const firstYearCell = (school) => {
    const cost = costById.get(school.id)
    if (state(school) !== 'cost') return fallback(school)
    const { options, deposits, uniforms } = cost.firstYear
    if (options.length + deposits.length + uniforms.length === 0) return muted(t('yearCost.notStated'))
    const uniformRange = uniforms.length > 0 && {
      min: Math.min(...uniforms.map(item => item.value.min)),
      max: Math.max(...uniforms.map(item => item.value.max)),
    }
    return (
      <div className="space-y-1.5">
        {/* Listed, not summed: two admission fees may be one or the other (round I or II). */}
        {options.length > 0 && optionList(options, { named: true })}
        {deposits.map(item => (
          <p key={item.row.id} className="text-xs text-neutral-600">
            {t('yearCost.deposit', { price: money(item.value) })} {source(item.row)}
          </p>
        ))}
        {uniformRange && <p className="text-xs text-neutral-600">{t('yearCost.uniformItems', { price: money(uniformRange) })}</p>}
      </div>
    )
  }

  const optionalCell = (school) => {
    const cost = costById.get(school.id)
    if (state(school) !== 'cost') return fallback(school)
    if (cost.optional.length === 0) return muted(t('yearCost.notStated'))
    return (
      <ul className="space-y-1.5">
        {cost.optional.map(item => (
          <li key={item.row.id} className="text-sm text-neutral-700">
            <span className="text-neutral-900">{t(`pricing.${priceCategory(item.row)}`)}</span>
            {`: ${money(item.value)}`}{periodText(item)}
            <span className="block">{source(item.row)}</span>
          </li>
        ))}
      </ul>
    )
  }

  const needLabel = line => (
    <label className="flex items-start gap-2 font-normal">
      <input
        type="checkbox"
        checked={needs.includes(line)}
        onChange={() => onToggleNeed(line)}
        className="mt-0.5 h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
      />
      <span>
        <span className="font-semibold">{lineName(line)}</span>
        <span className="block text-xs font-normal text-neutral-500">{t('yearCost.addToTotal')}</span>
      </span>
    </label>
  )

  const anyOptions = line => schools.some(school => (costById.get(school.id)?.lines[line]?.options.length ?? 0) > 0)
  const row = (key, label, getValue, getCompare) => ({ key, label, getValue, getCompare })

  const rows = [
    row('tuition', lineName('tuition'), tuitionCell, school => (state(school) === 'cost' ? costById.get(school.id).tuition.options.map(item => item.row.id).join(',') || 'none' : state(school))),
    row('materials', lineName('materials'), school => lineCell('materials', school), lineCompare('materials')),
    ...(anyOptions('required') ? [row('required', lineName('required'), school => lineCell('required', school), lineCompare('required'))] : []),
    ...COST_NEEDS.map(line => row(line, needLabel(line), school => lineCell(line, school), lineCompare(line))),
    row('requiredTotal', lineName('requiredTotal'), school => totalCell(cost => cost.required, school), totalCompare(cost => cost.required)),
    ...(needs.length > 0
      ? [row('withNeeds', lineName('withNeeds'), school => totalCell(cost => totalWithNeeds(cost, needs), school), totalCompare(cost => totalWithNeeds(cost, needs)))]
      : []),
    row('firstYear', lineName('firstYear'), firstYearCell, school => (state(school) === 'cost' ? costById.get(school.id).firstYear.options.map(item => item.row.id).join(',') || 'none' : state(school))),
    row('optional', lineName('optional'), optionalCell, school => (state(school) === 'cost' ? costById.get(school.id).optional.map(item => item.row.id).join(',') || 'none' : state(school))),
  ]

  const stageName = key => (key.startsWith('grade_')
    ? t('pricing.grades.one', { n: Number(key.slice(6)) })
    : t(`ageGroups.${key}`))
  const title = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span>{t('yearCost.title')}</span>
      <label className="flex items-center gap-2 text-xs font-normal">
        <span>{t('yearCost.for')}</span>
        <select
          value={stage}
          onChange={event => onStageChange(event.target.value)}
          className="rounded border border-white/40 bg-white px-2 py-1 text-sm text-neutral-900"
        >
          {COST_STAGES.map(key => <option key={key} value={key}>{stageName(key)}</option>)}
        </select>
      </label>
    </div>
  )

  return { key: 'yearCost', title, rows }
}
