/**
 * Phone-width scan of the rendered site: opens every school page (and the search, about
 * and compare pages) in Bulgarian and English at 390 px and reports what a parent should
 * never see: raw translation keys, leaked NaN/undefined, text spilling out of its box,
 * lev prices, implausible or missing prices, UI labels in the wrong language, console
 * errors and failed requests. Read-only: it only loads pages.
 *
 *   npm run scan:ui                                   # the live site
 *   SCAN_SITE_URL=http://localhost:5173 SCAN_API_URL=http://localhost:8000 npm run scan:ui
 *
 * Options (env): SCAN_SITE_URL, SCAN_API_URL, SCAN_LIMIT (first N schools),
 * SCAN_CONCURRENCY (default 4), SCAN_OUT (report folder, default ui-scan-report).
 * Writes report.md, report.json and a screenshot per page with findings; exits 1 when
 * anything was found.
 */
import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

import { buildDictionaryIndex, textFindings } from './ui-scan-checks.js'

const SITE = (process.env.SCAN_SITE_URL || 'https://schooldecider.com').replace(/\/+$/, '')
const API = (process.env.SCAN_API_URL || 'https://api.schooldecider.com').replace(/\/+$/, '')
const LIMIT = Number(process.env.SCAN_LIMIT) || Infinity
const CONCURRENCY = Number(process.env.SCAN_CONCURRENCY) || 4
const OUT = path.resolve(process.env.SCAN_OUT || 'ui-scan-report')
const VIEWPORT = { width: 390, height: 844 }
const LANGUAGE_PREFIX = { bg: '', en: '/en' }

async function fetchJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(30_000) })
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return response.json()
}

async function pageList() {
  const schools = (await fetchJson(`${API}/schools?country_code=bg&city=all`)).slice(0, LIMIT)
  const paths = [{ path: '/search' }, { path: '/about' }]
  for (const school of schools) paths.push({ path: `/schools/${school.id}`, schoolId: school.id })
  // Private and international schools three at a time: the compare page is where fees sit side by side.
  const privateIds = schools.filter(school => ['private', 'international'].includes(school.school_type)).map(school => school.id)
  for (let i = 0; i < privateIds.length; i += 3) paths.push({ path: `/compare?ids=${privateIds.slice(i, i + 3).join(',')}` })
  return Object.keys(LANGUAGE_PREFIX).flatMap(lang =>
    paths.map(page => ({ ...page, lang, url: `${SITE}${LANGUAGE_PREFIX[lang]}${page.path}` })),
  )
}

/**
 * Runs in the page. Returns the visible strings and the text that leaves its box: past
 * its nearest block container, or off the side of the screen, unless the box is meant to
 * scroll or cut text with an ellipsis. The map is skipped.
 */
function inspectPage() {
  const SKIP = 'script, style, noscript, svg, .leaflet-container'
  const viewport = document.documentElement.clientWidth
  const texts = []
  // Text the page marks as another language on purpose (TagList's "In Bulgarian" tags).
  const otherLanguageTexts = []
  const pageLang = document.documentElement.lang
  const overflow = []

  const describe = el => {
    const parts = []
    for (let node = el; node && node !== document.body && parts.length < 4; node = node.parentElement) {
      parts.unshift(node.tagName.toLowerCase() + (node.id ? `#${node.id}` : ''))
    }
    return parts.join(' > ')
  }
  const visible = el => {
    const style = getComputedStyle(el)
    return style.visibility !== 'hidden' && style.display !== 'none' && el.getClientRects().length > 0
  }
  const container = el => {
    let node = el
    while (node && ['inline', 'contents'].includes(getComputedStyle(node).display)) node = node.parentElement
    return node
  }
  // Text inside a box that scrolls sideways or ends in an ellipsis may be wider than the
  // box on purpose; text wholly outside a clipping box (a carousel's hidden column, sr-only
  // text) is not on screen at all. Neither is a finding.
  const intended = (el, box) => {
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      const style = getComputedStyle(node)
      if (['auto', 'scroll'].includes(style.overflowX) || style.textOverflow === 'ellipsis') return true
      if (style.overflowX !== 'visible' || style.clip !== 'auto') {
        const clip = node.getBoundingClientRect()
        if (clip.width <= 1 || box.left >= clip.right || box.right <= clip.left) return true
      }
    }
    return false
  }

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.replace(/\s+/g, ' ').trim()
    const parent = node.parentElement
    if (!text || !parent || parent.closest(SKIP) || !visible(parent)) continue
    texts.push(text)
    const marked = parent.closest('[lang]')
    if (marked && marked !== document.documentElement && marked.lang !== pageLang) otherLanguageTexts.push(text)

    const range = document.createRange()
    range.selectNodeContents(node)
    const box = range.getBoundingClientRect()
    if (!box.width || intended(parent, box)) continue
    const block = container(parent)
    const blockBox = block.getBoundingClientRect()
    if (box.right > blockBox.right + 2 || box.left < blockBox.left - 2) {
      overflow.push({ text: text.slice(0, 120), where: describe(block), by: Math.round(Math.max(box.right - blockBox.right, blockBox.left - box.left)) })
    } else if (box.right > viewport + 1 || box.left < -1) {
      overflow.push({ text: text.slice(0, 120), where: describe(block), by: Math.round(Math.max(box.right - viewport, -box.left)) })
    }
  }
  for (const el of document.querySelectorAll('[placeholder], [aria-label], [title], img[alt]')) {
    if (el.closest(SKIP)) continue
    for (const attribute of ['placeholder', 'aria-label', 'title', 'alt']) {
      const value = el.getAttribute(attribute)?.trim()
      if (value) texts.push(value)
    }
  }
  return { texts, otherLanguageTexts, overflow, pageWidth: document.documentElement.scrollWidth, viewport }
}

/** One line per host and reason: a map that fails to load is one finding, not forty. */
function collapseRequestFailures(findings) {
  const groups = new Map()
  for (let i = findings.length - 1; i >= 0; i--) {
    if (findings[i].check !== 'request-failed') continue
    const [url, ...reason] = findings[i].text.split(' ')
    const key = `${new URL(url).host} ${reason.join(' ')}`
    const group = groups.get(key) || { count: 0, example: url }
    group.count++
    groups.set(key, group)
    findings.splice(i, 1)
  }
  for (const [key, group] of groups) {
    findings.push({ check: 'request-failed', text: group.count > 1 ? `${key}: ${group.count} requests, e.g. ${group.example}` : `${key}: ${group.example}` })
  }
}

async function scanPage(context, page, index) {
  const tab = await context.newPage()
  const findings = []
  let schoolPayload = null
  tab.on('console', message => {
    // A failed load is reported once, with its URL, as request-failed.
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) findings.push({ check: 'console-error', text: message.text().slice(0, 300) })
  })
  tab.on('pageerror', error => findings.push({ check: 'page-error', text: String(error.message).slice(0, 300) }))
  tab.on('requestfailed', request => {
    const reason = request.failure()?.errorText || ''
    // Requests cancelled by the page itself (navigation, superseded fetches) are not failures.
    if (!reason.includes('ERR_ABORTED')) findings.push({ check: 'request-failed', text: `${request.url()} ${reason}` })
  })
  tab.on('response', async response => {
    if (response.status() >= 400) findings.push({ check: 'request-failed', text: `${response.url()} HTTP ${response.status()}` })
    // The API's answer, not the page's own HTML at the same path.
    if (page.schoolId && response.ok() && response.url().startsWith(`${API}/schools/${page.schoolId}`)) {
      schoolPayload = await response.json().catch(() => schoolPayload)
    }
  })

  try {
    await tab.goto(page.url, { waitUntil: 'networkidle', timeout: 45_000 })
  } catch (error) {
    findings.push({ check: 'page-load', text: error.message.split('\n')[0] })
  }
  // Open collapsed sections (fees, details) so their text is checked too.
  for (const button of await tab.locator('button[aria-expanded="false"]').all()) {
    await button.click({ timeout: 2_000 }).catch(() => {})
  }
  await tab.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {})

  const result = await tab.evaluate(inspectPage)
  collapseRequestFailures(findings)
  findings.push(...textFindings([...new Set(result.texts)], {
    lang: page.lang, index, schoolPayload, otherLanguageTexts: new Set(result.otherLanguageTexts),
  }))
  for (const item of result.overflow) {
    findings.push({ check: 'text-overflow', text: `${item.text} (${item.by}px past ${item.where})` })
  }
  if (result.pageWidth > result.viewport + 1) {
    findings.push({ check: 'page-scrolls-sideways', text: `page is ${result.pageWidth}px wide on a ${result.viewport}px screen` })
  }

  let screenshot = null
  if (findings.length) {
    screenshot = `${page.lang}${page.path.replace(/[^\w]+/g, '_')}.png`
    await tab.screenshot({ path: path.join(OUT, screenshot), fullPage: true }).catch(() => { screenshot = null })
  }
  await tab.close()
  return { url: page.url, lang: page.lang, findings, screenshot }
}

function markdownReport(results, startedAt) {
  const withFindings = results.filter(result => result.findings.length)
  const counts = {}
  for (const result of withFindings) for (const finding of result.findings) counts[finding.check] = (counts[finding.check] || 0) + 1
  const lines = [
    `# UI scan of ${SITE}`,
    '',
    `${startedAt} · ${results.length} pages at ${VIEWPORT.width}px · ${withFindings.length} with findings`,
    '',
    ...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([check, count]) => `- ${check}: ${count}`),
  ]
  for (const result of withFindings) {
    lines.push('', `## ${result.url}`, '')
    if (result.screenshot) lines.push(`Screenshot: ${result.screenshot}`, '')
    for (const finding of result.findings) lines.push(`- **${finding.check}**: ${finding.text.replace(/\n/g, ' ')}`)
  }
  return lines.join('\n') + '\n'
}

async function main() {
  const startedAt = new Date().toISOString()
  await mkdir(OUT, { recursive: true })
  const dictionaries = Object.fromEntries(
    await Promise.all(['bg', 'en'].map(async lang => [lang, JSON.parse(await readFile(new URL(`../src/i18n/${lang}.json`, import.meta.url)))])),
  )
  const index = buildDictionaryIndex(dictionaries)
  const pages = await pageList()
  console.log(`Scanning ${pages.length} pages of ${SITE} (API ${API})`)

  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: VIEWPORT, isMobile: true, hasTouch: true, deviceScaleFactor: 2 })
  const results = []
  let next = 0
  await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
    while (next < pages.length) {
      const page = pages[next++]
      const result = await scanPage(context, page, index)
      results.push(result)
      if (result.findings.length) {
        console.log(`${result.findings.length} finding(s): ${result.url}`)
        for (const finding of result.findings) console.log(`  ${finding.check}: ${finding.text.replace(/\n/g, ' ')}`)
      }
    }
  }))
  await browser.close()

  results.sort((a, b) => a.url.localeCompare(b.url))
  await writeFile(path.join(OUT, 'report.json'), JSON.stringify(results, null, 2))
  const report = markdownReport(results, startedAt)
  await writeFile(path.join(OUT, 'report.md'), report)
  // On GitHub Actions, show the report on the run's page too (the summary is capped at 1 MiB).
  if (process.env.GITHUB_STEP_SUMMARY) {
    const limit = 900_000
    const summary = report.length > limit ? `${report.slice(0, limit)}\n\n…truncated; the full report is in the run's artifact.\n` : report
    await appendFile(process.env.GITHUB_STEP_SUMMARY, summary)
  }
  const total = results.reduce((sum, result) => sum + result.findings.length, 0)
  console.log(`${total} finding(s) on ${results.filter(r => r.findings.length).length} of ${results.length} pages. Report: ${path.join(OUT, 'report.md')}`)
  process.exitCode = total ? 1 : 0
}

main().catch(error => {
  console.error(error)
  process.exitCode = 2
})
