import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { EVALUATORS } from './evaluators/index.js'

const RULES_DIR = fileURLToPath(new URL('./rules', import.meta.url))

function ruleFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return ruleFiles(path)
    return name.endsWith('.json') ? [path] : []
  })
}

function asRange(value) {
  return typeof value === 'number' ? { min: value, max: value } : value
}

const FILES = ruleFiles(RULES_DIR)

// Distinct legal states: a suspension, its lifting and a first-instance ruling that is
// under appeal are not interchangeable.
const STATUS_EVENTS = ['suspended', 'suspension_lifted', 'annulled_not_final', 'annulled_final', 'upheld_final']

test('every rule file is well formed', () => {
  assert.ok(FILES.length > 0)
  for (const file of FILES) {
    const rules = JSON.parse(readFileSync(file, 'utf8'))
    const relative = file.slice(RULES_DIR.length + 1).replace(/\.json$/, '')
    assert.equal(rules.id, relative, `${file}: id must match its path`)
    assert.ok(EVALUATORS[rules.evaluator], `${file}: unknown evaluator ${rules.evaluator}`)
    assert.match(rules.campaign, /^\d{4}\/\d{4}$/)
    assert.equal(rules.id.split('/').pop(), rules.campaign.replace('/', '-'), `${file}: id ends with the campaign`)
    assert.ok(rules.verified?.on, `${file}: needs verified.on`)
    const sourceIds = new Set((rules.sources || []).map(source => source.id))
    assert.match(rules.version, /^\d{4}-\d{2}-\d{2}$/, `${file}: version is the date of its latest act`)
    assert.ok(['verified', 'provisional'].includes(rules.verified.status), `${file}: verified.status`)
    for (const act of rules.acts || []) {
      assert.ok(sourceIds.has(act.source), `${file}: act ${act.id} cites unknown source`)
    }
    for (const event of rules.status_events || []) {
      assert.ok(STATUS_EVENTS.includes(event.status), `${file}: unknown status ${event.status}`)
      assert.ok(sourceIds.has(event.source), `${file}: status event cites unknown source`)
    }
    for (const id of rules.verified.sources) assert.ok(sourceIds.has(id), `${file}: verified cites unknown source ${id}`)
    assert.ok((rules.examples || []).length >= 3, `${file}: needs worked examples`)
  }
})

for (const file of FILES) {
  const rules = JSON.parse(readFileSync(file, 'utf8'))
  const evaluator = EVALUATORS[rules.evaluator]
  for (const example of rules.examples || []) {
    test(`${rules.id}: ${example.name}`, () => {
      const result = evaluator.evaluate(rules.params, example.answers, example.context, example.options)
      for (const [key, expected] of Object.entries(example.expected)) {
        const actual = result[key]
        const isRange = actual && typeof actual === 'object' && 'min' in actual && 'max' in actual
        assert.deepEqual(actual, isRange ? asRange(expected) : expected, key)
      }
    })
  }
}
