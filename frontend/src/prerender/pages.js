/**
 * Static HTML for crawlers and link previews: per-page head tags plus a small content
 * block inside `#root` that React replaces on mount (hidden until then, see index.html).
 * Pure builders; the file and network work lives in `scripts/prerender.js`.
 */
import { getAddress, getSchoolName } from '../utils/i18n.js'
import { URL_LANGUAGES, alternateLinks, canonicalUrl, languagePath } from '../utils/languageUrl.js'
import { schoolLevelLabel, schoolTypeLabel } from '../utils/levelLabel.js'
import {
  getAvailableExamTypes,
  getExamTypeForEducationLevel,
  getExamTypeLabel,
  getNvoSubjectKey,
  isAverageMetric,
} from '../utils/nvo.js'
import { statedTuition } from '../utils/pricing.js'

// Our own thin-content heuristic, not an SEO rule: a published Bulgarian summary shorter
// than this says little beyond the name, type and address the page already shows.
export const SUMMARY_MIN_CHARS = 300

const DESCRIPTION_MAX_CHARS = 160
const OG_LOCALES = { bg: 'bg_BG', en: 'en_GB' }
const BODY_CLASS = 'mx-auto max-w-3xl space-y-3 px-4 py-8 text-neutral-900'

/** A minimal `t()` over one locale file: dotted keys and `{{name}}` interpolation. */
export function createTranslator(dictionary) {
  return (key, values = {}) => {
    const template = key.split('.').reduce((node, part) => node?.[part], dictionary)
    if (typeof template !== 'string') return key
    return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (_match, name) => String(values[name] ?? ''))
  }
}

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function plainText(value) {
  return String(value ?? '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
}

/** The published summary in exactly this language (no fallback to another one). */
function summaryText(school, language) {
  const localized = school?.summary_i18n?.[language]
  const text = typeof localized === 'string' ? localized : localized?.long || localized?.short
  return plainText(text)
}

function truncate(text, max = DESCRIPTION_MAX_CHARS) {
  if (text.length <= max) return text
  const cut = text.slice(0, max - 1)
  const lastSpace = cut.lastIndexOf(' ')
  return `${(lastSpace > max / 2 ? cut.slice(0, lastSpace) : cut).replace(/[\s.,;:–-]+$/, '')}…`
}

/** Latest-year average scores per exam type, lowest grade first. */
function latestNvoResults(school) {
  const results = Array.isArray(school?.exam_results) ? school.exam_results : []
  return getAvailableExamTypes(results).flatMap((examType) => {
    const rows = results.filter((row) => row.exam_type === examType && isAverageMetric(row.metric))
    const year = Math.max(...rows.map((row) => row.year))
    const scores = {}
    rows
      .filter((row) => row.year === year)
      .forEach((row) => {
        const subject = getNvoSubjectKey(row.subject)
        const value = Number(row.value)
        if (subject && Number.isFinite(value)) scores[subject] = value
      })
    return Object.keys(scores).length > 0 ? [{ examType, year, scores }] : []
  })
}

function nvoLine(result, t) {
  const scores = [
    result.scores.bulgarian != null && t('seo.nvoBulgarian', { value: result.scores.bulgarian.toFixed(1) }),
    result.scores.math != null && t('seo.nvoMath', { value: result.scores.math.toFixed(1) }),
  ].filter(Boolean)
  return t('seo.nvoLine', {
    exam: getExamTypeLabel(result.examType, t),
    year: result.year,
    scores: scores.join(', '),
  })
}

function tuitionLine(school, t) {
  const tuition = statedTuition(school?.pricing)
  // Euro only, in the period the school states (never annualised), and never an older
  // year's fee, which the description has no room to label as such.
  if (!tuition || tuition.currency !== 'EUR' || !tuition.period || tuition.yearStatus === 'dated_other') return ''
  const [min, max] = [tuition.min, tuition.max].map((value) => Math.round(value))
  const period = t(`pricing.per.${tuition.period}`)
  return min === max ? t('seo.tuitionOne', { min, period }) : t('seo.tuitionRange', { min, max, period })
}

/**
 * What makes a school page worth indexing: official NVO results, a published price, or a
 * substantive published summary. A page with none of them is thin (`noindex`).
 */
export function indexReasons(school) {
  return {
    nvo: latestNvoResults(school).length > 0,
    pricing: Array.isArray(school?.pricing) && school.pricing.length > 0,
    summary: summaryText(school, 'bg').length >= SUMMARY_MIN_CHARS,
  }
}

export function isThinSchool(school) {
  return !Object.values(indexReasons(school)).some(Boolean)
}

// The host serves the nearest `404.html` for an unknown URL, so the page of the school
// whose id is 404 cannot be `schools/404.html`: every unknown `/schools/*` URL would show
// that school. It is written under this name and public/_redirects rewrites its URL to it.
export const SCHOOL_404_FILE_STEM = 'school-404'

/** Output file of a route path: flat `.html` files, so URLs carry no trailing slash. */
export function outputFile(routePath, language) {
  const prefix = languagePath('/', language).slice(1)
  if (routePath === '/') return `${prefix}index.html`
  if (routePath === '/schools/404') return `${prefix}schools/${SCHOOL_404_FILE_STEM}.html`
  return `${prefix}${routePath.slice(1)}.html`
}

function mainBlock(parts) {
  return `<main data-prerender class="${BODY_CLASS}">${parts.filter(Boolean).join('')}</main>`
}

function heading(text) {
  return `<h1 class="text-2xl font-bold">${escapeHtml(text)}</h1>`
}

function paragraph(text) {
  return text ? `<p>${escapeHtml(text)}</p>` : ''
}

/** The pages that exist without school data, including the two aliases of `/search`. */
export function fixedPages(t, language) {
  const siteName = t('nav.title')
  const search = {
    title: t('seo.searchTitle'),
    description: t('seo.searchDescription'),
    canonicalPath: '/search',
    body: mainBlock([heading(t('seo.searchTitle')), paragraph(t('seo.searchDescription'))]),
  }
  const searchLink = `<p><a href="${escapeHtml(languagePath('/search', language))}">${escapeHtml(t('notFound.cta'))}</a></p>`
  return [
    { ...search, routePath: '/search', sitemap: true },
    // `/` redirects to `/search` (public/_redirects); this file is only the fallback.
    { ...search, routePath: '/' },
    {
      routePath: '/about',
      title: `${t('about.title')} · ${siteName}`,
      description: truncate(t('about.intro')),
      canonicalPath: '/about',
      sitemap: true,
      body: mainBlock([heading(t('about.title')), paragraph(t('about.intro'))]),
    },
    {
      routePath: '/compare',
      title: `${t('compare.title')} · ${siteName}`,
      description: t('seo.searchDescription'),
      canonicalPath: null,
      noindex: true,
      body: '',
    },
    {
      routePath: '/404',
      title: `${t('notFound.title')} · ${siteName}`,
      description: t('notFound.body'),
      canonicalPath: null,
      noindex: true,
      body: mainBlock([heading(t('notFound.title')), paragraph(t('notFound.body')), searchLink]),
    },
  ]
}

/**
 * A school's page. State schools lead with their official NVO results. A school the site
 * does not list (`listed: false`, outside the launch city) keeps a working URL but is not
 * offered to search engines.
 */
export function schoolPage(school, t, language, { listed = true } = {}) {
  const name = getSchoolName(school, language)
  const facts = t('seo.schoolFacts', {
    type: schoolTypeLabel(school, t),
    level: schoolLevelLabel(school, t),
  })
  const locations = Array.isArray(school.locations) ? school.locations : []
  const primary = locations.find((location) => location.is_primary) || locations[0]
  const addresses = [primary, ...locations.filter((location) => location !== primary)]
    .map((location) => getAddress(location, language))
    .filter(Boolean)
  const nvo = latestNvoResults(school)
  const reasons = indexReasons(school)
  const summary = reasons.summary ? summaryText(school, language) || summaryText(school, 'bg') : ''
  const tuition = tuitionLine(school, t)
  const leadWithNvo = school.school_type === 'state' && nvo.length > 0

  const levelExam = getExamTypeForEducationLevel(school.education_level)
  const headlineNvo = nvo.find((result) => result.examType === levelExam) || nvo[nvo.length - 1]
  const about = `${[facts, addresses[0]].filter(Boolean).join(', ')}.`
  const nvoSentence = headlineNvo ? `${nvoLine(headlineNvo, t)}.` : ''
  const description = (leadWithNvo ? [nvoSentence, about] : [about, nvoSentence, summary || tuition])
    .filter(Boolean)
    .join(' ')

  const nvoSection = nvo.length > 0
    ? `<section><h2 class="font-semibold">${escapeHtml(t('seo.nvoHeading'))}</h2><ul>${nvo
      .map((result) => `<li>${escapeHtml(nvoLine(result, t))}</li>`)
      .join('')}</ul></section>`
    : ''
  const aboutParts = [paragraph(facts), ...addresses.map(paragraph)]
  const rest = [paragraph(summary), paragraph(tuition)]

  return {
    routePath: `/schools/${school.id}`,
    title: `${name} · ${t('nav.title')}`,
    description: truncate(description),
    canonicalPath: `/schools/${school.id}`,
    noindex: !listed || isThinSchool(school),
    sitemap: listed && !isThinSchool(school),
    body: mainBlock([
      heading(name),
      ...(leadWithNvo ? [nvoSection, ...aboutParts, ...rest] : [...aboutParts, ...rest, nvoSection]),
    ]),
  }
}

function replaceOnce(html, pattern, replacement, label) {
  if (!pattern.test(html)) throw new Error(`prerender: ${label} not found in the built index.html`)
  return html.replace(pattern, () => replacement)
}

/** Stamp one page into the built `index.html`. */
export function renderPage(template, page, { language, origin, siteName }) {
  const meta = (attribute, key, content) => `<meta ${attribute}="${key}" content="${escapeHtml(content)}" />`
  const link = (rel, href, extra = '') => `<link rel="${rel}"${extra} href="${escapeHtml(href)}" />`
  const head = [meta('name', 'description', page.description)]
  if (page.noindex) head.push(meta('name', 'robots', 'noindex, follow'))
  if (page.canonicalPath) {
    const canonical = canonicalUrl(page.canonicalPath, language, origin)
    head.push(link('canonical', canonical))
    alternateLinks(page.canonicalPath, origin).forEach(({ hreflang, href }) => {
      head.push(link('alternate', href, ` hreflang="${hreflang}"`))
    })
    head.push(meta('property', 'og:url', canonical))
  }
  head.push(
    meta('property', 'og:type', 'website'),
    meta('property', 'og:site_name', siteName),
    meta('property', 'og:title', page.title),
    meta('property', 'og:description', page.description),
    meta('property', 'og:locale', OG_LOCALES[language]),
    ...URL_LANGUAGES.filter((other) => other !== language).map((other) =>
      meta('property', 'og:locale:alternate', OG_LOCALES[other])
    ),
  )

  let html = replaceOnce(template, /<html lang="[^"]*">/, `<html lang="${language}">`, '<html lang>')
  html = replaceOnce(html, /<title>[^<]*<\/title>/, `<title>${escapeHtml(page.title)}</title>`, '<title>')
  html = replaceOnce(html, /<\/head>/, `  ${head.join('\n    ')}\n  </head>`, '</head>')
  return replaceOnce(html, /<div id="root"><\/div>/, `<div id="root">${page.body}</div>`, '#root')
}

/** Sitemap of route paths, each listed in every language with its hreflang alternates. */
export function buildSitemap(routePaths, origin) {
  const urls = routePaths.flatMap((routePath) =>
    URL_LANGUAGES.map((language) => {
      const alternates = alternateLinks(routePath, origin)
        .map(({ hreflang, href }) => `    <xhtml:link rel="alternate" hreflang="${hreflang}" href="${escapeHtml(href)}" />`)
        .join('\n')
      return `  <url>\n    <loc>${escapeHtml(canonicalUrl(routePath, language, origin))}</loc>\n${alternates}\n  </url>`
    })
  )
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ...urls,
    '</urlset>',
    '',
  ].join('\n')
}

/**
 * Search and AI answers that link back are welcome; model training is not. These signals
 * are advisory: per-crawler enforcement is Cloudflare's managed robots.txt (P3.2 Terraform).
 */
export function buildRobots(origin) {
  return [
    'User-agent: *',
    'Content-Signal: search=yes, ai-input=yes, ai-train=no, use=reference',
    'Allow: /',
    '',
    `Sitemap: ${canonicalUrl('/sitemap.xml', 'bg', origin)}`,
    '',
  ].join('\n')
}
