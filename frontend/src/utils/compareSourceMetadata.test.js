import assert from 'node:assert/strict'
import test from 'node:test'

import { getProvenanceSourceKey } from '../components/ComparePage/sourceMetadata.js'

test('provenance list keys use only allowed metadata and distinguish duplicate rows', () => {
  const source = {
    source_type: 'official_website',
    source_url: 'https://school.example/about',
    last_verified: '2026-07-15T00:00:00Z',
    confidence: 'high',
  }
  const internalFields = {
    ...source,
    id: 529,
    category: 'general_info',
    scraped_at: '2026-07-14T00:00:00Z',
    value_text: 'must not be used',
  }

  assert.equal(getProvenanceSourceKey(source, 0), getProvenanceSourceKey(internalFields, 0))
  assert.notEqual(getProvenanceSourceKey(source, 0), getProvenanceSourceKey(source, 1))
})
