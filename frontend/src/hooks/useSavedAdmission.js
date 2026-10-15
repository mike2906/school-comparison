import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { getSaved, subscribeSaved } from '../admission/saved'
import { savedVerdictFor } from '../admission/schoolVerdict'
import { sofiaToday } from '../admission/calculator'
import { loadCatchments, loadDistricts } from '../admission/loadCatchments'

/** The answers saved on this device (src/admission/saved.js), updated when they change. */
export function useSavedAdmission() {
  return useSyncExternalStore(subscribeSaved, getSaved, getSaved)
}

// One load per page view, shared by every card. Rule files come in their own chunk.
let dataPromise = null
function loadVerdictData() {
  if (!dataPromise) {
    dataPromise = Promise.all([
      import('../admission/ruleSets').then(module => module.RULE_SETS),
      loadDistricts(),
      loadCatchments().then(data => data.byId),
    ]).then(([ruleSets, districts, catchments]) => ({ ruleSets, districts, catchments }))
    dataPromise.catch(() => {
      dataPromise = null
    })
  }
  return dataPromise
}

function useVerdictData(enabled) {
  const [data, setData] = useState(null)
  useEffect(() => {
    if (!enabled) return undefined
    let live = true
    loadVerdictData().then(loaded => live && setData(loaded), () => {})
    return () => {
      live = false
    }
  }, [enabled])
  return data
}

/**
 * The saved answers against one school's cut-offs (src/admission/schoolVerdict.js), or
 * null without saved answers that apply. Nothing loads until something is saved.
 */
export function useSavedVerdict(school, ageGroup = null) {
  const saved = useSavedAdmission()
  const hasSaved = Boolean(saved.kindergarten || saved.grade_1)
  const data = useVerdictData(hasSaved)
  return useMemo(() => {
    if (!hasSaved || !data || !school) return null
    return savedVerdictFor(school, saved, ageGroup, { ...data, today: sofiaToday() })
  }, [hasSaved, data, school, saved, ageGroup])
}
