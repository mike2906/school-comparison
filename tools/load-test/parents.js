// k6 load test: simulated parents on the live API (README.md in this folder).
// Each virtual user is one parent browsing at a parent's pace, so the number of virtual
// users is the number of people using the site at the same moment.
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate } from 'k6/metrics'

const API = (__ENV.API_URL || 'https://api.schooldecider.com').replace(/\/+$/, '')
const PEAK = Number(__ENV.PEAK || 1000)
const SCOPE = 'country_code=bg&city=sofia'

// Share of answers Cloudflare served from its copy.
const edgeHit = new Rate('edge_hit')

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
    'http_req_duration{name:country}': ['p(95)<2000'],
    'http_req_duration{name:exam_averages}': ['p(95)<2000'],
    'http_req_duration{name:search}': ['p(95)<2000'],
    'http_req_duration{name:school}': ['p(95)<2000'],
    'http_req_duration{name:compare}': ['p(95)<2000'],
  },
  summaryTrendStats: ['med', 'p(95)', 'max', 'count'],
}

// The site's request for these: gzip, as a browser asks.
const PARAMS = { headers: { 'Accept-Encoding': 'gzip, deflate, br' } }

function get(path, name) {
  const response = http.get(`${API}${path}`, { ...PARAMS, tags: { name } })
  check(response, { [`${name} 200`]: (r) => r.status === 200 })
  edgeHit.add(response.headers['Cf-Cache-Status'] === 'HIT', { name })
  return response
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

// What the search box sends while a parent types a name: one request per pause.
function typedQueries(name) {
  const text = name.replace(/["„“]/g, '').trim()
  return [...new Set([4, 9, 16].map((length) => text.slice(0, length).trim()))].filter((q) => q.length >= 2)
}

export default function (data) {
  // Opening the search page.
  get(`/schools?${SCOPE}`, 'list')
  get(`/schools/filters?${SCOPE}`, 'filters')
  get(`/schools/counts?${SCOPE}`, 'counts')
  get('/countries/bg', 'country')
  get(`/schools/exam-averages?${SCOPE}`, 'exam_averages')
  sleep(5 + Math.random() * 10)

  // Most parents look a school up by name.
  if (Math.random() < 0.6) {
    for (const q of typedQueries(pick(data.names))) {
      get(`/schools/search?q=${encodeURIComponent(q)}&${SCOPE}`, 'search')
      sleep(0.5 + Math.random())
    }
  }

  // A few school pages, read for a while.
  const opened = []
  for (let i = 0; i < 2 + Math.floor(Math.random() * 3); i += 1) {
    const id = pick(data.ids)
    opened.push(id)
    get(`/schools/${id}`, 'school')
    sleep(15 + Math.random() * 30)
  }

  if (opened.length >= 2 && Math.random() < 0.3) {
    get(`/compare?ids=${opened.slice(0, 3).join(',')}`, 'compare')
    sleep(10 + Math.random() * 20)
  }
}
