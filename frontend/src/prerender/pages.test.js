import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { hasCanonical } from '../utils/languageUrl.js'
import {
  SUMMARY_MIN_CHARS,
  buildRobots,
  buildSitemap,
  createTranslator,
  districtPages,
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
  createTranslator(JSON.parse(readFileSync(new URL(`../i18n/${language}.json`, import.meta.url), 'utf8')), language)
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
  assert.deepEqual(indexReasons(withNvo), { nvo: true, pricing: false, facts: false, admission: false, summary: false })
  assert.equal(isThinSchool(school({ pricing: [{ id: 1, category: 'tuition' }] })), false)
})

const kindergarten = school({
  education_level: 'kindergarten',
  school_facts: {
    facts: [
      { topic: 'facilities', rank: 'key', text_i18n: { bg: 'Има басейн.', en: 'It has a <pool>.' } },
      { topic: 'meals', rank: 'more', text_i18n: { bg: 'Собствена кухня.' } },
    ],
  },
  admission_info: {
    cutoff_summary: [
      {
        stage: 'kindergarten',
        age_group: 'nursery',
        birth_year: 2024,
        campaign: '2026/2027',
        rounds: 1,
        first: {
          round_id: 1, round_kind: 'current', classified_on: '2026-09-04',
          places: 2, applicants_count: 34, admitted_count: 2, last_admitted_points: 19,
        },
      },
    ],
  },
})

test('facts from the school site or official admission rankings make a page indexable', () => {
  assert.equal(isThinSchool(school({ school_facts: kindergarten.school_facts })), false)
  assert.equal(isThinSchool(school({ admission_info: kindergarten.admission_info })), false)
  assert.equal(isThinSchool(school({ admission_info: { cutoff_summary: [{ stage: 'grade_1', rounds: 0 }] } })), true)
  const noPlaces = { ...kindergarten.admission_info.cutoff_summary[0], first: { round_id: 2, round_kind: 'current', places: 0 } }
  assert.equal(isThinSchool(school({ admission_info: { cutoff_summary: [noPlaces] } })), true)
  assert.equal(isThinSchool(school({ admission_info: { cutoff_summary: {} } })), true)
  const onlyMore = { facts: [kindergarten.school_facts.facts[1]] }
  assert.equal(isThinSchool(school({ school_facts: onlyMore })), true)
})

test('a page lists its site facts and admission results, escaped', () => {
  const page = schoolPage(kindergarten, t, 'en')
  assert.match(page.body, /From the school's website.*Facilities: It has a &lt;pool&gt;\./)
  assert.doesNotMatch(page.body, /Собствена кухня/)
  assert.match(schoolPage(kindergarten, translator('bg'), 'bg').body, /Собствена кухня/)
  assert.match(page.body, /Who got in before.*Nursery · born 2024 \(2026\/27\): Free-place ranking, 4 September 2026, last admitted: 19 points \(2 places · 34 applicants · 2 admitted\)/)
  assert.match(page.description, /, 1 Main St\. It has a <pool>\.$/)
  assert.equal(page.sitemap, true)
})

test('the translator picks plural keys by count', () => {
  const tr = createTranslator({ n_one: '{{count}} place', n_other: '{{count}} places', plain: 'x' }, 'en')
  assert.equal(tr('n', { count: 1 }), '1 place')
  assert.equal(tr('n', { count: 3 }), '3 places')
  assert.equal(tr('plain', { count: 3 }), 'x')
  assert.equal(tr('missing', { defaultValue: 'fallback' }), 'fallback')
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
    assert.deepEqual([pages['/search'].sitemap, pages['/about'].sitemap, pages['/admission'].sitemap], [true, true, true])
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

test('/schools lists schools then kindergartens by name, linked in the page language', () => {
  const listed = [
    school({ id: 3, resolved_name_i18n: { en: '10th School' } }),
    school({ id: 4, resolved_name_i18n: { en: '„2nd School“' } }),
    school({ id: 5, education_level: 'kindergarten', resolved_name_i18n: { en: 'Bee <Garden>' } }),
    school({ id: 6, resolved_name_i18n: {} }),
  ]
  const page = (language) => fixedPages(translator(language), language, listed).find((p) => p.routePath === '/schools')
  const { body, sitemap, canonicalPath } = page('en')
  assert.deepEqual([sitemap, canonicalPath], [true, '/schools'])
  assert.deepEqual([...body.matchAll(/href="([^"]+)"/g)].map((m) => m[1]), ['/en/districts', '/en/schools/4', '/en/schools/3', '/en/schools/5'])
  assert.ok(body.indexOf('Schools (2)') < body.indexOf('Kindergartens (1)'))
  assert.match(body, /Bee &lt;Garden&gt;/)
  assert.match(page('bg').body, /href="\/schools\/5"/)
  const search = fixedPages(t, 'en', listed).find((p) => p.routePath === '/search')
  assert.match(search.body, /<a href="\/en\/schools">All schools and kindergartens<\/a>/)
})

const districts = {
  type: 'FeatureCollection',
  features: [
    { properties: { name: 'Младост' }, geometry: { type: 'Polygon', coordinates: [[[23, 42], [24, 42], [24, 43], [23, 43], [23, 42]]] } },
  ],
}
const pinned = school({
  website_url: 'https://school.example',
  locations: [{ is_primary: true, lat: 42.5, lng: 23.5, phone: '02/824 11 70', address_i18n: { en: '1 Main St</script>' } }],
})

test('a school page names its district in the title, from the source or the pin', () => {
  assert.match(schoolPage(pinned, t, 'en', { districts }).title, /^School <One>, Mladost district · /)
  const named = school({ admission_info: { admin_district: 'Младост' }, locations: [] })
  assert.match(schoolPage(named, translator('bg'), 'bg', { districts }).title, /^Училище, район Младост · /)
  assert.doesNotMatch(schoolPage(school(), t, 'en', { districts }).title, /district/)
})

test('a school page carries schema.org data that cannot break out of its script', () => {
  const page = schoolPage(pinned, t, 'en', { districts, origin: ORIGIN })
  assert.deepEqual(page.jsonLd, {
    '@context': 'https://schema.org',
    '@type': 'School',
    name: 'School <One>',
    alternateName: 'Училище',
    url: 'https://example.test/en/schools/12',
    sameAs: 'https://school.example',
    telephone: '+35928241170',
    address: {
      '@type': 'PostalAddress',
      streetAddress: '1 Main St</script>',
      addressLocality: 'Sofia',
      addressRegion: 'Mladost district',
      addressCountry: 'BG',
    },
    geo: { '@type': 'GeoCoordinates', latitude: 42.5, longitude: 23.5 },
  })
  assert.equal(schoolPage(kindergarten, t, 'en', { origin: ORIGIN }).jsonLd['@type'], 'Preschool')
  const html = renderPage(TEMPLATE, page, { language: 'en', origin: ORIGIN, siteName: 'Site' })
  const script = html.match(/<script type="application\/ld\+json">(.*?)<\/script>/)[1]
  assert.doesNotMatch(script, /</)
  assert.equal(JSON.parse(script).address.streetAddress, '1 Main St</script>')
})

test('district pages list each district with its schools, kindergartens and headline results', () => {
  const listed = [
    pinned,
    { ...kindergarten, id: 13, resolved_name_i18n: { en: 'Bee Garden' }, school_type: 'state', locations: pinned.locations },
    school({ id: 14, locations: [{ is_primary: true, lat: 10, lng: 10 }] }),
  ]
  const pages = districtPages(t, 'en', listed, districts)
  assert.deepEqual(pages.map((page) => page.routePath), ['/districts', '/districts/mladost'])
  assert.match(pages[0].body, /<a href="\/en\/districts\/mladost">Mladost district<\/a> · 2 kindergartens and schools/)
  const page = pages[1]
  assert.equal(page.title, 'Kindergartens and schools in Mladost district · Sofia Schools')
  assert.ok(page.sitemap && page.canonicalPath === '/districts/mladost')
  assert.match(page.body, /Schools \(1\).*School &lt;One&gt;.*Kindergartens \(1\).*same district \(Mladost district\) get 3 extra points.*Bee Garden.*<br \/>1 Main St&lt;\/script&gt;/)
  assert.doesNotMatch(page.body, /schools\/14/)
  assert.deepEqual(districtPages(t, 'en', [], districts), [])
})
