// Drive-time settings and formatting shared by the worker, the hook and the cards.

/** Whole-city road graph, built monthly by .github/workflows/road-graph.yml. */
export const ROAD_GRAPH_URL = '/routing/sofia-roads.bin.gz'

// Free-flow times are scaled by Sofia's morning rush-hour factor (TomTom Traffic Index, the
// public city figure) until road-level factors from bus data exist. See docs/DRIVE_TIMES.md.
export const RUSH_HOUR_FACTOR = 1.73

// A start further than this from any road is outside the area the graph covers.
export const MAX_START_METRES = 1000

/** Rush-hour minutes for display, from free-flow seconds; null when unknown. */
export function rushHourMinutes(freeFlowSeconds) {
  if (!Number.isFinite(freeFlowSeconds)) return null
  return Math.max(1, Math.round((freeFlowSeconds * RUSH_HOUR_FACTOR) / 60))
}
