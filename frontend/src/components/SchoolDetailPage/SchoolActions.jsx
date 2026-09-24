import { useTranslation } from 'react-i18next'

const SHARE_ICON = 'M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z'
const CHECK_ICON = 'M5 13l4 4L19 7'

/**
 * Share + Compare buttons. `compact` is the mobile bottom-bar variant (icon-only share,
 * buttons stretch to the bar width).
 */
function SchoolActions({ onShare, onCompare, inCompare, canAddMore, compact = false }) {
  const { t } = useTranslation()
  const compareDisabled = !inCompare && !canAddMore

  return (
    <div className={`flex gap-3 ${compact ? 'w-full' : ''}`}>
      <button
        type="button"
        onClick={onShare}
        aria-label={t('schools.share')}
        className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium text-neutral-700 bg-neutral-100 hover:bg-neutral-200 rounded-lg transition-colors border border-neutral-300 ${compact ? 'flex-1' : ''}`}
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={SHARE_ICON} />
        </svg>
        {!compact && t('schools.share')}
      </button>
      <button
        type="button"
        onClick={onCompare}
        disabled={compareDisabled}
        aria-pressed={inCompare}
        className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium rounded-lg transition-colors ${compact ? 'flex-[2]' : ''} ${
          inCompare
            ? 'bg-primary-100 text-primary-700 border-2 border-primary-500 hover:bg-primary-200'
            : compareDisabled
            ? 'bg-neutral-100 text-neutral-400 cursor-not-allowed border border-neutral-300'
            : 'bg-primary-700 text-white hover:bg-primary-800 border border-primary-600'
        }`}
      >
        {inCompare && (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={CHECK_ICON} />
          </svg>
        )}
        {inCompare ? t('schools.comparing') : t('schools.compare')}
      </button>
    </div>
  )
}

export default SchoolActions
