/**
 * Static HTML for crawlers and link previews: per-page head tags plus a small content
 * block inside `#root` that React replaces on mount (hidden until then, see index.html).
 * Pure builders; the file and network work lives in `scripts/prerender.js`.
 */
import {
  LAST_ADMITTED_KINDS,
  comparableRound,
  cutoffText,
  cutoffValue,
  rankingLabel,
  roundCounts,
  rowLabel,
  shortCampaign,
} from '../admission/calculator.js'
import { districtLabel } from '../admission/catchments.js'
import { kindergartenDistrictFeature } from '../admission/schoolVerdict.js'
import { getAddress, getSchoolName } from '../utils/i18n.js'
import { formatPercent } from '../utils/nvoDisplay.js'
import { parseCoordinate } from '../utils/coordinates.js'
import { parsePhones } from '../utils/phone.js'
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
import { getSchoolFacts } from '../utils/schoolFacts.js'
import { directorySections } from '../utils/schoolDirectory.js'

// Our own thin-content heuristic, not an SEO rule: a published Bulgarian summary shorter
// than this says little beyond the name, type and address the page already shows.
export const SUMMARY_MIN_CHARS = 300

const DESCRIPTION_MAX_CHARS = 160
const OG_LOCALES = { bg: 'bg_BG', en: 'en_GB' }
const BODY_CLASS = 'mx-auto max-w-3xl space-y-3 px-4 py-8 text-neutral-900'

/**
 * A minimal `t()` over one locale file: dotted keys, `{{name}}` interpolation, and
 * i18next's `_one`/`_other` plural keys when `count` is given.
 */
export function createTranslator(dictionary, language = 'en') {
  const plurals = new Intl.PluralRules(language)
  const lookup = (key) => key.split('.').reduce((node, part) => node?.[part], dictionary)
  return (key, values = {}) => {
    const plural = typeof values.count === 'number' ? lookup(`${key}_${plurals.select(values.count)}`) : undefined
    const template = typeof plural === 'string' ? plural : lookup(key)
    if (typeof template !== 'string') return values.defaultValue ?? key
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

function nvoLine(result, t, language) {
  const scores = [
    result.scores.bulgarian != null && t('seo.nvoBulgarian', { value: formatPercent(result.scores.bulgarian, 1, language) }),
    result.scores.math != null && t('seo.nvoMath', { value: formatPercent(result.scores.math, 1, language) }),
  ].filter(Boolean)
  return t('seo.nvoLine', {
    exam: getExamTypeLabel(result.examType, t),
    year: result.year,
    scores: scores.join(', '),
  })
}

function tuitionLine(school, t) {
  // This year's fee, not next year's: the description has no room to label the year.
  const tuition = statedTuition(school?.pricing, { intake: false })
  // Euro only, in the period the school states (never annualised), and never an older
  // year's fee, which the description has no room to label as such.
  if (!tuition || tuition.currency !== 'EUR' || !tuition.period || tuition.yearStatus === 'dated_other') return ''
  const [min, max] = [tuition.min, tuition.max].map((value) => Math.round(value))
  const period = t(`pricing.per.${tuition.period}`)
  const [one, range] = tuition.total ? ['seo.totalOne', 'seo.totalRange'] : ['seo.tuitionOne', 'seo.tuitionRange']
  return min === max ? t(one, { min, period }) : t(range, { min, max, period })
}

/**
 * The admission rankings in words, one line per group or class, read on the same ranking
 * as the school page's "Who got in before" (`comparableRound`).
 */
function cutoffLines(school, t, language) {
  const rows = school?.admission_info?.cutoff_summary
  if (!Array.isArray(rows)) return []
  return rows.flatMap((row) => {
    const round = comparableRound(row)
    if (!round) return []
    const value = cutoffText(round, t, language)
    const ranking = rankingLabel(row, round, t, language)
    const result = value
      ? `${ranking}${LAST_ADMITTED_KINDS.has(cutoffValue(round)?.kind) ? `, ${t('cutoffs.lastAdmitted')} ` : ': '}${value}`
      : ranking
    const tie = round.tie_at_cutoff ? ` ${t('cutoffs.tie')}` : ''
    const counts = roundCounts(round, t)
    const label = [shortCampaign(row.campaign), row.quota && t('cutoffs.quota')].filter(Boolean).join(', ')
    return [`${rowLabel(row, t, language)} (${label}): ${result}${tie}${counts ? ` (${counts})` : ''}`]
  })
}

/** A row whose ranking says who got in: a cut-off, or that everyone did. */
function hasAdmissionResult(row) {
  const kind = cutoffValue(comparableRound(row))?.kind
  return Boolean(kind) && kind !== 'noPlaces'
}

/**
 * What makes a school page worth indexing: official NVO results, a published price,
 * facts from the school's own site, an admission ranking that says who got in, or a substantive
 * published summary. A page with none of them is thin (`noindex`).
 */
export function indexReasons(school) {
  const rows = school?.admission_info?.cutoff_summary
  return {
    nvo: latestNvoResults(school).length > 0,
    pricing: Array.isArray(school?.pricing) && school.pricing.length > 0,
    facts: getSchoolFacts(school, 'bg', { keyOnly: true }).length > 0,
    admission: Array.isArray(rows) && rows.some(hasAdmissionResult),
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

function listSection(title, lines) {
  if (lines.length === 0) return ''
  const items = lines.map((line) => `<li>${escapeHtml(line)}</li>`).join('')
  return `<section><h2 class="font-semibold">${escapeHtml(title)}</h2><ul>${items}</ul></section>`
}

function paragraph(text) {
  return text ? `<p>${escapeHtml(text)}</p>` : ''
}

/** The A–Z list of `/schools`, the same sections and links the app renders there. */
function directoryBody(listedSchools, t, language) {
  const sections = directorySections(listedSchools, language).map(({ key, entries }) => {
    const items = entries
      .map(({ school, name }) => {
        const href = languagePath(`/schools/${school.id}`, language)
        const facts = t('seo.schoolFacts', { type: schoolTypeLabel(school, t), level: schoolLevelLabel(school, t) })
        return `<li><a href="${escapeHtml(href)}">${escapeHtml(name)}</a> · ${escapeHtml(facts)}</li>`
      })
      .join('')
    return `<section><h2 class="font-semibold">${escapeHtml(`${t(`directory.${key}`)} (${entries.length})`)}</h2><ul>${items}</ul></section>`
  })
  return mainBlock([heading(t('directory.title')), paragraph(t('directory.intro')), ...sections])
}

/**
 * The pages that exist without school data, including the two aliases of `/search`.
 * `listedSchools` (the schools the site lists) fill the A–Z list at `/schools`.
 */
export function fixedPages(t, language, listedSchools = []) {
  const siteName = t('nav.title')
  // The app's list shows a few dozen cards at a time and crawlers do not scroll, so the
  // search page points them at the A–Z list, which links every school page.
  const directoryLink = `<p><a href="${escapeHtml(languagePath('/schools', language))}">${escapeHtml(t('seo.allSchools'))}</a></p>`
  const search = {
    title: t('seo.searchTitle'),
    description: t('seo.searchDescription'),
    canonicalPath: '/search',
    body: mainBlock([heading(t('seo.searchTitle')), paragraph(t('seo.searchDescription')), directoryLink]),
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
      routePath: '/admission',
      title: `${t('admissionCalc.title')} · ${siteName}`,
      description: truncate(t('admissionCalc.intro')),
      canonicalPath: '/admission',
      sitemap: true,
      body: mainBlock([heading(t('admissionCalc.title')), paragraph(t('admissionCalc.intro'))]),
    },
    {
      routePath: '/my-child',
      title: `${t('myChild.title')} · ${siteName}`,
      description: truncate(t('myChild.intro')),
      canonicalPath: '/my-child',
      sitemap: true,
      body: mainBlock([heading(t('myChild.title')), paragraph(t('myChild.intro'))]),
    },
    {
      routePath: '/schools',
      title: `${t('directory.title')} · ${siteName}`,
      description: truncate(t('directory.intro')),
      canonicalPath: '/schools',
      sitemap: true,
      body: directoryBody(listedSchools, t, language),
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

function primaryLocation(school) {
  const locations = Array.isArray(school?.locations) ? school.locations : []
  return locations.find((location) => location.is_primary) || locations[0]
}

/**
 * schema.org data for a school page: a Preschool or School with its address, pin and
 * phone, so search engines and assistants read the facts without parsing the text.
 */
function schoolJsonLd(school, { name, url, language, district, listed, t }) {
  const primary = primaryLocation(school)
  const other = URL_LANGUAGES.filter((code) => code !== language).map((code) => getSchoolName(school, code))
  const address = getAddress(primary, language)
  const lat = parseCoordinate(primary?.lat)
  const lng = parseCoordinate(primary?.lng)
  return {
    '@context': 'https://schema.org',
    '@type': school.education_level === 'kindergarten' ? 'Preschool' : 'School',
    name,
    alternateName: other.filter((value) => value && value !== name)[0],
    url,
    sameAs: /^https?:\/\//.test(school.website_url || '') ? school.website_url : undefined,
    telephone: parsePhones(primary?.phone).find((phone) => phone.href)?.href.replace(/^tel:/, '') || undefined,
    address: address
      ? {
        '@type': 'PostalAddress',
        streetAddress: address,
        // Listed schools are in the launch city; the rest only keep a working URL.
        addressLocality: listed ? t('seo.city') : undefined,
        addressRegion: district ? t('seo.district', { district: districtLabel(district, language) }) : undefined,
        addressCountry: 'BG',
      }
      : undefined,
    geo: lat != null && lng != null && (lat || lng)
      ? { '@type': 'GeoCoordinates', latitude: lat, longitude: lng }
      : undefined,
  }
}

/**
 * A school's page. State schools lead with their official NVO results. A school the site
 * does not list (`listed: false`, outside the launch city) keeps a working URL but is not
 * offered to search engines.
 */
export function schoolPage(school, t, language, { listed = true, districts = null, origin = null } = {}) {
  const name = getSchoolName(school, language)
  const district = listed ? kindergartenDistrictFeature(school, districts)?.properties.name ?? null : null
  const districtText = district ? t('seo.district', { district: districtLabel(district, language) }) : ''
  const facts = t('seo.schoolFacts', {
    type: schoolTypeLabel(school, t),
    level: schoolLevelLabel(school, t),
  })
  const locations = Array.isArray(school.locations) ? school.locations : []
  const primary = primaryLocation(school)
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
  const nvoSentence = headlineNvo ? `${nvoLine(headlineNvo, t, language)}.` : ''
  // Only facts written in the page's language: the app labels a Bulgarian fallback, this can't.
  const siteFacts = getSchoolFacts(school, language, { fallback: false })
  const admission = cutoffLines(school, t, language)
  const keyFact = siteFacts.find((fact) => fact.rank === 'key')?.text
  const detail = summary || tuition || keyFact || (admission[0] && `${admission[0]}.`)
  const description = (leadWithNvo ? [nvoSentence, about] : [about, nvoSentence, detail])
    .filter(Boolean)
    .join(' ')

  const nvoSection = listSection(t('seo.nvoHeading'), nvo.map((result) => nvoLine(result, t, language)))
  const factsSection = listSection(
    t('schoolFacts.title'),
    siteFacts.map((fact) => `${t(`schoolFacts.topics.${fact.topic}`)}: ${fact.text}`),
  )
  const admissionSection = listSection(t('cutoffs.title'), admission)
  const aboutParts = [paragraph(facts), ...addresses.map(paragraph)]
  const rest = [paragraph(summary), paragraph(tuition), factsSection, admissionSection]

  return {
    routePath: `/schools/${school.id}`,
    // The district is what parents add to a school's name when they search.
    title: `${[name, districtText].filter(Boolean).join(', ')} · ${t('nav.title')}`,
    description: truncate(description),
    canonicalPath: `/schools/${school.id}`,
    jsonLd: origin ? schoolJsonLd(school, { name, url: canonicalUrl(`/schools/${school.id}`, language, origin), language, district, listed, t }) : null,
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
  if (page.jsonLd) {
    // `<` escaped so school text can never close the script element.
    head.push(`<script type="application/ld+json">${JSON.stringify(page.jsonLd).replace(/</g, '\\u003c')}</script>`)
  }
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
