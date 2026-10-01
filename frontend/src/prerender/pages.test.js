import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { hasCanonical } from '../utils/languageUrl.js'
import {
  SUMMARY_MIN_CHARS,
  buildRobots,
  buildSitemap,
  createTranslator,
  fixedPages,
  indexReasons,
  isThinSchool,
  outputFile,
  renderPage,
  schoolPage,
} from './pages.js'

const ORIGIN = 'https://example.test'
const TEMPLATE = '<!doctype html>\n<html lang="bg">\n  <head>\n    <title>App</title>\n  </head>\n  <body>\n    <div id="root"></div>\n  </body>\n</html>'
const translator = (language) =>
  createTranslator(JSON.parse(readFileSync(new URL(`../i18n/${language}.json`, import.meta.url), 'utf8')))
const t = translator('en')

const exam = (exam_type, year, subject, value) => ({ exam_type, year, subject, metric: 'average_score', value })
const school = (overrides = {}) => ({
  id: 12,
  school_type: 'state',
  education_level: 'lower_secondary',
  resolved_name_i18n: { bg: 'Училище', en: 'School <One>' },
  locations: [{ is_primary: true, age_groups: ['grade_5_7'], address_i18n: { en: '1 Main St' } }],
  exam_results: [],
  pricing: [],
  summary_i18n: null,
  ...overrides,
})
const withNvo = school({
  exam_results: [
    exam('nvo_7', 2024, 'bulgarian', 50),
    exam('nvo_7', 2025, 'bulgarian', 82.63),
    exam('nvo_7', 2025, 'math', 71.16),
  ],
})

test('createTranslator resolves dotted keys and interpolates', () => {
  const tr = createTranslator({ a: { b: 'Hi {{name}}' } })
  assert.equal(tr('a.b', { name: 'Ana' }), 'Hi Ana')
  assert.equal(tr('a.missing'), 'a.missing')
})

test('a school with only a name, type and address is thin', () => {
  assert.equal(isThinSchool(school()), true)
  const page = schoolPage(school(), t, 'en')
  assert.equal(page.noindex, true)
  assert.equal(page.sitemap, false)
})

test('a school the site does not list keeps a page but is not indexed', () => {
  const page = schoolPage(withNvo, t, 'en', { listed: false })
  assert.equal(page.noindex, true)
  assert.equal(page.sitemap, false)
})

test('the app and the prerender agree on which fixed routes carry a canonical', () => {
  for (const page of fixedPages(t, 'en')) {
    if (page.routePath === '/') continue
    assert.equal(hasCanonical(page.routePath), page.canonicalPath !== null, page.routePath)
  }
})

test('NVO results or a published price alone make a page indexable', () => {
  assert.deepEqual(indexReasons(withNvo), { nvo: true, pricing: false, summary: false })
  assert.equal(isThinSchool(school({ pricing: [{ id: 1, category: 'tuition' }] })), false)
})

test('only a substantive Bulgarian summary counts, measured on trimmed plain text', () => {
  const long = 'а'.repeat(SUMMARY_MIN_CHARS)
  assert.equal(isThinSchool(school({ summary_i18n: { bg: { long } } })), false)
  assert.equal(isThinSchool(school({ summary_i18n: { bg: long } })), false)
  assert.equal(isThinSchool(school({ summary_i18n: { bg: `  ${long.slice(1)}   ` } })), true)
  assert.equal(isThinSchool(school({ summary_i18n: { bg: `<p>${long.slice(1)}</p>` } })), true)
  assert.equal(isThinSchool(school({ summary_i18n: { en: { long } } })), true)
})

test('a state school leads with its latest official NVO results', () => {
  const page = schoolPage(withNvo, t, 'en')
  assert.match(page.description, /^7th Grade NVO 2025: Bulgarian 82\.6, maths 71\.2\. State/)
  assert.ok(page.body.indexOf('Official NVO results') < page.body.indexOf('1 Main St'))
  assert.doesNotMatch(page.body, /2024/)
  assert.equal(page.noindex, false)
})

test('a private school leads with its facts, not NVO', () => {
  const page = schoolPage({ ...withNvo, school_type: 'private' }, t, 'en')
  assert.match(page.description, /^Private/)
  assert.ok(page.body.indexOf('1 Main St') < page.body.indexOf('Official NVO results'))
})

test('school data is escaped in the body and the head', () => {
  const page = schoolPage(withNvo, t, 'en')
  assert.match(page.body, /School &lt;One&gt;/)
  const html = renderPage(TEMPLATE, page, { language: 'en', origin: ORIGIN, siteName: 'Site' })
  assert.match(html, /<title>School &lt;One&gt; · Sofia Schools<\/title>/)
  assert.doesNotMatch(html, /<One>/)
})

test('renderPage stamps language, canonical, hreflang and Open Graph', () => {
  const html = renderPage(TEMPLATE, schoolPage(withNvo, t, 'en'), { language: 'en', origin: ORIGIN, siteName: 'Site' })
  assert.match(html, /<html lang="en">/)
  assert.match(html, /<link rel="canonical" href="https:\/\/example\.test\/en\/schools\/12" \/>/)
  assert.match(html, /<link rel="alternate" hreflang="bg" href="https:\/\/example\.test\/schools\/12" \/>/)
  assert.match(html, /<link rel="alternate" hreflang="x-default" href="https:\/\/example\.test\/schools\/12" \/>/)
  assert.match(html, /<meta property="og:url" content="https:\/\/example\.test\/en\/schools\/12" \/>/)
  assert.match(html, /<meta property="og:locale" content="en_GB" \/>/)
  assert.doesNotMatch(html, /name="robots"/)
  assert.match(html, /<div id="root"><main/)
})

test('renderPage marks thin pages noindex and fails on an unexpected template', () => {
  const html = renderPage(TEMPLATE, schoolPage(school(), t, 'en'), { language: 'en', origin: ORIGIN, siteName: 'Site' })
  assert.match(html, /<meta name="robots" content="noindex, follow" \/>/)
  assert.throws(() => renderPage('<html lang="bg"><head><title>x</title></head></html>', schoolPage(school(), t, 'en'), {
    language: 'en', origin: ORIGIN, siteName: 'Site',
  }), /#root not found/)
})

test('fixed pages: aliases canonicalise to /search, compare and 404 are noindex without canonical', () => {
  for (const language of ['bg', 'en']) {
    const pages = Object.fromEntries(fixedPages(translator(language), language).map((page) => [page.routePath, page]))
    assert.equal(pages['/'].canonicalPath, '/search')
    assert.ok(!pages['/'].sitemap)
    assert.deepEqual([pages['/search'].sitemap, pages['/about'].sitemap], [true, true])
    for (const path of ['/compare', '/404']) {
      assert.equal(pages[path].noindex, true)
      assert.equal(pages[path].canonicalPath, null)
      const html = renderPage(TEMPLATE, pages[path], { language, origin: ORIGIN, siteName: 'Site' })
      assert.doesNotMatch(html, /rel="canonical"|hreflang/)
    }
  }
})

test('outputFile writes flat files, /en/ as a directory index, and keeps 404.html for not-found', () => {
  assert.equal(outputFile('/', 'bg'), 'index.html')
  assert.equal(outputFile('/', 'en'), 'en/index.html')
  assert.equal(outputFile('/search', 'en'), 'en/search.html')
  assert.equal(outputFile('/schools/12', 'bg'), 'schools/12.html')
  assert.equal(outputFile('/404', 'en'), 'en/404.html')
  assert.equal(outputFile('/schools/404', 'bg'), 'schools/school-404.html')
  assert.equal(outputFile('/schools/404', 'en'), 'en/schools/school-404.html')
})

test('public/_redirects rewrites the id-404 school to the file outputFile writes', () => {
  const redirects = readFileSync(new URL('../../public/_redirects', import.meta.url), 'utf8')
  for (const language of ['bg', 'en']) {
    const prefix = language === 'en' ? '/en' : ''
    const target = `/${outputFile('/schools/404', language).replace(/\.html$/, '')}`
    assert.ok(redirects.includes(`${prefix}/schools/404 ${target} 200`))
  }
})

test('buildSitemap lists every path in both languages with reciprocal alternates', () => {
  const xml = buildSitemap(['/search', '/schools/12'], ORIGIN)
  assert.equal(xml.match(/<url>/g).length, 4)
  assert.match(xml, /<loc>https:\/\/example\.test\/en\/schools\/12<\/loc>/)
  assert.equal(xml.match(/hreflang="x-default" href="https:\/\/example\.test\/schools\/12"/g).length, 2)
})

test('buildRobots allows search and AI answers, opts out of training, and names the sitemap', () => {
  const robots = buildRobots(ORIGIN)
  assert.match(robots, /Content-Signal: search=yes, ai-input=yes, ai-train=no, use=reference/)
  assert.match(robots, /^Allow: \/$/m)
  assert.doesNotMatch(robots, /Disallow/)
  assert.match(robots, /^Sitemap: https:\/\/example\.test\/sitemap\.xml$/m)
})
