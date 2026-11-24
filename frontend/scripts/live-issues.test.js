// The rules that turn failed live flows into issues (e2e/live/issues.js).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { END_OF_RUN } from '../e2e/live/annotate.js'
import { CRASHED, failedFlows, flowOf, plan } from '../e2e/live/issues.js'

const issue = (number, flow) => ({ number, title: `Live check failing: ${flow}` })

test('a passing run lists no failures, whatever the file says', () => {
  assert.deepEqual(failedFlows('', 'success'), [])
  assert.deepEqual(failedFlows(`${END_OF_RUN}\n`, 'success'), [])
})

test('a failed run lists its flows once', () => {
  assert.deepEqual(failedFlows(`a flow\nb  flow\na flow\n${END_OF_RUN}\n`, 'failure'), ['a flow', 'b flow'])
})

test('a run cut short, or one that never ran, counts as a crash', () => {
  assert.deepEqual(failedFlows('a flow\n', 'failure'), ['a flow', CRASHED])
  assert.deepEqual(failedFlows(`${END_OF_RUN}\n`, 'failure'), [CRASHED])
  assert.deepEqual(failedFlows('', 'skipped'), [CRASHED])
})

test('an issue opens only on the second daily failure in a row', () => {
  const { toOpen } = plan(['new', 'again'], new Set(['again', 'fixed']), [])
  assert.deepEqual(toOpen, ['again'])
})

test('a flow with an open issue opens no second one, and a passing flow closes its issue', () => {
  const still = issue(1, 'still')
  const passed = issue(2, 'passed')
  const other = { number: 3, title: 'Something else' }
  const { toOpen, toClose } = plan(['still'], new Set(['still']), [still, passed, other])
  assert.deepEqual(toOpen, [])
  assert.deepEqual(toClose, [passed])
  assert.equal(flowOf(other), null)
})
