// k6 load test: simulated parents on the live API (README.md in this folder).
// Each virtual user is one parent browsing at a parent's pace, so the number of virtual
// users is the number of people using the site at the same moment.
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate } from 'k6/metrics'

const API = (__ENV.API_URL || 'https://api.schooldecider.com').replace(/\/+$/, '')
const PEAK = Number(__ENV.PEAK || 1000)
if (!Number.isInteger(PEAK) || PEAK < 1) throw new Error(`PEAK must be a whole number above 0, got ${__ENV.PEAK}`)
// BYPASS_CACHE=1: every request asks past Cloudflare's copy, as right after a publish,
// so the test measures the server alone.
const BYPASS = Boolean(__ENV.BYPASS_CACHE)
const SCOPE = 'country_code=bg&city=sofia'

// Share of answers Cloudflare served from its copy (also a stale one while it refreshes).
const edgeHit = new Rate('edge_hit')
const EDGE_SERVED = new Set(['HIT', 'UPDATING', 'STALE', 'REVALIDATED'])

export const options = {
  // Half the peak, hold, then the full peak, hold. QUICK=1: a short check of the script.
  stages: __ENV.QUICK ? [{ duration: '20s', target: PEAK }] : [
    { duration: '3m', target: Math.round(PEAK / 2) },
    { duration: '4m', target: Math.round(PEAK / 2) },
    { duration: '3m', target: PEAK },
    { duration: '4m', target: PEAK },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    // Stop early, not only fail at the end: this runs against production.
    http_req_failed: [{ threshold: 'rate<0.02', abortOnFail: true, delayAbortEval: '1m' }],
    http_req_duration: [{ threshold: 'p(95)<3000', abortOnFail: true, delayAbortEval: '1m' }],
    // One per request kind, so the summary shows each.
    'http_req_duration{name:list}': ['p(95)<2000'],
    'http_req_duration{name:filters}': ['p(95)<2000'],
    'http_req_duration{name:counts}': ['p(95)<2000'],
    'http_req_duration{name:exam_averages}': ['p(95)<2000'],
    'http_req_duration{name:search}': ['p(95)<2000'],
    'http_req_duration{name:school}': ['p(95)<2000'],
    'http_req_duration{name:compare}': ['p(95)<2000'],
  },
  summaryTrendStats: ['med', 'p(95)', 'max', 'count'],
}

// The site's request for these: gzip, as a browser asks.
const PARAMS = { headers: { 'Accept-Encoding': 'gzip, deflate, br' } }

function url(path) {
  return BYPASS ? `${API}${path}${path.includes('?') ? '&' : '?'}load=${__VU}-${__ITER}-${Date.now()}` : `${API}${path}`
}

function record(response, name) {
  check(response, { [`${name} 200`]: (r) => r.status === 200 })
  edgeHit.add(EDGE_SERVED.has(response.headers['Cf-Cache-Status']), { name })
}

function get(path, name) {
  const response = http.get(url(path), { ...PARAMS, tags: { name } })
  record(response, name)
  return response
}

// Requests the browser starts together, sent in parallel as it does: [path, name] pairs.
function getAll(requests) {
  const responses = http.batch(requests.map(([path, name]) => ['GET', url(path), null, { ...PARAMS, tags: { name } }]))
  responses.forEach((response, i) => record(response, requests[i][1]))
}

export function setup() {
  const list = http.get(`${API}/schools?${SCOPE}`, PARAMS).json()
  if (!Array.isArray(list) || list.length === 0) throw new Error('the API returned no schools')
  return {
    ids: list.map((school) => school.id),
    names: list.map((school) => (school.name_i18n && school.name_i18n.bg) || '').filter((name) => name.length >= 6),
  }
}

function pick(items) {
  return items[Math.floor(Math.random() * items.length)]
}

function pickDistinct(items, count) {
  const picked = new Set()
  while (picked.size < Math.min(count, items.length)) picked.add(pick(items))
  return [...picked]
}

// As the site's URLSearchParams writes it (a space is "+"), so the edge cache keys match.
function queryValue(text) {
  return encodeURIComponent(text).replace(/%20/g, '+')
}

// What the search box sends while a parent types a name: one request per pause.
function typedQueries(name) {
  const text = name.replace(/["„“]/g, '').trim()
  return [...new Set([4, 9, 16].map((length) => text.slice(0, length).trim()))].filter((q) => q.length >= 2)
}

export default function (data) {
  // Opening the search page; the age picker (counts) is opened by about half.
  const pageLoad = [
    [`/schools?${SCOPE}`, 'list'],
    [`/schools/filters?${SCOPE}`, 'filters'],
    [`/schools/exam-averages?${SCOPE}`, 'exam_averages'],
  ]
  if (Math.random() < 0.5) pageLoad.push([`/schools/counts?${SCOPE}`, 'counts'])
  getAll(pageLoad)
  sleep(5 + Math.random() * 10)

  // Most parents look a school up by name.
  if (Math.random() < 0.6) {
    for (const q of typedQueries(pick(data.names))) {
      get(`/schools/search?q=${queryValue(q)}&${SCOPE}`, 'search')
      sleep(0.5 + Math.random())
    }
  }

  // A few school pages, read for a while.
  const opened = pickDistinct(data.ids, 2 + Math.floor(Math.random() * 3))
  for (const id of opened) {
    get(`/schools/${id}`, 'school')
    sleep(15 + Math.random() * 30)
  }

  if (opened.length >= 2 && Math.random() < 0.3) {
    get(`/compare?ids=${opened.slice(0, 3).join(',')}`, 'compare')
    sleep(10 + Math.random() * 20)
  }
}
