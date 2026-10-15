import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import {
  addressFactsForSchool,
  catchmentSchoolFor,
  catchmentUrl,
  districtAt,
  districtByName,
  districtLabel,
  indexAddresses,
  indexCatchments,
  neighbouringCatchments,
  normalizeHouseNumber,
  searchKey,
  sortedDistricts,
  mapSearchQuery,
  searchStreets,
} from './catchments.js'

function snapshot(file) {
  const path = fileURLToPath(new URL(`../../public${catchmentUrl('2026/2027', file)}`, import.meta.url))
  return JSON.parse(readFileSync(path, 'utf8'))
}

const streets = indexAddresses(snapshot('addresses.json'))
const catchments = indexCatchments(snapshot('catchments.json'))
const districts = snapshot('districts.json')

test('the snapshot covers every Sofia catchment and district', () => {
  assert.equal(catchments.size, 159)
  assert.equal(districts.features.length, 24)
  assert.ok(streets.length > 5000)
})

test('searchKey matches Cyrillic and Latin spellings', () => {
  assert.equal(searchKey('Иван Щерев'), 'ivan shterev')
  assert.equal(searchKey('ivan shterev'), 'ivan shterev')
  assert.equal(searchKey('Ж.К. Люлин'), 'zh k lyulin')
})

test('searchStreets finds a street by part of its name, in either alphabet', () => {
  const found = searchStreets(streets, 'щерев')
  assert.ok(found.some(street => street.street === 'УЛ.ИВАН ЩЕРЕВ'))
  const latin = searchStreets(streets, 'ivan shterev')
  assert.equal(latin[0].street, 'УЛ.ИВАН ЩЕРЕВ')
  assert.deepEqual(searchStreets(streets, 'щ'), [])
})

test('searchStreets lists the city street before same-named village streets', () => {
  for (const query of ['стара планина', 'витоша']) {
    const found = searchStreets(streets, query)
    assert.equal(found[0].settlement, 'ГР.СОФИЯ', query)
  }
})

test('mapSearchQuery turns a table address into a map search', () => {
  assert.equal(mapSearchQuery({ street: 'УЛ.ИВАН ЩЕРЕВ', settlement: 'ГР.СОФИЯ' }, '5'), 'ул. Иван Щерев 5, София, Bulgaria')
  assert.equal(mapSearchQuery({ street: 'УЛ.ВИТОША', settlement: 'С.БИСТРИЦА' }, '18а'), 'ул. Витоша 18А, Бистрица, Bulgaria')
  assert.equal(mapSearchQuery({ street: 'МАРИЦА', settlement: 'ГР.СОФИЯ' }, '2'), 'Марица 2, София, Bulgaria')
  assert.equal(mapSearchQuery({ street: 'УЛ.ВИТОША', settlement: 'ГР.СОФИЯ,КВ.ГОРУБЛЯНЕ' }, '3'), 'ул. Витоша 3, кв. Горубляне, София, Bulgaria')
})

test('house numbers are read the way the table writes them', () => {
  assert.equal(normalizeHouseNumber('032'), '32')
  assert.equal(normalizeHouseNumber(' 18 a '), '18А')
  assert.equal(normalizeHouseNumber('18б'), '18Б')
  assert.equal(normalizeHouseNumber('abc'), null)
})

test('an address is classified from the official table, including its neighbouring catchments', () => {
  const street = searchStreets(streets, 'ivan shterev').find(s => s.settlement === 'ГР.СОФИЯ')
  assert.equal(catchmentSchoolFor(street, '32'), '653')
  assert.equal(catchmentSchoolFor(street, '99999'), null)
  assert.deepEqual(addressFactsForSchool(catchments, '653', '653'), { inCatchment: true, inNeighbouring: false })
  assert.deepEqual(addressFactsForSchool(catchments, '653', '651'), { inCatchment: false, inNeighbouring: true })
  assert.deepEqual(addressFactsForSchool(catchments, '653', '616'), { inCatchment: false, inNeighbouring: false })
  assert.deepEqual(addressFactsForSchool(catchments, null, '616'), { inCatchment: null, inNeighbouring: null })
})

test('neighbouring catchments are read in the direction the rule uses', () => {
  const around = neighbouringCatchments(catchments, '651').map(f => f.properties.id)
  assert.ok(around.includes('653'))
  assert.ok(!around.includes('651'))
})

test('districts are found by point and by name', () => {
  // Младост 1, ж.к. Младост
  assert.equal(districtAt(districts, 42.6505, 23.3790).properties.name, 'Младост')
  assert.equal(districtAt(districts, 43.5, 25.0), null)
  assert.equal(districtByName(districts, 'подуяне').properties.code, '06')
  assert.equal(districtByName(districts, 'Студентски град').properties.code, '16')
  assert.equal(districtByName(districts, ''), null)
})

test('district names are transliterated and sorted for English pages', () => {
  assert.equal(districtLabel('Овча купел', 'en'), 'Ovcha Kupel')
  assert.equal(districtLabel('Нови Искър', 'en'), 'Novi Iskar')
  assert.equal(districtLabel('Нови Искър', 'bg'), 'Нови Искър')
  const districts = { features: ['Средец', 'Банкя', 'Възраждане'].map(name => ({ properties: { name } })) }
  assert.deepEqual(sortedDistricts(districts, 'bg').map(f => f.properties.name), ['Банкя', 'Възраждане', 'Средец'])
  assert.deepEqual(sortedDistricts(districts, 'en').map(f => districtLabel(f.properties.name, 'en')), ['Bankya', 'Sredets', 'Vazrazhdane'])
})
