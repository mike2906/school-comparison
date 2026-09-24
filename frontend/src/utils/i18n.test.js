import assert from 'node:assert/strict'
import test from 'node:test'

import { compareSchoolNames, getAddress, getSchoolName, schoolMatchesQuery } from './i18n.js'

test('getSchoolName prefers resolved_name_i18n over legal name', () => {
  const school = {
    resolved_name_i18n: {
      bg: 'Fusion School',
      en: 'Fusion School'
    },
    name_i18n: {
      bg: 'Частно основно училище Фюжън ЕООД',
      en: 'CHASTNO OSNOVNO UCHILISHTE FYUZHAN EOOD'
    }
  }

  assert.equal(getSchoolName(school, 'en'), 'Fusion School')
  assert.equal(getSchoolName(school, 'bg'), 'Fusion School')
})

test('getSchoolName prefers resolved_name_i18n derived fallback', () => {
  const school = {
    resolved_name_i18n: {
      bg: 'Д-р Петър Берон',
      en: 'Dr. Petar Beron'
    },
    name_i18n: {
      bg: 'ДГ №1 Щастливо детство'
    }
  }

  assert.equal(getSchoolName(school, 'en'), 'Dr. Petar Beron')
})

test('getSchoolName falls back to name_i18n when resolved name is missing', () => {
  const school = {
    name_i18n: {
      bg: 'ДГ №1 Щастливо детство'
    }
  }

  assert.equal(getSchoolName(school, 'en'), 'ДГ №1 Щастливо детство')
})

test('getAddress falls back to available address value when en is missing', () => {
  const location = {
    address_i18n: {
      bg: 'ул. Иван Вазов 15, София'
    }
  }

  assert.equal(getAddress(location, 'en'), 'ул. Иван Вазов 15, София')
})

test('getAddress prefers resolved_address_i18n transliteration fallback', () => {
  const location = {
    address_i18n: {
      bg: 'бул. "Джеймс Баучер" № 116'
    },
    resolved_address_i18n: {
      bg: 'бул. "Джеймс Баучер" № 116',
      en: 'bul. "Dzheims Baucher" № 116'
    }
  }

  assert.equal(getAddress(location, 'en'), 'bul. "Dzheims Baucher" № 116')
})

test('compareSchoolNames sorts numbered schools numerically', () => {
  const names = ['101 SU', '10 SU', '2 SU', '1 SU', '119 SU']
  assert.deepEqual([...names].sort((a, b) => compareSchoolNames(a, b, 'en')), ['1 SU', '2 SU', '10 SU', '101 SU', '119 SU'])
})

test('compareSchoolNames ignores leading quote noise', () => {
  const names = ["''Yagodina", 'Aurora', 'Zora']
  assert.deepEqual([...names].sort((a, b) => compareSchoolNames(a, b, 'en')), ['Aurora', "''Yagodina", 'Zora'])
})

test('schoolMatchesQuery matches names in any language, numbers and partial words', () => {
  const school = {
    name_i18n: { bg: '1 Средно училище „Пенчо П. Славейков“' },
    resolved_name_i18n: { en: '1 Sredno uchilishte "Pencho P. Slaveykov"' },
  }
  assert.equal(schoolMatchesQuery(school, ''), true)
  assert.equal(schoolMatchesQuery(school, 'пенчо'), true)
  assert.equal(schoolMatchesQuery(school, 'Slaveykov 1'), true)
  assert.equal(schoolMatchesQuery(school, '"Pencho'), true)
  assert.equal(schoolMatchesQuery(school, 'Vazov'), false)
  assert.equal(schoolMatchesQuery({}, 'x'), false)
})

test('schoolMatchesQuery handles how parents write school names', () => {
  const su119 = { name_i18n: { bg: '119 Средно училище "Академик Михаил Арнаудов"' } }
  const su1190 = { name_i18n: { bg: '1190 Средно училище "Тест"' } }
  const smg = { name_i18n: { bg: 'Софийска математическа гимназия  "Паисий Хилендарски"' } }
  const npmg = { name_i18n: { bg: 'Национална природо-математическа  гимназия "Академик Любомир Чакалов"' } }
  const aeg1 = { name_i18n: { bg: 'Първа английска езикова гимназия' } }
  const ieg164 = { name_i18n: { bg: '164. гимназия с преподаване  на испански език "Мигел де Сервантес"' } }
  const ou150 = { name_i18n: { bg: '150-то ОСНОВНО УЧИЛИЩЕ  "ЦАР СИМЕОН ПЪРВИ"' } }
  const dg5 = { name_i18n: { bg: 'ДГ №5 Надежда (с яслени групи)' } }

  for (const q of ['119', '119 СУ', 'СУ 119', '119-то', '№119', '119th', 'No. 119', '119 su', '119-то СОУ']) {
    assert.equal(schoolMatchesQuery(su119, q), true, q)
  }
  assert.equal(schoolMatchesQuery(su1190, '119'), false)
  assert.equal(schoolMatchesQuery(su119, '119 ОУ'), false)
  assert.equal(schoolMatchesQuery(su119, 'xqzv'), false)

  for (const q of ['СМГ', 'smg', 'sofiyska matematicheska', 'смг паисий']) {
    assert.equal(schoolMatchesQuery(smg, q), true, q)
  }
  assert.equal(schoolMatchesQuery(smg, 'смг вазов'), false)
  assert.equal(schoolMatchesQuery(npmg, 'СМГ'), false)
  assert.equal(schoolMatchesQuery(npmg, 'НПМГ'), true)
  assert.equal(schoolMatchesQuery(npmg, 'npmg'), true)
  for (const q of ['1 АЕГ', '1-ва АЕГ', 'I АЕГ', 'Първа АЕГ', '1 aeg']) {
    assert.equal(schoolMatchesQuery(aeg1, q), true, q)
  }
  assert.equal(schoolMatchesQuery(aeg1, '2 АЕГ'), false)
  assert.equal(schoolMatchesQuery(ieg164, '164 ИЕГ'), true)
  assert.equal(schoolMatchesQuery(ou150, '150 ОУ'), true)
  assert.equal(schoolMatchesQuery(ou150, 'ОУ 150'), true)
  assert.equal(schoolMatchesQuery(dg5, 'ДГ 5'), true)
  assert.equal(schoolMatchesQuery(dg5, 'дг 55'), false)
  assert.equal(schoolMatchesQuery(dg5, 'constructor'), false)
})
