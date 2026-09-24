import { useCallback, useLayoutEffect, useRef } from 'react'

// Returns a function with a stable identity that always calls the latest `fn`.
// Lets memoized list items (school cards, map markers) skip re-rendering when
// the parent re-renders. Only call the result from event handlers, not during render.
export function useStableCallback(fn) {
  const fnRef = useRef(fn)
  useLayoutEffect(() => {
    fnRef.current = fn
  })
  return useCallback((...args) => fnRef.current?.(...args), [])
}
