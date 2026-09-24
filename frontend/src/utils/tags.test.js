import test from 'node:test'
import assert from 'node:assert/strict'

import { humanizeTag, isCyrillicTag, splitTagsByLanguage, toSentenceCase } from './tags.js'

test('Title Case English becomes sentence case, keeping acronyms', () => {
  assert.equal(
    toSentenceCase('Excursions And Extracurricular Activities'),
    'Excursions and extracurricular activities'
  )
  assert.equal(toSentenceCase('Clubs For STEM And IT'), 'Clubs for STEM and IT')
  assert.equal(toSentenceCase('Preparation For The IB Diploma'), 'Preparation for the IB diploma')
})

test('proper names without a capitalised function word are left alone', () => {
  assert.equal(toSentenceCase('Cambridge English'), 'Cambridge English')
  assert.equal(toSentenceCase('International Baccalaureate (IB)'), 'International Baccalaureate (IB)')
  assert.equal(toSentenceCase('Excursions and extracurricular activities'), 'Excursions and extracurricular activities')
})

test('humanizeTag reads keys and free text without Title-Casing sentences', () => {
  assert.equal(humanizeTag('science_lab'), 'Science lab')
  assert.equal(humanizeTag('Excursions and extracurricular activities'), 'Excursions and extracurricular activities')
  assert.equal(humanizeTag('Excursions And Extracurricular Activities'), 'Excursions and extracurricular activities')
  assert.equal(humanizeTag('обновена материална база'), 'Обновена материална база')
  assert.equal(humanizeTag('  '), '')
})

test('isCyrillicTag detects Bulgarian text', () => {
  assert.equal(isCyrillicTag('обновена материална база'), true)
  assert.equal(isCyrillicTag('МОН лиценз'), true)
  assert.equal(isCyrillicTag('Проект BG05M2OP001-3.018 Подкрепа за приобщаващо образование'), true)
  assert.equal(isCyrillicTag('Cambridge English'), false)
  assert.equal(isCyrillicTag('24/7'), false)
})

test('splitTagsByLanguage groups Bulgarian tags only outside the Bulgarian UI', () => {
  const tags = ['Clubs', 'спортна площадка', 'IB Diploma', 'видеонаблюдение']
  assert.deepEqual(splitTagsByLanguage(tags, 'en'), {
    main: ['Clubs', 'IB Diploma'],
    bulgarian: ['спортна площадка', 'видеонаблюдение'],
  })
  assert.deepEqual(splitTagsByLanguage(tags, 'bg'), { main: tags, bulgarian: [] })
  assert.deepEqual(splitTagsByLanguage(null, 'en'), { main: [], bulgarian: [] })
})
