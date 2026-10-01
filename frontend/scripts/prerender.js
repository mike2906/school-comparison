/**
 * Post-build step: writes a real HTML file for every valid URL into `dist/`, plus
 * `sitemap.xml`, `robots.txt` and the 404 pages. See `src/prerender/pages.js`.
 *
 * School data comes from the public API (`PRERENDER_API_URL`), so only what the publish
 * boundary lets through can reach a page. Without it a local or PR build writes the fixed
 * pages only. `--strict` (`npm run build:production`) refuses to build a site that would
 * deploy without its school pages or with a guessed origin.
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { loadEnv } from 'vite'

import {
  buildRobots,
  buildSitemap,
  createTranslator,
  fixedPages,
  indexReasons,
  outputFile,
  renderPage,
  schoolPage,
} from '../src/prerender/pages.js'
import { DEFAULT_SITE_ORIGIN, URL_LANGUAGES } from '../src/utils/languageUrl.js'

const DIST = path.resolve('dist')
const CONCURRENCY = 8
const strict = process.argv.includes('--strict')

function siteOrigin(value) {
  if (!value) {
    if (strict) throw new Error('VITE_SITE_ORIGIN must be set for a production build')
    return DEFAULT_SITE_ORIGIN
  }
  const url = new URL(value)
  if (strict && url.protocol !== 'https:') throw new Error(`VITE_SITE_ORIGIN must be https, got ${value}`)
  if (url.pathname !== '/' || url.search || url.hash) throw new Error(`VITE_SITE_ORIGIN must be a bare origin, got ${value}`)
  return url.origin
}

async function fetchJson(url) {
  let lastError
  for (const delaySeconds of [0, 1, 2]) {
    if (delaySeconds) await new Promise((resolve) => setTimeout(resolve, delaySeconds * 1000))
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(30_000) })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return await response.json()
    } catch (error) {
      lastError = error
    }
  }
  throw new Error(`prerender: ${url} failed: ${lastError.message}`)
}

/**
 * Every published school with its detail payload, and the ids the site lists (the launch
 * city). Schools outside the listing still get a page, so their URLs keep working, but
 * are not indexed. Any failure fails the build.
 */
async function fetchSchools(apiUrl) {
  const base = apiUrl.replace(/\/+$/, '')
  const list = await fetchJson(`${base}/schools?country_code=bg&city=all`)
  const listed = await fetchJson(`${base}/schools?country_code=bg&city=sofia`)
  if (!Array.isArray(list) || !Array.isArray(listed) || listed.length === 0) {
    throw new Error('prerender: the API returned no schools')
  }
  const schools = new Array(list.length)
  let next = 0
  await Promise.all(
    Array.from({ length: CONCURRENCY }, async () => {
      while (next < list.length) {
        const index = next++
        schools[index] = await fetchJson(`${base}/schools/${list[index].id}`)
      }
    })
  )
  return { schools, listedIds: new Set(listed.map((school) => school.id)) }
}

async function write(file, content) {
  const target = path.join(DIST, file)
  await mkdir(path.dirname(target), { recursive: true })
  await writeFile(target, content)
}

async function main() {
  const env = loadEnv('production', process.cwd(), '')
  const origin = siteOrigin(env.VITE_SITE_ORIGIN)
  const apiUrl = env.PRERENDER_API_URL
  if (!apiUrl && strict) throw new Error('PRERENDER_API_URL must be set for a production build')

  const { schools, listedIds } = apiUrl ? await fetchSchools(apiUrl) : { schools: [], listedIds: new Set() }
  const template = await readFile(path.join(DIST, 'index.html'), 'utf8')

  let sitemapPaths = []
  for (const language of URL_LANGUAGES) {
    const dictionary = JSON.parse(await readFile(path.resolve(`src/i18n/${language}.json`), 'utf8'))
    const t = createTranslator(dictionary)
    const pages = [
      ...fixedPages(t, language),
      ...schools.map((school) => schoolPage(school, t, language, { listed: listedIds.has(school.id) })),
    ]
    for (const page of pages) {
      await write(outputFile(page.routePath, language), renderPage(template, page, { language, origin, siteName: t('nav.title') }))
    }
    // Indexability is decided per school, not per language, so every language lists the same paths.
    sitemapPaths = pages.filter((page) => page.sitemap).map((page) => page.routePath)
  }
  await write('sitemap.xml', buildSitemap(sitemapPaths, origin))
  await write('robots.txt', buildRobots(origin))

  if (!apiUrl) {
    console.warn('\n\x1b[33m[warn] PRERENDER_API_URL is not set: no school pages were prerendered.\x1b[0m\n')
    return
  }
  const reasons = schools.filter((school) => listedIds.has(school.id)).map(indexReasons)
  const count = (key) => reasons.filter((reason) => reason[key]).length
  const thin = reasons.filter((reason) => !Object.values(reason).some(Boolean)).length
  console.log(
    `prerender: ${reasons.length} listed schools, ${reasons.length - thin} indexable ` +
      `(NVO ${count('nvo')}, pricing ${count('pricing')}, summary ${count('summary')}), ${thin} thin (noindex); ` +
      `${schools.length - reasons.length} unlisted (noindex)`
  )
}

main().catch((error) => {
  console.error(`\n\x1b[31m${error.message}\x1b[0m\n`)
  process.exit(1)
})
