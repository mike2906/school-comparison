import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { getSaved, subscribeSaved } from '../admission/saved'
import { savedVerdictsFor } from '../admission/schoolVerdict'
import { sofiaToday } from '../admission/calculator'
import { loadCatchments, loadDistricts } from '../admission/loadCatchments'

/** The answers saved on this device (src/admission/saved.js), updated when they change. */
export function useSavedAdmission() {
  return useSyncExternalStore(subscribeSaved, getSaved, getSaved)
}

// Rule files come in their own chunk; the snapshots are cached by loadCatchments.
let ruleSetsPromise = null
function loadRuleSets() {
  ruleSetsPromise ??= import('../admission/ruleSets').then(module => module.RULE_SETS)
  ruleSetsPromise.catch(() => {
    ruleSetsPromise = null
  })
  return ruleSetsPromise
}

/**
 * What the saved tabs need, loaded once: districts for kindergartens, catchments for first
 * grade. A file that fails leaves only its own tab without results.
 */
function useVerdictData(needsDistricts, needsCatchments) {
  const [data, setData] = useState(null)
  useEffect(() => {
    if (!needsDistricts && !needsCatchments) return undefined
    let live = true
    const optional = (needed, load) => (needed ? load().catch(() => null) : Promise.resolve(null))
    Promise.all([
      loadRuleSets(),
      optional(needsDistricts, loadDistricts),
      optional(needsCatchments, () => loadCatchments().then(loaded => loaded.byId)),
    ]).then(
      ([ruleSets, districts, catchments]) => live && setData({ ruleSets, districts, catchments }),
      () => {}
    )
    return () => {
      live = false
    }
  }, [needsDistricts, needsCatchments])
  return data
}

/**
 * The saved answers against one school's cut-offs (src/admission/schoolVerdict.js), one
 * per saved tab that applies; empty without any. Nothing loads until something is saved.
 */
export function useSavedVerdicts(school, ageGroup = null) {
  const saved = useSavedAdmission()
  const hasSaved = Boolean(saved.kindergarten || saved.grade_1)
  const data = useVerdictData(Boolean(saved.kindergarten), Boolean(saved.grade_1))
  return useMemo(() => {
    if (!hasSaved || !data || !school) return []
    return savedVerdictsFor(school, saved, ageGroup, { ...data, today: sofiaToday() })
  }, [hasSaved, data, school, saved, ageGroup])
}
