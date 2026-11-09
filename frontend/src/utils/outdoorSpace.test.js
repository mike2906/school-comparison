import { test } from 'node:test'
import assert from 'node:assert/strict'
import { outdoorEntries, outdoorWebsiteFacts, usesOpenStreetMap } from './outdoorSpace.js'

const school = {
  locations: [{ id: 1, address_i18n: { bg: 'ул. 1' } }, { id: 2, address_i18n: { bg: 'ул. 2' } }],
  outdoor_space: {
    locations: [
      { location_id: 1, kind: 'grounds', source: 'osm', open_m2: 4400, pitches: 3, playgrounds: 0 },
      { location_id: 2, kind: 'shared', source: 'osm', shared_with_id: 7, shared_with_i18n: { bg: '47 СУ', en: '47 Secondary School' } },
      { location_id: 3, kind: 'made-up' },
    ],
    website: [{ text_i18n: { bg: 'Двор' }, source_url: 'https://example.bg/' }],
  },
}

test('entries keep known kinds, their building and the text in the page language', () => {
  const entries = outdoorEntries(school, 'en')
  assert.deepEqual(entries.map(e => e.kind), ['grounds', 'shared'])
  assert.equal(entries[0].location.id, 1)
  assert.equal(entries[0].openM2, 4400)
  assert.equal(entries[1].sharedWith, '47 Secondary School')
  assert.equal(usesOpenStreetMap(entries), true)
})

test('website facts fall back to Bulgarian; no outdoor_space gives nothing', () => {
  assert.deepEqual(outdoorWebsiteFacts(school, 'en'), [{ text: 'Двор', sourceUrl: 'https://example.bg/' }])
  assert.deepEqual(outdoorEntries({ locations: [] }, 'bg'), [])
})
