import { createContext, useContext, useState, useEffect } from 'react'

const CompareContext = createContext()

const MAX_COMPARE = 4 // Maximum number of schools to compare

export function CompareProvider({ children }) {
  const [compareList, setCompareList] = useState(() => {
    // Load from localStorage on mount
    const saved = localStorage.getItem('compareList')
    return saved ? JSON.parse(saved) : []
  })

  // Save to localStorage whenever compareList changes
  useEffect(() => {
    localStorage.setItem('compareList', JSON.stringify(compareList))
  }, [compareList])

  const addToCompare = (school) => {
    setCompareList(prev => {
      // Don't add if already in list
      if (prev.find(s => s.id === school.id)) {
        return prev
      }
      // Don't add if list is full
      if (prev.length >= MAX_COMPARE) {
        return prev
      }
      return [...prev, school]
    })
  }

  const removeFromCompare = (schoolId) => {
    setCompareList(prev => prev.filter(s => s.id !== schoolId))
  }

  const clearCompare = () => {
    setCompareList([])
  }

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
