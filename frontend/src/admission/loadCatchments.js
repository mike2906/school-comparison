/**
 * Loads a campaign's catchment snapshot from public/catchments/ once per page view.
 * Plain GETs of static files: nothing about the parent is sent.
 */
import {
  catchmentUrl,
  indexAddresses,
  indexCatchments,
} from './catchments.js'

/** Campaigns with a published catchment snapshot, newest first. */
export const CATCHMENT_CAMPAIGNS = ['2026/2027']

const cache = new Map()

function load(campaign, file, prepare) {
  const key = `${campaign}:${file}`
  if (!cache.has(key)) {
    const promise = fetch(catchmentUrl(campaign, file))
      .then(response => {
        if (!response.ok) throw new Error(`Failed to load ${file}`)
        return response.json()
      })
      .then(prepare)
    // A failed load can be retried on the next request.
    promise.catch(() => cache.delete(key))
    cache.set(key, promise)
  }
  return cache.get(key)
}

export function loadCatchments(campaign = CATCHMENT_CAMPAIGNS[0]) {
  return load(campaign, 'catchments.json', data => ({ meta: data.meta, byId: indexCatchments(data) }))
}

export function loadDistricts(campaign = CATCHMENT_CAMPAIGNS[0]) {
  return load(campaign, 'districts.json', data => data)
}

export function loadAddresses(campaign = CATCHMENT_CAMPAIGNS[0]) {
  return load(campaign, 'addresses.json', data => ({ meta: data, streets: indexAddresses(data) }))
}
