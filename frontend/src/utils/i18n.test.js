import assert from 'node:assert/strict'
import test from 'node:test'

import { compareSchoolNames, getAddress, getSchoolName } from './i18n.js'

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
