import test from 'node:test'
import assert from 'node:assert/strict'

import { formatCheckedDate, safeSourceUrl, safeWebsiteUrl, sharedPriceSource, sourceSiteName } from './priceSources.js'

const PAGE = 'https://roerich-school.org/%d1%82%d0%b0%d0%ba%d1%81%d0%b8/'

test('safeSourceUrl: only http(s) links', () => {
  assert.equal(safeSourceUrl(PAGE), PAGE)
  assert.equal(safeSourceUrl('javascript:alert(1)'), null)
  assert.equal(safeSourceUrl('not a url'), null)
  assert.equal(safeSourceUrl(null), null)
})

test('safeWebsiteUrl: http(s) or a bare host, never another scheme', () => {
  assert.equal(safeWebsiteUrl('https://school.bg/'), 'https://school.bg/')
  assert.equal(safeWebsiteUrl(' www.school.bg '), 'https://www.school.bg/')
  assert.equal(safeWebsiteUrl('школа.бг/за-нас'), new URL('https://школа.бг/за-нас').href)
  assert.equal(safeWebsiteUrl('javascript:alert(1)'), null)
  assert.equal(safeWebsiteUrl('JavaScript:alert(1)'), null)
  assert.equal(safeWebsiteUrl('data:text/html,x'), null)
  assert.equal(safeWebsiteUrl('not a url'), null)
  assert.equal(safeWebsiteUrl(null), null)
})

test('sourceSiteName: the site without www', () => {
  assert.equal(sourceSiteName(PAGE), 'roerich-school.org')
  assert.equal(sourceSiteName('https://www.example.bg/fees'), 'example.bg')
  assert.equal(sourceSiteName('ftp://example.bg/fees'), null)
})

test('formatCheckedDate: a day in the UI language, null when unusable', () => {
  assert.match(formatCheckedDate('2026-09-17T19:31:08.418464', 'en'), /17 Sept? 2026/)
  assert.match(formatCheckedDate('2026-09-17T19:31:08', 'bg'), /17\.09\.2026|17 септ/)
  assert.equal(formatCheckedDate('nonsense', 'en'), null)
  assert.equal(formatCheckedDate(null, 'en'), null)
})

test('sharedPriceSource: one page and one kind of source, latest check', () => {
  const rows = [
    { source: 'scraped_website', source_url: PAGE, scraped_at: '2026-09-17T19:31:08' },
    { source: 'scraped_website', source_url: PAGE, scraped_at: '2026-09-18T08:00:00' },
  ]
  assert.deepEqual(sharedPriceSource(rows), {
    url: PAGE,
    source: 'scraped_website',
    checkedAt: '2026-09-18T08:00:00',
  })
})

test('sharedPriceSource: null when rows differ, so each row shows its own', () => {
  const a = { source: 'scraped_website', source_url: PAGE, scraped_at: '2026-09-17T19:31:08' }
  assert.equal(sharedPriceSource([a, { ...a, source_url: 'https://roerich-school.org/other/' }]), null)
  assert.equal(sharedPriceSource([a, { ...a, source: 'official' }]), null)
  assert.equal(sharedPriceSource([]), null)
})

test('sharedPriceSource: no stored link still shares the source kind, with no url', () => {
  const rows = [{ source: 'official', source_url: null, scraped_at: null }]
  assert.deepEqual(sharedPriceSource(rows), { url: null, source: 'official', checkedAt: null })
})
