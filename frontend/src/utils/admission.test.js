import test from 'node:test'
import assert from 'node:assert/strict'
import { classifyAdmissionRequirement } from './admission.js'

test('classifies English and Bulgarian interview requirements', () => {
  assert.equal(classifyAdmissionRequirement('Interview with the family').kind, 'interview')
  assert.equal(classifyAdmissionRequirement('Интервю с родителите').kind, 'interview')
  assert.equal(classifyAdmissionRequirement('Събеседване с детето').kind, 'interview')
})

test('classifies English and Bulgarian test requirements', () => {
  assert.equal(classifyAdmissionRequirement('Entrance exam').kind, 'test')
  assert.equal(classifyAdmissionRequirement(['Входящ тест']).kind, 'test')
  assert.equal(classifyAdmissionRequirement('Приемен изпит').kind, 'test')
})

test('negative phrases win over embedded test and interview keywords', () => {
  assert.equal(classifyAdmissionRequirement('No entrance exam').kind, 'none')
  assert.equal(classifyAdmissionRequirement('No test required').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Without an interview').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Без входен изпит').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Не се изисква интервю').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Тест не се изисква').kind, 'none')
})

test('preserves unknown free text and joins extracted arrays for fallback rendering', () => {
  assert.deepEqual(classifyAdmissionRequirement(['Оценка на документи', 'Среща със семейството']), {
    kind: 'other',
    text: 'Оценка на документи • Среща със семейството',
  })
})

test('returns null for missing or unusable requirements', () => {
  assert.equal(classifyAdmissionRequirement(null), null)
  assert.equal(classifyAdmissionRequirement([]), null)
  assert.equal(classifyAdmissionRequirement({}), null)
})
