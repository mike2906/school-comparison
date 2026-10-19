import { useEffect, useState } from 'react'

/**
 * The results sort while a name is typed: "match" (best name match first) unless the
 * parent picks another sort then. Clearing the name goes back to `sortBy`, the sort the
 * page keeps in the URL, which a pick made while typing leaves alone.
 */
export function useQuerySort(nameQuery, sortBy, setSortBy) {
  const [querySort, setQuerySort] = useState(null)
  const hasNameQuery = nameQuery.trim() !== ''
  useEffect(() => {
    if (!hasNameQuery) setQuerySort(null)
  }, [hasNameQuery])
  return {
    hasNameQuery,
    activeSort: hasNameQuery ? (querySort ?? 'match') : sortBy,
    setActiveSort: hasNameQuery ? setQuerySort : setSortBy,
    // Drops a pick made while typing that is no longer offered (distance without a location).
    dropQuerySort: values => setQuerySort(current => (values.includes(current) ? null : current)),
  }
}
