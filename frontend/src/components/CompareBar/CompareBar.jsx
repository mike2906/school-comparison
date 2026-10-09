import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import { useCompare } from '../../context/CompareContext'
import { compareHref } from '../../utils/compareList'
import { getSchoolName } from '../../utils/i18n'

function RemoveIcon() {
  return (
    <svg className="w-4 h-4 text-neutral-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
    </svg>
  )
}

function CompareBar() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { pathname, search } = useLocation()
  const { compareList, removeFromCompare, clearCompare, maxCompare } = useCompare()
  const [showHint, setShowHint] = useState(false)
  const [pillsOpen, setPillsOpen] = useState(false)
  const hintTimeoutRef = useRef(null)

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

  // The compare page has its own controls; the bar would only cover its content.
  if (compareList.length === 0 || pathname.startsWith('/compare')) {
    return null
  }

  const canCompare = compareList.length >= 2
  const isFull = compareList.length >= maxCompare
  const maxNote = isFull ? t('compare.maxReached', { max: maxCompare }) : null

  const pills = compareList.map(school => (
    <div
      key={school.id}
      className="flex items-center gap-2 px-3 py-2 bg-neutral-100 rounded-lg border border-neutral-200 whitespace-nowrap min-w-0 md:flex-shrink-0"
    >
      <span className="flex-1 min-w-0 text-sm text-neutral-900 truncate md:flex-none md:max-w-[200px]">
        {getSchoolName(school, i18n.language)}
      </span>
      <button
        type="button"
        onClick={() => removeFromCompare(school.id)}
        className="flex-shrink-0 p-0.5 hover:bg-neutral-200 rounded transition-colors"
        aria-label={`${t('compare.remove')}: ${getSchoolName(school, i18n.language)}`}
      >
        <RemoveIcon />
      </button>
    </div>
  ))

  const compareButton = (
    <div
      className="relative flex-shrink-0"
      onClick={triggerHint}
      onMouseDown={triggerHint}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') triggerHint()
      }}
      role="button"
      tabIndex={canCompare ? -1 : 0}
      aria-disabled={!canCompare}
    >
      <button
        type="button"
        onClick={() => navigate(compareHref(search))}
        disabled={!canCompare}
        className={`
          px-4 md:px-6 py-2.5 md:py-2 rounded-lg text-sm font-medium transition-all whitespace-nowrap
          ${canCompare
            ? 'bg-primary-700 text-white hover:bg-primary-800 shadow-lg hover:shadow-xl'
            : 'bg-neutral-300 text-neutral-500 cursor-not-allowed pointer-events-none'
          }
        `}
      >
        {t('compare.compareCount', { count: compareList.length })}
      </button>
      {showHint && (
        <div className="absolute right-0 -top-10 whitespace-nowrap rounded-md bg-neutral-900 text-white text-xs px-3 py-1.5 shadow-lg">
          {t('compare.addMoreHint')}
        </div>
      )}
    </div>
  )

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-white border-t-2 border-primary-500 shadow-2xl z-[2000] pb-[env(safe-area-inset-bottom)]">
      {/* Mobile: one compact row; the pills open above it on demand. */}
      <div className="md:hidden px-4 py-2">
        {pillsOpen && (
          <div id="compare-bar-pills" className="flex flex-col gap-2 pb-2">
            {pills}
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setPillsOpen(open => !open)}
            aria-expanded={pillsOpen}
            aria-controls="compare-bar-pills"
            aria-label={pillsOpen ? t('compare.hideSelected') : t('compare.showSelected')}
            className="flex-1 min-w-0 flex items-center gap-2 py-1.5 text-left"
          >
            <span className="flex-shrink-0 px-2 py-0.5 bg-primary-100 text-primary-700 text-xs font-semibold rounded-full">
              {compareList.length}/{maxCompare}
            </span>
            {/* The 4/4 badge already gives the count, so a full list shows why it is full. */}
            <span className={`min-w-0 font-medium leading-tight ${maxNote ? 'text-xs text-neutral-600' : 'truncate text-sm text-neutral-900'}`}>
              {maxNote || t('compare.selectedCount', { count: compareList.length })}
            </span>
            <svg
              className={`h-4 w-4 flex-shrink-0 text-neutral-500 transition-transform ${pillsOpen ? '' : 'rotate-180'}`}
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.94a.75.75 0 111.08 1.04l-4.24 4.5a.75.75 0 01-1.08 0l-4.24-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
            </svg>
          </button>
          <button
            type="button"
            onClick={clearCompare}
            className="flex-shrink-0 px-2 py-2.5 text-sm font-medium text-neutral-600 hover:text-neutral-900"
          >
            {t('compare.clear')}
          </button>
          {compareButton}
        </div>
      </div>

      {/* Desktop */}
      <div className="hidden md:block max-w-7xl mx-auto px-4 py-4">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
              <span className="font-semibold text-neutral-900">
                {t('compare.title')}
              </span>
            </div>
            <span className="px-2 py-0.5 bg-primary-100 text-primary-700 text-xs font-medium rounded-full">
              {compareList.length}/{maxCompare}
            </span>
            {maxNote && <span className="text-xs text-neutral-500">{maxNote}</span>}
          </div>

          <div className="flex-1 min-w-0 flex items-center gap-2 overflow-x-auto scrollbar-thin">
            {pills}
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              type="button"
              onClick={clearCompare}
              className="px-4 py-2 text-sm font-medium text-neutral-700 hover:text-neutral-900 transition-colors"
            >
              {t('compare.clear')}
            </button>
            {compareButton}
          </div>
        </div>
      </div>

      <span className="sr-only" aria-live="polite">
        {showHint ? t('compare.addMoreHint') : ''}
      </span>
    </div>
  )
}

export default CompareBar
