import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { searchSchools } from '../../api/schools'
import { getAddress, getSchoolName } from '../../utils/i18n'
import { normalizeSchoolList } from '../../utils/schoolAttributes'

const MAX_SUGGESTIONS = 6

/**
 * Name search on the results page. Typing filters the list instantly (the parent applies
 * `value`); the dropdown also finds schools outside the current filters and opens them.
 */
function SchoolNameSearch({ value, onChange, onOpenSchool, className = '', compact = false }) {
  const { t, i18n } = useTranslation()
  const containerRef = useRef(null)
  // Rendered twice (mobile and desktop toolbars), so ids must be unique per instance.
  const idBase = useId()
  const inputId = `${idBase}-input`
  const listId = `${idBase}-suggestions`
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const query = value.trim()

  useEffect(() => {
    if (query.length < 2) {
      setResults([])
      return undefined
    }
    let current = true
    const timer = setTimeout(() => {
      searchSchools(query)
        .then(data => { if (current) setResults(data) })
        .catch(() => { if (current) setResults([]) })
    }, 250)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [query])

  useEffect(() => {
    const handlePointer = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handlePointer)
    return () => document.removeEventListener('mousedown', handlePointer)
  }, [])

  const suggestions = useMemo(
    () => normalizeSchoolList(results, i18n.language).slice(0, MAX_SUGGESTIONS),
    [results, i18n.language]
  )
  const showDropdown = open && query.length >= 2 && suggestions.length > 0

  const choose = (school) => {
    setOpen(false)
    setActiveIndex(-1)
    onOpenSchool(school)
  }

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      if (showDropdown || value) event.preventDefault()
      if (showDropdown) setOpen(false)
      else if (value) onChange('')
      return
    }
    if (!showDropdown) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex(index => Math.min(index + 1, suggestions.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex(index => Math.max(index - 1, -1))
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault()
      choose(suggestions[activeIndex])
    }
  }

  const primaryAddress = (school) => {
    const location = school.locations?.find(item => item.is_primary) || school.locations?.[0]
    return location ? getAddress(location, i18n.language) : ''
  }

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <label className="sr-only" htmlFor={inputId}>{t('landing.searchPlaceholder')}</label>
      <svg className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
      <input
        id={inputId}
        type="search"
        value={value}
        onChange={(event) => {
          onChange(event.target.value)
          setOpen(true)
          setActiveIndex(-1)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder={compact ? t('nameSearch.placeholderShort') : t('landing.searchPlaceholder')}
        autoComplete="off"
        role="combobox"
        aria-expanded={showDropdown}
        aria-controls={listId}
        aria-activedescendant={activeIndex >= 0 ? `${idBase}-option-${suggestions[activeIndex]?.id}` : undefined}
        className="h-11 lg:h-10 w-full rounded-lg border border-neutral-300 bg-white pl-9 pr-3 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
      />
      {showDropdown && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-[1200] mt-1 max-h-80 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-xl"
        >
          <li className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-400">
            {t('nameSearch.openSchool')}
          </li>
          {suggestions.map((school, index) => (
            <li
              key={school.id}
              id={`${idBase}-option-${school.id}`}
              role="option"
              aria-selected={index === activeIndex}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(school)}
              className={`cursor-pointer px-3 py-2 ${index === activeIndex ? 'bg-primary-50' : 'hover:bg-neutral-50'}`}
            >
              <div className="text-sm font-medium text-neutral-900">{getSchoolName(school, i18n.language)}</div>
              <div className="text-xs text-neutral-500">
                {t(`schoolTypes.${school.school_type}`)}
                {primaryAddress(school) ? ` · ${primaryAddress(school)}` : ''}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default SchoolNameSearch
