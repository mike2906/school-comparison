/**
 * Browser journeys for the search page's URL and history handling (Lane D R3b).
 *
 * `SearchPage` keeps its state and the URL in step through ordered effects and refs;
 * these drive a real browser through the paths a parent takes and check both. They run
 * against the production build (`npm run build` first) served by `vite preview`, with the
 * API answered from `e2e/fixtures/api.json`, so no backend is needed.
 *
 *   npm run build && npm run test:journeys
 *
 * To re-record the API answers from a local backend (seeded demo data):
 *
 *   E2E_RECORD_API=http://localhost:8000 npm run test:journeys
 */
import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import { after, before, beforeEach, afterEach, describe, test } from 'node:test'
import { chromium } from 'playwright'
import { loadEnv, preview } from 'vite'

const FIXTURES = new URL('./fixtures/api.json', import.meta.url)
const RECORD_FROM = process.env.E2E_RECORD_API?.replace(/\/+$/, '')
const SEARCH_PLACEHOLDER = 'Търсене на конкретно училище...'
// A state school with a detail page in the fixtures, and a name the search finds once.
const SCHOOL_ID = 95
const SCHOOL_BG = '18 СУ „Уилям Гладстон“'
const SCHOOL_EN = '18 SU "Uilyam Gladston"'
const QUERY = 'Славейков'

let server
let site
let browser
let page
let answers = {}
const unanswered = new Set()

async function answerApi(route) {
  const url = new URL(route.request().url())
  const key = url.pathname.replace(/^\/api/, '') + url.search
  if (route.request().method() !== 'GET') {
    unanswered.add(`${route.request().method()} ${key}`)
    return route.fulfill({ status: 405, contentType: 'application/json', body: '{"detail":"GET only"}' })
  }
  if (RECORD_FROM) {
    const response = await fetch(RECORD_FROM + key)
    const body = await response.text()
    if (response.ok) answers[key] = JSON.parse(body)
    return route.fulfill({ status: response.status, contentType: 'application/json', body })
  }
  if (!(key in answers)) {
    unanswered.add(key)
    return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not recorded"}' })
  }
  return route.fulfill({ contentType: 'application/json', body: JSON.stringify(answers[key]) })
}

const path = () => {
  const url = new URL(page.url())
  return decodeURIComponent(url.pathname + url.search)
}
const isSearch = response => response.url().includes('/api/schools/search?')
const resultCount = () => page.locator('text=/^\\d+ резултат/').first().innerText()
const typeButton = name => page.getByRole('button', { name, exact: true })

before(async () => {
  // The answers are served for the build's own `/api` requests; a build for a real API
  // origin would bypass them (and test against that API).
  const apiBase = loadEnv('production', process.cwd(), 'VITE_').VITE_API_BASE
  if (apiBase && apiBase.replace(/\/+$/, '') !== '/api') {
    throw new Error(`Build with VITE_API_BASE unset (it is ${apiBase}): the journeys answer /api themselves`)
  }
  // A re-recording adds to the answers, so recording one journey keeps the others'.
  answers = JSON.parse(await readFile(FIXTURES, 'utf8'))
  server = await preview({ preview: { port: 0, strictPort: false }, logLevel: 'silent' })
  site = server.resolvedUrls.local[0].replace(/\/+$/, '')
  browser = await chromium.launch()
})

after(async () => {
  await browser?.close()
  if (server) await new Promise(resolve => server.httpServer.close(resolve))
  if (RECORD_FROM) await writeFile(FIXTURES, `${JSON.stringify(answers, null, 2)}\n`)
})

async function openPage(options = { viewport: { width: 1280, height: 900 } }) {
  page = await browser.newPage(options)
  await page.route('**/api/**', answerApi)
  // Map tiles are not part of any journey.
  await page.route(/basemaps\.cartocdn\.com|tile\.openstreetmap\.org/, route => route.abort())
}

beforeEach(async () => {
  unanswered.clear()
  await openPage()
})

afterEach(async () => {
  await page.close()
  assert.deepEqual([...unanswered], [], 'every API request has a recorded answer')
})

describe('search page history', () => {
  test('a filter survives Back and Forward', async () => {
    await page.goto(`${site}/search`)
    const all = await resultCount()
    await typeButton('Частно').click()
    await page.waitForURL(/school_type=private/)
    const privateOnly = await resultCount()
    assert.notEqual(privateOnly, all)

    await page.getByRole('link', { name: 'За данните' }).click()
    await page.waitForURL(/\/about/)
    await page.goBack()
    await page.waitForURL(/\/search\?school_type=private/)
    assert.equal(await typeButton('Частно').getAttribute('aria-pressed'), 'true')
    assert.equal(await resultCount(), privateOnly)

    await page.goForward()
    await page.waitForURL(/\/about/)
    await page.goBack()
    await page.waitForURL(/\/search\?school_type=private/)
    assert.equal(await resultCount(), privateOnly)

    // Clearing the filter replaces the entry rather than adding one.
    const entries = await page.evaluate(() => history.length)
    await typeButton('Всички').click()
    await page.waitForURL(url => !url.search.includes('school_type'))
    assert.equal(await resultCount(), all)
    assert.equal(await page.evaluate(() => history.length), entries)
  })

  test('Back closes an open school panel and Forward reopens it', async () => {
    await page.goto(`${site}/search?school_type=state`)
    await resultCount()
    await page.locator(`a[href="/schools/${SCHOOL_ID}"]`).filter({ hasText: 'Детайли' }).click()
    await page.waitForURL(new RegExp(`detail=${SCHOOL_ID}`))
    await page.getByText(SCHOOL_BG, { exact: true }).first().waitFor()
    await page.getByText('Обратно към резултатите').waitFor()

    // Back closes the panel and leaves the school it showed selected (`school=`), written
    // into the same entry once the page has caught up.
    await page.goBack()
    await page.waitForURL(url => !url.searchParams.has('detail') && url.searchParams.get('school') === String(SCHOOL_ID))
    assert.deepEqual(Object.fromEntries(new URL(page.url()).searchParams), {
      school_type: 'state',
      school: String(SCHOOL_ID),
    })
    await page.getByText('Обратно към резултатите').waitFor({ state: 'detached' })

    await page.goForward()
    await page.waitForURL(new RegExp(`detail=${SCHOOL_ID}`))
    await page.getByText('Обратно към резултатите').waitFor()
  })

  test('a name query survives a reload', async () => {
    await page.goto(`${site}/search`)
    await resultCount()
    // The name box also asks the API for matches (debounced); wait for that answer too.
    await Promise.all([
      page.waitForResponse(isSearch),
      page.getByPlaceholder(SEARCH_PLACEHOLDER).first().fill(QUERY),
    ])
    await page.waitForURL(url => url.searchParams.get('q') === QUERY)
    await page.locator('text=/^1 резултат/').first().waitFor()

    await Promise.all([page.waitForResponse(isSearch), page.reload()])
    assert.equal(path(), `/search?q=${QUERY}`)
    assert.equal(await page.getByPlaceholder(SEARCH_PLACEHOLDER).first().inputValue(), QUERY)
    await page.locator('text=/^1 резултат/').first().waitFor()
  })

  test('opening a school while the map still zooms to it raises no error', async () => {
    // Leaflet ends a zoom from a 250 ms timer that outlived the map, so opening a school
    // right after a name search threw on the school page (leafletGuard.js). On a phone the
    // result opens the school's own page, and the map fits even while the list tab hides it.
    await page.close()
    await openPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    // Only leaving the search page matters here, not what the school page shows.
    await page.route(url => /^\/api\/schools\/\d+$/.test(url.pathname), route => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }))
    await page.goto(`${site}/search`)
    await resultCount()
    await page.getByPlaceholder('Име или №').filter({ visible: true }).first().fill(QUERY)
    // Tap the result in the same tick the map starts zooming to it.
    await page.waitForFunction(() => {
      if (!document.querySelector('.leaflet-zoom-anim')) return false
      document.querySelector('a[href^="/schools/"]').click()
      return true
    }, null, { polling: 'raf' })
    await page.waitForURL(url => url.pathname.startsWith('/schools/'))
    await page.waitForTimeout(500)
    assert.deepEqual(errors, [])
  })

  test('switching language keeps the open panel', async () => {
    await page.goto(`${site}/search?detail=${SCHOOL_ID}`)
    await page.getByText(SCHOOL_BG, { exact: true }).first().waitFor()

    await page.getByRole('link', { name: 'EN', exact: true }).click()
    await page.waitForURL(new RegExp(`/en/search\\?detail=${SCHOOL_ID}`))
    await page.getByText(SCHOOL_EN, { exact: true }).first().waitFor()
    await page.getByText('Back to results').waitFor()

    await page.goBack()
    await page.waitForURL(url => url.pathname === '/search' && url.searchParams.get('detail') === String(SCHOOL_ID))
    await page.getByText(SCHOOL_BG, { exact: true }).first().waitFor()
  })
})
