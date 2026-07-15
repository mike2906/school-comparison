import assert from 'node:assert/strict'
import test from 'node:test'

import { hasAnySchoolSummary } from '../components/ComparePage/summaryVisibility.js'

test('summary comparison row stays hidden when launch responses have no summaries', () => {
  assert.equal(
    hasAnySchoolSummary(
      [
        { id: 1, summary_i18n: null },
        { id: 2, summary_i18n: { bg: null, en: null } },
      ],
      'bg',
    ),
    false,
  )
})

test('summary comparison row returns when publication is explicitly enabled', () => {
  assert.equal(
    hasAnySchoolSummary(
      [{ id: 1, summary_i18n: { bg: { short: 'Кратко', long: 'Дълго' } } }],
      'bg',
    ),
    true,
  )
})
