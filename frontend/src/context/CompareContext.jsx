import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import {
  MAX_COMPARE,
  loadCompareList,
  safeLocalStorage,
  saveCompareList,
  syncCompareList as syncEntries,
  toCompareEntry,
} from '../utils/compareList'

const CompareContext = createContext()

export function CompareProvider({ children }) {
  // Entries are {id, name_i18n, resolved_name_i18n, school_type}; old full-object lists are
  // trimmed on load. The compare page fetches fresh data by id.
  const [compareList, setCompareList] = useState(() => loadCompareList(safeLocalStorage()))

  useEffect(() => {
    saveCompareList(safeLocalStorage(), compareList)
  }, [compareList])

  const addToCompare = useCallback((school) => {
    const entry = toCompareEntry(school)
    if (!entry) return
    setCompareList(prev => {
      if (prev.some(s => s.id === entry.id) || prev.length >= MAX_COMPARE) {
        return prev
      }
      return [...prev, entry]
    })
  }, [])

  const removeFromCompare = useCallback((schoolId) => {
    setCompareList(prev => prev.filter(s => s.id !== schoolId))
  }, [])

  const clearCompare = useCallback(() => {
    setCompareList([])
  }, [])

  // Refresh stored names from a compare response and drop ids the API no longer returns.
  const syncCompareList = useCallback((requestedIds, freshSchools) => {
    setCompareList(prev => syncEntries(prev, requestedIds, freshSchools))
  }, [])

  const isInCompare = (schoolId) => {
    return compareList.some(s => s.id === schoolId)
  }

  const canAddMore = compareList.length < MAX_COMPARE

  return (
    <CompareContext.Provider
      value={{
        compareList,
        addToCompare,
        removeFromCompare,
        clearCompare,
        syncCompareList,
        isInCompare,
        canAddMore,
        maxCompare: MAX_COMPARE,
      }}
    >
      {children}
    </CompareContext.Provider>
  )
}

export function useCompare() {
  const context = useContext(CompareContext)
  if (!context) {
    throw new Error('useCompare must be used within a CompareProvider')
  }
  return context
}
