/**
 * Coverage for realistic parents: for each child a parent might have (stage, age group,
 * start year), the live data run through the site's own code must give a result at most
 * of the schools that publish the data for it. The flows check that pages work; this
 * checks that a normal parent does not get an empty answer everywhere, as when every
 * kindergarten said "no cut-off" for a 2027 start although 200 had one (#398).
 * No browser: the API answers go through the same functions the pages call.
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, test } from 'node:test'

import { ruleSetFor, sofiaToday, stageById, startYearChoices, summaryRowsFor } from '../../src/admission/calculator.js'
import { catchmentUrl, indexCatchments, schoolCatchmentIds } from '../../src/admission/catchments.js'
import { GRADE1_INITIAL, KINDERGARTEN_GROUPS, KINDERGARTEN_INITIAL, kindergartenAgeGroup } from '../../src/admission/inputs.js'
import { CATCHMENT_CAMPAIGNS } from '../../src/admission/loadCatchments.js'
import { kindergartenDistrict, savedVerdictsFor } from '../../src/admission/schoolVerdict.js'
import { usesSofiaKindergartenSystem } from '../../src/utils/admission.js'
import { statedTuition } from '../../src/utils/pricing.js'
import { languageKey } from '../../src/utils/languages.js'
import { getLanguageFocusPairs } from '../../src/utils/schoolAttributes.js'
import { allSchools } from './live.js'

// A parent should get an answer at no fewer than this share of the schools that publish
// the data for their child. Today's shares are 67–100%; an empty-result bug gives ~0%.
const MIN_SHARE = 0.5
// Fewer schools than this with the data means the data went missing, not the answer.
const MIN_WITH_DATA = 20

function ruleFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? ruleFiles(path) : name.endsWith('.json') ? [path] : []
  })
}
const ruleSets = ruleFiles(fileURLToPath(new URL('../../src/admission/rules', import.meta.url)))
  .map(file => JSON.parse(readFileSync(file, 'utf8')))
const snapshot = file =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../../public${catchmentUrl(CATCHMENT_CAMPAIGNS[0], file)}`, import.meta.url)), 'utf8'))
const today = sofiaToday()
const data = { ruleSets, districts: snapshot('districts.json'), catchments: indexCatchments(snapshot('catchments.json')), today }

/** The start years the calculator offers that have rules for this stage. */
const startYears = stage => startYearChoices().filter(year => ruleSetFor(ruleSets, stageById(stage), year, today))

/** Fails with every short persona at once, and logs each share for the run page. */
function assertCoverage(t, results) {
  for (const r of results) t.diagnostic(`${r.persona}: ${r.shown}/${r.withData}`)
  const short = results.filter(r => r.withData < MIN_WITH_DATA || r.shown < MIN_SHARE * r.withData)
  assert.deepEqual(
    short.map(r => `${r.persona}: a result at ${r.shown} of ${r.withData} schools with data`),
    [],
    `realistic parents get an answer at fewer than ${MIN_SHARE * 100}% of schools with the data (or under ${MIN_WITH_DATA} schools have it)`
  )
}

describe('coverage for realistic parents', () => {
  test('kindergarten: saved answers get a cut-off comparison at most kindergartens with one for the child', async t => {
    const kindergartens = (await allSchools()).filter(usesSofiaKindergartenSystem)
    const results = []
    for (const startYear of startYears('kindergarten')) {
      for (let age = 1; age <= 7; age += 1) {
        const birthYear = startYear - age
        const group = kindergartenAgeGroup({ birthYear }, startYear)
        if (!KINDERGARTEN_GROUPS.includes(group)) continue
        // A two-working-parent Sofia family with no priority, as the form fully answered.
        const answers = {
          ...KINDERGARTEN_INITIAL,
          birthYear,
          inSofia: true,
          addressSince: `${startYear - age}-01-15`,
          parentChildSameAddress: true,
          parentActivities: ['working', 'working'],
          nurseryAttended: false,
        }
        let withData = 0
        let shown = 0
        for (const school of kindergartens) {
          const rows = summaryRowsFor(school.admission_info?.cutoff_summary, stageById('kindergarten'))
          if (!rows.some(row => row.age_group === group)) continue
          withData += 1
          const saved = { kindergarten: { startYear, answers: { ...answers, homeDistrict: kindergartenDistrict(school, data.districts) } } }
          const verdict = savedVerdictsFor(school, saved, group, data)[0]
          if (verdict && verdict.verdict !== 'noCutoff') shown += 1
        }
        results.push({ persona: `start ${startYear}, born ${birthYear} (${group})`, withData, shown })
      }
    }
    assert.ok(results.length > 0, 'the calculator offers a start year with kindergarten rules')
    assertCoverage(t, results)
  })

  test('first grade: a parent in the catchment gets a cut-off comparison at most schools with one', async t => {
    const schools = (await allSchools()).filter(school =>
      schoolCatchmentIds(school).length > 0 &&
      summaryRowsFor(school.admission_info?.cutoff_summary, stageById('grade_1')).length > 0)
    const results = startYears('grade_1').map(startYear => {
      let shown = 0
      for (const school of schools) {
        const address = { check: { schoolId: schoolCatchmentIds(school)[0] }, inCatchment: true, since: `${startYear - 7}-01-15` }
        const saved = { grade_1: { startYear, answers: GRADE1_INITIAL, addresses: [address] } }
        const verdict = savedVerdictsFor(school, saved, 'grade_1_4', data)[0]
        if (verdict && !['noCutoff', 'needsAddress'].includes(verdict.verdict)) shown += 1
      }
      return { persona: `start ${startYear}, in the catchment`, withData: schools.length, shown }
    })
    assert.ok(results.length > 0, 'the calculator offers a start year with first-grade rules')
    assertCoverage(t, results)
  })

  test('fees: a search for an age the school takes shows a tuition at most schools with tuition', async t => {
    const schools = (await allSchools()).filter(school => (school.pricing || []).some(row => row.category === 'tuition'))
    const groups = new Map()
    for (const school of schools) {
      for (const group of new Set((school.locations || []).flatMap(l => l.age_groups || []))) {
        if (!groups.has(group)) groups.set(group, [])
        groups.get(group).push(school)
      }
    }
    const results = [{ persona: 'no age chosen', withData: schools.length, shown: schools.filter(s => statedTuition(s.pricing)).length }]
    for (const [group, taking] of [...groups].sort()) {
      // Too few schools take this group for a share to mean anything.
      if (taking.length < MIN_WITH_DATA) continue
      results.push({ persona: `age group ${group}`, withData: taking.length, shown: taking.filter(s => statedTuition(s.pricing, { ageGroup: group })).length })
    }
    assertCoverage(t, results)
  })

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
