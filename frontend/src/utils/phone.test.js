import test from 'node:test'
import assert from 'node:assert/strict'

import { parsePhones } from './phone.js'

const one = (raw) => {
  const parsed = parsePhones(raw)
  assert.equal(parsed.length, 1)
  return parsed[0]
}

test('international mobile with a stray slash is regrouped and dialled as +359', () => {
  assert.deepEqual(one('+359/889243971'), { display: '+359 88 924 3971', href: 'tel:+359889243971' })
  assert.deepEqual(one('+359/0885706805'), { display: '+359 88 570 6805', href: 'tel:+359885706805' })
  assert.deepEqual(one('359/0877403435'), { display: '+359 87 740 3435', href: 'tel:+359877403435' })
})

test('national mobile numbers keep the national form but dial internationally', () => {
  assert.deepEqual(one('/0884305108'), { display: '0884 305 108', href: 'tel:+359884305108' })
  assert.deepEqual(one('0884 195 883'), { display: '0884 195 883', href: 'tel:+359884195883' })
})

test('Sofia landlines are regrouped', () => {
  assert.deepEqual(one('02/824 11 70'), { display: '02 824 1170', href: 'tel:+35928241170' })
  assert.deepEqual(one('02/827-74-92'), { display: '02 827 7492', href: 'tel:+35928277492' })
})

test('a Sofia area code glued onto another number is dropped', () => {
  assert.equal(one('02/0887070435').display, '0887 070 435')
  assert.equal(one('02/+359 886 005 138').display, '+359 88 600 5138')
  assert.equal(one('02/02/9839621').display, '02 983 9621')
  assert.equal(one('02/028264163').display, '02 826 4163')
})

test('unknown formats are shown as published with a digits-only href', () => {
  assert.deepEqual(one('02991/996 40 02'), { display: '02991/996 40 02', href: 'tel:029919964002' })
  assert.deepEqual(one('9963213'), { display: '9963213', href: 'tel:9963213' })
  assert.deepEqual(one('ext 12'), { display: 'ext 12', href: null })
})

test('several numbers in one field are split', () => {
  assert.deepEqual(parsePhones('/0877429067; 0898414266').map(p => p.display), [
    '0877 429 067',
    '0898 414 266',
  ])
})

test('missing or blank values produce no phones', () => {
  assert.deepEqual(parsePhones(null), [])
  assert.deepEqual(parsePhones(undefined), [])
  assert.deepEqual(parsePhones('  / '), [])
})
