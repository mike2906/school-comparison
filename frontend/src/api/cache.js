/**
 * Tiny in-memory GET cache so navigating back to a page renders from memory instead of
 * refetching and flashing skeletons. Data changes at most daily, so a short TTL is plenty.
 */

const TTL_MS = 10 * 60 * 1000

const entries = new Map()

/**
 * Fetch JSON through the cache. Concurrent callers share one request; failures are not
 * cached. `fetchImpl` and `now` exist for tests.
 */
export function cachedJson(url, errorLabel, { fetchImpl = fetch, now = Date.now } = {}) {
  const entry = entries.get(url)
  if (entry && now() - entry.at < TTL_MS) return entry.promise

  const promise = fetchImpl(url).then(response => {
    if (!response.ok) {
      throw new Error(`${errorLabel}: ${response.statusText}`)
    }
    return response.json()
  })
  const record = { at: now(), promise, data: undefined, resolved: false }
  entries.set(url, record)

  promise.then(
    data => {
      record.data = data
      record.resolved = true
    },
    () => {
      if (entries.get(url) === record) entries.delete(url)
    }
  )
  return promise
}

/** The cached response for `url` if it has already arrived and is fresh, else undefined. */
export function peekCachedJson(url, { now = Date.now } = {}) {
  const entry = entries.get(url)
  if (!entry || !entry.resolved || now() - entry.at >= TTL_MS) return undefined
  return entry.data
}

export function clearJsonCache() {
  entries.clear()
}
