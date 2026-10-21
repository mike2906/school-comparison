/**
 * Tiny in-memory GET cache so navigating back to a page renders from memory instead of
 * refetching and flashing skeletons. Data changes at most daily, so a short TTL is plenty.
 */

const TTL_MS = 10 * 60 * 1000

// A deploy or publish restarts the API for a few seconds; Cloudflare then answers 502-524
// or the connection drops. Wait and ask again rather than tell a parent the school is gone.
const RETRY_DELAYS_MS = [1000, 2000, 4000]
const isRetryable = status => status === 502 || status === 503 || status === 504 || (status >= 520 && status <= 524)

const entries = new Map()

async function fetchJson(url, errorLabel, fetchImpl, sleep) {
  for (let attempt = 0; ; attempt += 1) {
    let response
    try {
      response = await fetchImpl(url)
    } catch (err) {
      if (attempt >= RETRY_DELAYS_MS.length) throw err
    }
    if (response) {
      if (response.ok) return response.json()
      if (!isRetryable(response.status) || attempt >= RETRY_DELAYS_MS.length) {
        const error = new Error(`${errorLabel}: ${response.statusText}`)
        error.status = response.status
        throw error
      }
    }
    await sleep(RETRY_DELAYS_MS[attempt])
  }
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms))

/**
 * Fetch JSON through the cache. Concurrent callers share one request; failures are not
 * cached, and a failed request carries the HTTP `status` when there was a response.
 * `fetchImpl`, `now` and `sleep` exist for tests.
 */
export function cachedJson(url, errorLabel, { fetchImpl = fetch, now = Date.now, sleep = wait } = {}) {
  const entry = entries.get(url)
  if (entry && now() - entry.at < TTL_MS) return entry.promise

  const promise = fetchJson(url, errorLabel, fetchImpl, sleep)
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
