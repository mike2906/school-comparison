import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import SchoolDetailPage from '../SchoolDetailPage/SchoolDetailPage'

/**
 * A school's detail over the search page, so the map, list, filters and scroll position
 * stay put. Desktop: a panel over the filters + list with the map still visible. Below
 * `lg` it only appears for a shared `?detail=` link, as a full-screen sheet.
 * In map-only view it sits beside the map (`beside`) rather than over it.
 */
function SchoolDetailPanel({ schoolId, onClose, beside = false }) {
  const { t } = useTranslation()
  const panelRef = useRef(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    panelRef.current?.focus()
    if (scrollRef.current) scrollRef.current.scrollTop = 0
  }, [schoolId])

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div
      ref={panelRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="false"
      aria-label={t('detailPanel.label')}
      // Over the filters + list in the list views; `beside` the map in map-only view, so the
      // map shrinks instead of hiding the highlighted school under the panel.
      className={`fixed inset-0 z-[2100] flex flex-col bg-neutral-100 outline-none lg:z-[1100] lg:border-r lg:border-neutral-200 ${
        beside
          ? 'lg:static lg:w-[min(760px,50%)] lg:flex-shrink-0'
          : 'lg:absolute lg:inset-y-0 lg:left-0 lg:right-auto lg:w-[calc(20rem+40%)] lg:shadow-2xl'
      }`}
    >
      <div className="flex items-center justify-between gap-3 border-b border-neutral-200 bg-white px-4 py-2">
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-10 items-center gap-2 rounded-lg px-2 text-sm font-medium text-neutral-700 hover:bg-neutral-100"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t('detailPanel.backToResults')}
        </button>
        <Link
          to={`/schools/${schoolId}`}
          className="inline-flex h-10 items-center gap-1 rounded-lg px-2 text-sm font-medium text-primary-700 hover:bg-primary-50"
        >
          {t('detailPanel.openFullPage')}
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </Link>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <SchoolDetailPage key={schoolId} schoolId={String(schoolId)} embedded onClose={onClose} />
      </div>
    </div>
  )
}

export default SchoolDetailPanel
