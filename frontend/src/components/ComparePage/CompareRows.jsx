import { Link } from 'react-router-dom'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { useCountry } from '../../context/CountryContext'
import { getSchoolName } from '../../utils/i18n'
import { getProvenanceSourceKey } from './sourceMetadata'
import { safeSourceUrl, sourceSiteName } from '../../utils/priceSources'
import {
  formatDate,
  normalizeCompareValue,
  SOURCE_BADGE_STYLES,
  SOURCE_CATEGORY_LABELS,
} from './compareHelpers.js'


export function InlineSource({ badgeKey, badgeLabel, url, displayUrl, dateLabel, t }) {
  const href = safeSourceUrl(url)
  if (!badgeLabel && !href && !dateLabel) return null

  // The site only: a full, percent-encoded path doesn't wrap and runs into the next column.
  const finalDisplay = href ? (displayUrl || sourceSiteName(href)) : null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1 text-[12px] text-neutral-500">
      {badgeLabel && (
        <span className={`inline-flex items-center rounded px-1.5 py-0.5 font-medium ${SOURCE_BADGE_STYLES[badgeKey] || SOURCE_BADGE_STYLES.unknown}`}>
          {badgeLabel}
        </span>
      )}
      {dateLabel && <span>{dateLabel}</span>}
      {finalDisplay && (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-w-0 items-center gap-1 break-all text-teal-700 hover:text-teal-800 underline"
          aria-label={t('compare.opensInNewWindow')}
        >
          {finalDisplay}
          <span aria-hidden="true">↗</span>
        </a>
      )}
    </div>
  )
}

export function SchoolHeader({ school, onRemove, t, language, compact = false }) {
  const { config: countryConfig } = useCountry()
  const name = getSchoolName(school, language)
  return (
    <div className={compact ? 'space-y-1.5' : 'space-y-2'}>
      <div className="flex items-start justify-between gap-1">
        <Link
          to={`/schools/${school.id}`}
          title={name}
          className={`min-w-0 font-bold text-neutral-900 hover:text-primary-700 hover:underline line-clamp-2 ${
            compact ? 'text-sm leading-snug' : 'text-base leading-snug'
          }`}
        >
          {name}
        </Link>
        <button
          type="button"
          onClick={() => onRemove(school.id)}
          className="flex-shrink-0 -mr-1 -mt-1 p-1.5 hover:bg-neutral-100 rounded transition-colors"
          aria-label={`${t('compare.remove')}: ${name}`}
        >
          <svg className="w-4 h-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
          school.school_type === 'state'
            ? 'bg-primary-50 text-primary-700'
            : 'bg-violet-50 text-violet-700'
        }`}>
          {schoolTypeLabel(school, t)}
        </span>
        {!compact && school.education_level && (
          <span className="px-2 py-0.5 rounded text-xs bg-neutral-100 text-neutral-700">
            {schoolLevelLabel(school, t, countryConfig)}
          </span>
        )}
      </div>
      {!compact && (
        <Link
          to={`/schools/${school.id}`}
          className="inline-block text-xs font-medium text-primary-600 hover:text-primary-700 hover:underline"
        >
          {t('schools.viewDetails')}
        </Link>
      )}
    </div>
  )
}

export function getDiffClasses(cells, highlightDiffs) {
  if (!highlightDiffs) return cells.map(() => 'bg-white')
  const normalized = cells.map(cell => normalizeCompareValue(cell.compareValue))
  const allSame = normalized.every(value => value === normalized[0])
  return cells.map(() => (allSame ? 'bg-white opacity-50' : 'bg-primary-50'))
}

export function renderCellContent(content) {
  return typeof content === 'string'
    ? <p className="text-sm text-neutral-700">{content || '—'}</p>
    : content
}

export function MobileCompareRow({ label, cells, highlightDiffs, labelStyle, gridStyle }) {
  const diffClasses = getDiffClasses(cells, highlightDiffs)
  return (
    <div className="border-b border-neutral-200">
      <h3
        className="sticky left-0 bg-neutral-100 px-3 py-1.5 text-xs font-semibold text-neutral-700"
        style={labelStyle}
      >
        {label}
      </h3>
      <div className="grid" style={gridStyle}>
        {cells.map((cell, idx) => (
          <div
            key={idx}
            className={`min-w-0 snap-start break-words px-3 py-2.5 ${idx > 0 ? 'border-l border-neutral-200' : ''} ${diffClasses[idx]}`}
          >
            {renderCellContent(cell.content)}
          </div>
        ))}
      </div>
    </div>
  )
}

export function FragmentSection({ section, schools, highlightDiffs }) {
  return (
    <>
      <SectionRow label={section.title} colSpan={schools.length + 1} />
      {section.rows.map((row) => (
        <CompareRow
          key={`${section.key}-${row.label}`}
          label={row.label}
          highlightDiffs={highlightDiffs}
          cells={schools.map((school) => ({
            content: row.getValue(school),
            compareValue: row.getCompare ? row.getCompare(school) : row.getValue(school),
          }))}
        />
      ))}
    </>
  )
}

export function SectionRow({ label, colSpan, tone = 'primary' }) {
  const classes = tone === 'muted'
    ? 'bg-neutral-100 text-neutral-700 border-neutral-200'
    : 'bg-primary-700 text-white border-primary-700'

  return (
    <tr>
      <th
        colSpan={colSpan}
        className={`text-left text-sm font-semibold px-4 py-2 border ${classes}`}
      >
        {label}
      </th>
    </tr>
  )
}

export function SourcesSectionRow({ label, colSpan, open, onToggle, t }) {
  return (
    <tr>
      <th
        colSpan={colSpan}
        className="bg-neutral-100 text-neutral-700 text-left text-sm font-semibold px-4 py-2 border border-neutral-200"
      >
        <div className="flex items-center justify-between gap-3">
          <span>{label}</span>
          <SourcesToggle open={open} onToggle={onToggle} t={t} />
        </div>
      </th>
    </tr>
  )
}

export function SourcesToggle({ open, onToggle, t }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="inline-flex items-center gap-2 text-xs font-medium text-neutral-600 hover:text-neutral-800"
    >
      <span>{open ? t('compare.hideSources') : t('compare.showSources')}</span>
      <svg
        className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`}
        viewBox="0 0 20 20"
        fill="currentColor"
        aria-hidden="true"
      >
        <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.94a.75.75 0 111.08 1.04l-4.24 4.5a.75.75 0 01-1.08 0l-4.24-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
      </svg>
    </button>
  )
}

export function CompareRow({ label, cells, highlightDiffs }) {
  const diffClasses = getDiffClasses(cells, highlightDiffs)

  return (
    <tr>
      <td className="sticky left-0 z-10 bg-neutral-100 p-4 font-semibold text-neutral-900 border border-neutral-200">
        {label}
      </td>
      {cells.map((cell, idx) => (
        <td key={idx} className={`p-4 border border-neutral-200 align-top break-words ${diffClasses[idx]}`}>
          {renderCellContent(cell.content)}
        </td>
      ))}
    </tr>
  )
}

export function SourcesPanel({ school, t, locale, completeness, open }) {
  const fieldSources = school.field_sources || []

  const presentText = completeness?.presentLabels?.length
    ? completeness.presentLabels.join(', ')
    : null
  const missingText = completeness?.missingLabels?.length
    ? completeness.missingLabels.join(', ')
    : null

  return (
    <div className="space-y-2">
      <div className="relative inline-flex items-center gap-2 text-xs font-medium text-neutral-600 group">
        <span>{t('compare.dataCompletenessShort', { score: completeness?.score ?? 0, total: completeness?.total ?? 0 })}</span>
        <span className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-neutral-300 text-[10px] text-neutral-500">
          i
        </span>
        <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 w-64 rounded-md bg-neutral-900 px-2 py-1.5 text-[11px] text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
          {presentText ? (
            <>
              <div>{t('compare.dataCompletenessHas')}: {presentText}</div>
              {missingText ? <div className="mt-1">{t('compare.dataCompletenessMissing')}: {missingText}</div> : null}
            </>
          ) : (
            <div>{t('compare.dataCompletenessTooltipEmpty')}</div>
          )}
        </div>
      </div>

      {open && fieldSources.length > 0 ? (
        <div className="space-y-3 text-sm text-neutral-700">
          <div>
            <div className="text-xs font-semibold uppercase text-neutral-500">
              {t(SOURCE_CATEGORY_LABELS.other)}
            </div>
            <ul className="mt-1 space-y-1">
              {fieldSources.map((item, index) => (
                <li key={getProvenanceSourceKey(item, index)}>
                  <InlineSource
                    badgeKey={item.source_type}
                    badgeLabel={t(`compare.sourceTypes.${item.source_type}`)}
                    url={item.source_url}
                    dateLabel={formatDate(item.last_verified, locale)}
                    t={t}
                  />
                </li>
              ))}
            </ul>
          </div>

          <div className="text-xs text-neutral-500">
            {t('compare.lastUpdated', { date: formatDate(school.updated_at, locale) || t('compare.notAvailable') })}
          </div>
        </div>
      ) : null}
    </div>
  )
}
