/**
 * Shared set-up for the live user flows (`e2e/live/*.test.js`): a parent's journeys,
 * driven in a real browser against the deployed site and its API. Unlike `e2e/`, nothing
 * is recorded: each flow picks its school from the live API, so the flows follow the
 * data as it is published. Read-only: they only load pages and type into the page.
 *
 *   npm run test:live                                     # schooldecider.com
 *   LIVE_SITE_URL=http://localhost:5173 LIVE_API_URL=http://localhost:8000 npm run test:live
 *
 * A failed flow leaves a screenshot in LIVE_OUT (default live-flows-report/).
 */
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { after, afterEach, before, beforeEach } from 'node:test'
import { chromium } from 'playwright'

export const SITE = (process.env.LIVE_SITE_URL || 'https://schooldecider.com').replace(/\/+$/, '')
export const API = (process.env.LIVE_API_URL || 'https://api.schooldecider.com').replace(/\/+$/, '')
const OUT = path.resolve(process.env.LIVE_OUT || 'live-flows-report')
export const PHONE = { width: 390, height: 844 }
export const DESKTOP = { width: 1280, height: 900 }

const dictionaries = Object.fromEntries(
  await Promise.all(['bg', 'en'].map(async lang => [lang, JSON.parse(await readFile(new URL(`../../src/i18n/${lang}.json`, import.meta.url)))])),
)

/** The page's text for `key` in `lang`, so a copy change does not break a flow. */
export function t(lang, key, vars = {}) {
  const value = key.split('.').reduce((node, part) => node?.[part], dictionaries[lang])
  if (typeof value !== 'string') throw new Error(`No ${lang} text for ${key}`)
  return value.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name) => String(vars[name] ?? ''))
}

/** `/en` for English, nothing for Bulgarian (the URL decides the language). */
export const prefix = lang => (lang === 'en' ? '/en' : '')

export { getAddress as address, getSchoolName as schoolName } from '../../src/utils/i18n.js'

/**
 * A live API answer, asked up to three times. A request through the edge can stall for a
 * minute while the next one takes a second (Cloudflare's Ashburn trouble on 28 Oct 2026
 * failed every PR's coverage check that way), so each try gets 20 s and a fresh request.
 * An HTTP answer is returned as it is, errors included: only a stall or a dropped
 * connection is tried again, and after the last try the error says it was the network.
 */
export async function fetchLive(url, tries = 3) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      // The body is read here too, so a stall half-way through counts as a stall.
      const response = await fetch(url, { signal: AbortSignal.timeout(20_000) })
      const body = await response.text()
      return { ok: response.ok, status: response.status, json: () => JSON.parse(body) }
    } catch (error) {
      if (attempt >= tries) throw new Error(`The live API did not answer ${url} in ${tries} tries (${error.message}): the network, not the site`)
      await new Promise(resolve => setTimeout(resolve, 2_000 * attempt))
    }
  }
}

export async function fetchJson(url) {
  const response = await fetchLive(url)
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return response.json()
}

let listing
/** Every published Sofia school, as the search page gets them. */
export function allSchools() {
  listing ??= fetchJson(`${API}/schools?country_code=bg&city=sofia`)
  return listing
}

const details = new Map()
export function schoolDetail(id) {
  if (!details.has(id)) details.set(id, fetchJson(`${API}/schools/${id}`))
  return details.get(id)
}

/**
 * The first school whose detail answer passes `test`, trying the listed schools that pass
 * `listTest` in order. Fails the flow when none does: a feature with nothing to show on
 * the live site is worth knowing about too.
 */
export async function findSchool(what, test, listTest = () => true, limit = 80) {
  const candidates = (await allSchools()).filter(listTest).slice(0, limit)
  // A few at a time, in list order, so the first match is always the same school.
  for (let i = 0; i < candidates.length; i += 8) {
    const batch = await Promise.all(candidates.slice(i, i + 8).map(candidate => schoolDetail(candidate.id)))
    const found = batch.find(test)
    if (found) return found
  }
  throw new NoSchoolError(`No live school ${what} (checked ${candidates.length})`)
}

export class NoSchoolError extends Error {}

let browser
export let page
const requestErrors = []

/** Opens the browser once per file and a fresh page per flow, at `viewport`. */
export function liveBrowser(viewport = PHONE) {
  before(async () => {
    await mkdir(OUT, { recursive: true })
    browser = await chromium.launch()
  })
  after(async () => {
    await browser?.close()
  })
  beforeEach(async () => {
    requestErrors.length = 0
    const context = await browser.newContext({ viewport, isMobile: viewport === PHONE, hasTouch: viewport === PHONE })
    page = await context.newPage()
    page.setDefaultTimeout(20_000)
    page.on('pageerror', error => requestErrors.push(`page error: ${error.message}`))
    page.on('response', response => {
      // The site's own API failing is a broken flow; a third party's tile is not.
      if (response.status() >= 500 && response.url().startsWith(API)) requestErrors.push(`${response.url()} HTTP ${response.status()}`)
    })
  })
  afterEach(async testContext => {
    const failed = testContext.passed === false || requestErrors.length > 0
    if (failed) {
      const file = `${testContext.name.replace(/[^\w]+/g, '_').slice(0, 80)}.png`
      await page.screenshot({ path: path.join(OUT, file), fullPage: true }).catch(() => {})
      console.log(`Screenshot of the failed flow: ${path.join(OUT, file)} (${page.url()})`)
    }
    await page.context().close()
    if (requestErrors.length) throw new Error(`Errors while the flow ran:\n${requestErrors.join('\n')}`)
  })
}

/** Opens a site path in `lang`, waiting for the page's own requests to settle. */
export async function open(lang, sitePath) {
  await page.goto(`${SITE}${prefix(lang)}${sitePath}`, { waitUntil: 'networkidle', timeout: 45_000 })
}

/** A number as the page prints it ("12", "12,5", "1 234.5", with any kind of space) as a JS number. */
export function number(text) {
  const cleaned = String(text).replace(/\s/g, '').replace(',', '.')
  const value = Number(cleaned)
  if (!Number.isFinite(value)) throw new Error(`Not a number: ${JSON.stringify(text)}`)
  return value
}
