import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useCompare } from '../../context/CompareContext'
import { getSchoolName } from '../../utils/i18n'

function CompareBar() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { compareList, removeFromCompare, clearCompare } = useCompare()
  const [showHint, setShowHint] = useState(false)
  const hintTimeoutRef = useRef(null)

  const handleCompare = () => {
    navigate('/compare')
  }

  useEffect(() => {
    return () => {
      if (hintTimeoutRef.current) {
        clearTimeout(hintTimeoutRef.current)
      }
    }
  }, [])

  const triggerHint = () => {
    if (compareList.length >= 2) return
    setShowHint(true)
    if (hintTimeoutRef.current) {
      clearTimeout(hintTimeoutRef.current)
    }
    hintTimeoutRef.current = setTimeout(() => {
      setShowHint(false)
    }, 1800)
  }

  if (compareList.length === 0) {
    return null
  }

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-white border-t-2 border-primary-500 shadow-2xl z-[2000]">
      <div className="max-w-7xl mx-auto px-4 py-4">
        <div className="flex items-center gap-4">
          {/* Title and Count */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
              <span className="font-semibold text-neutral-900">
                {t('compare.title')}
              </span>
            </div>
            <span className="px-2 py-0.5 bg-primary-100 text-primary-700 text-xs font-medium rounded-full">
              {compareList.length}
            </span>
          </div>

          {/* School Pills */}
          <div className="flex-1 flex items-center gap-2 overflow-x-auto scrollbar-thin">
            {compareList.map(school => (
              <div
                key={school.id}
                className="flex items-center gap-2 px-3 py-2 bg-neutral-100 rounded-lg border border-neutral-200 whitespace-nowrap"
              >
                <span className="text-sm text-neutral-900 truncate max-w-[200px]">
                  {getSchoolName(school, i18n.language)}
                </span>
                <button
                  onClick={() => removeFromCompare(school.id)}
                  className="flex-shrink-0 p-0.5 hover:bg-neutral-200 rounded transition-colors"
                  aria-label={t('compare.remove')}
                >
                  <svg className="w-4 h-4 text-neutral-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={clearCompare}
              className="px-4 py-2 text-sm font-medium text-neutral-700 hover:text-neutral-900 transition-colors"
            >
              {t('compare.clear')}
            </button>
            <div
              className="relative"
              onClick={triggerHint}
              onMouseDown={triggerHint}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') triggerHint()
              }}
              role="button"
              tabIndex={compareList.length < 2 ? 0 : -1}
              aria-disabled={compareList.length < 2}
            >
              <button
                onClick={handleCompare}
                disabled={compareList.length < 2}
                className={`
                  px-6 py-2 rounded-lg text-sm font-medium transition-all
                  ${compareList.length >= 2
                    ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-lg hover:shadow-xl'
                    : 'bg-neutral-300 text-neutral-500 cursor-not-allowed pointer-events-none'
                  }
                `}
              >
                {t('compare.compareNow')}
              </button>
              {showHint && (
                <div className="absolute right-0 -top-10 whitespace-nowrap rounded-md bg-neutral-900 text-white text-xs px-3 py-1.5 shadow-lg">
                  {t('compare.addMoreHint')}
                </div>
              )}
            </div>
          </div>
        </div>

        <span className="sr-only" aria-live="polite">
          {showHint ? t('compare.addMoreHint') : ''}
        </span>
      </div>
    </div>
  )
}

export default CompareBar
