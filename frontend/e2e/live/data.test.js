/**
 * Live data the parent checks found wrong, checked after each deploy and daily: the site's
 * own code on the live API must not show these again. Not in the PR coverage step, since a
 * data fix lands with a publish, not with the PR.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { languageKey } from '../../src/utils/languages.js'
import { getLanguageFocusPairs } from '../../src/utils/schoolAttributes.js'
import { allSchools } from './live.js'

describe('data the parent checks found wrong', () => {
  test('languages: every school with an official German intensive class is found by the German filter', async () => {
    // "Чужди езици - НЕ интензивно, АЕ": НЕ is RUO's code for German (73 СУ, 31 СУ were missed).
    const german = /(?<![\p{L}])(?:НЕ(?:\s+|\s*-\s*)интензивно|интензивно\s+НЕ)(?![\p{L}])/u
    const schools = (await allSchools()).filter(school =>
      (school.admission_info?.cutoff_summary || []).some(row => row.stage === 'grade_8' && german.test(row.class_name || '')))
    assert.ok(schools.length > 0, 'some school has a German intensive class')
    const key = languageKey('German')
    const missed = schools.filter(school => ![...getLanguageFocusPairs(school)].some(pair => pair.split(':')[0] === key))
    assert.deepEqual(missed.map(school => school.id), [], 'schools with a German intensive class but no German in their languages')
  })

  test('evening schools (ages 16 and up) are not in first-grade lists', async () => {
    const evening = /(?<![\p{L}])(?:сменно-?\s*)?вечерн[оа](?:-сменн[оа])?\s+(?:\p{L}+\s+)?(?:училище|гимназия)/iu
    const schools = (await allSchools()).filter(school => school.education_level !== 'kindergarten' && evening.test(school.name_i18n?.bg || ''))
    const listed = schools.filter(school => (school.locations || []).some(location => (location.age_groups || []).includes('grade_1_4')))
    assert.deepEqual(listed.map(school => school.id), [], 'evening schools taking grades 1-4')
  })
})
