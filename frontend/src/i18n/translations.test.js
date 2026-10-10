// Missing translations fail here instead of reaching the page as a raw key ("schoolCard.foo").
// Keys built from data at runtime (`ageGroups.${group}`) only show up on the rendered site.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const I18N_DIR = path.dirname(fileURLToPath(import.meta.url))
const SRC_DIR = path.dirname(I18N_DIR)
const LANGUAGES = ['bg', 'en']

const dictionaries = Object.fromEntries(
  LANGUAGES.map(lang => [lang, JSON.parse(readFileSync(path.join(I18N_DIR, `${lang}.json`), 'utf8'))]),
)

function leaves(node, prefix = '', out = new Map()) {
  for (const [key, value] of Object.entries(node)) {
    const full = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object') leaves(value, full, out)
    else out.set(full, value)
  }
  return out
}

const leafMaps = Object.fromEntries(LANGUAGES.map(lang => [lang, leaves(dictionaries[lang])]))
const namespaces = new Set(Object.keys(dictionaries.en))

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return /\.jsx?$/.test(entry.name) && !entry.name.endsWith('.test.js') ? [full] : []
  })
}

function resolves(leafMap, key) {
  // i18next plurals: t('x', { count }) reads x_one / x_other.
  return leafMap.has(key) || leafMap.has(`${key}_one`) || leafMap.has(`${key}_other`)
}

function isNamespace(leafMap, prefix) {
  return [...leafMap.keys()].some(key => key.startsWith(prefix))
}

// Any quoted string shaped like a key under one of our namespaces: t('a.b'), labelKey: 'a.b',
// ternaries and lookup tables alike.
const LITERAL_KEY_RE = /(['"])([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\1/g
// The fixed part of a template key, inside t() or stored for later:
// `ageGroups.${group}` → "ageGroups.", `location.distance${n}km` → "location.distance"
const TEMPLATE_PREFIX_RE = /`([A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]*)\$\{/g

const sources = sourceFiles(SRC_DIR).map(file => ({
  file: path.relative(SRC_DIR, file),
  text: readFileSync(file, 'utf8'),
}))

test('every literal translation key in src exists in bg and en', () => {
  const missing = []
  for (const { file, text } of sources) {
    for (const [, , key] of text.matchAll(LITERAL_KEY_RE)) {
      if (!namespaces.has(key.split('.')[0])) continue
      for (const lang of LANGUAGES) {
        if (!resolves(leafMaps[lang], key)) missing.push(`${file}: ${key} (${lang})`)
      }
    }
  }
  assert.deepEqual(missing, [])
})

test('every template translation key starts like an existing key', () => {
  const missing = []
  for (const { file, text } of sources) {
    for (const [, prefix] of text.matchAll(TEMPLATE_PREFIX_RE)) {
      if (!namespaces.has(prefix.split('.')[0])) continue
      for (const lang of LANGUAGES) {
        if (!isNamespace(leafMaps[lang], prefix)) missing.push(`${file}: ${prefix}* (${lang})`)
      }
    }
  }
  assert.deepEqual(missing, [])
})

test('bg and en have the same keys, none empty', () => {
  const [bg, en] = LANGUAGES.map(lang => leafMaps[lang])
  assert.deepEqual([...bg.keys()].filter(key => !en.has(key)), [], 'in bg only')
  assert.deepEqual([...en.keys()].filter(key => !bg.has(key)), [], 'in en only')
  const empty = LANGUAGES.flatMap(lang =>
    [...leafMaps[lang]].filter(([, value]) => typeof value !== 'string' || !value.trim()).map(([key]) => `${key} (${lang})`),
  )
  assert.deepEqual(empty, [])
})

test('bg and en use the same {{placeholders}} in each string', () => {
  const placeholders = value => [...String(value).matchAll(/\{\{\s*([^}\s]+)\s*\}\}/g)].map(m => m[1]).sort()
  const mismatched = []
  for (const [key, bgValue] of leafMaps.bg) {
    if (!leafMaps.en.has(key)) continue
    const bgNames = placeholders(bgValue)
    const enNames = placeholders(leafMaps.en.get(key))
    if (bgNames.join() !== enNames.join()) mismatched.push(`${key}: bg {${bgNames}} en {${enNames}}`)
  }
  assert.deepEqual(mismatched, [])
})
