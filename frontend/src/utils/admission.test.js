import test from 'node:test'
import assert from 'node:assert/strict'
import {
  classifyAdmissionRequirement,
  curatedRequirement,
  getAdmissionRequirement,
  getLastAdmittedPoints,
  getMinNvoScore,
  getMinNvoScores,
  getPointsHistory,
  usesSofiaKindergartenSystem,
} from './admission.js'

test('classifies English and Bulgarian interview requirements', () => {
  assert.equal(classifyAdmissionRequirement('Interview with the family').kind, 'interview')
  assert.equal(classifyAdmissionRequirement('Интервю с родителите').kind, 'interview')
  assert.equal(classifyAdmissionRequirement('Събеседване с детето').kind, 'interview')
})

test('classifies English and Bulgarian test requirements', () => {
  assert.equal(classifyAdmissionRequirement('Entrance exam').kind, 'test')
  assert.equal(classifyAdmissionRequirement(['Входящ тест']).kind, 'test')
  assert.equal(classifyAdmissionRequirement('Приемен изпит').kind, 'test')
})

test('negative phrases win over embedded test and interview keywords', () => {
  assert.equal(classifyAdmissionRequirement('No entrance exam').kind, 'none')
  assert.equal(classifyAdmissionRequirement('No test required').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Without an interview').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Без входен изпит').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Няма приемен изпит').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Без приемен изпит').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Не се изисква интервю').kind, 'none')
  assert.equal(classifyAdmissionRequirement('Тест не се изисква').kind, 'none')
})

test('required steps outrank separate no-exam fragments', () => {
  assert.equal(
    classifyAdmissionRequirement(['No entrance exam', 'Interview with the family']).kind,
    'interview'
  )
  assert.equal(
    classifyAdmissionRequirement(['Без изпит', 'Входящ тест по английски']).kind,
    'test'
  )
})

test('preserves full text when multiple distinct requirements are present', () => {
  assert.deepEqual(classifyAdmissionRequirement(['Interview', 'Entrance test']), {
    kind: 'other',
    text: 'Interview • Entrance test',
  })
  assert.deepEqual(classifyAdmissionRequirement(['No entrance exam', 'Document review']), {
    kind: 'other',
    text: 'No entrance exam • Document review',
  })
})

test('preserves unknown free text and joins extracted arrays for fallback rendering', () => {
  assert.deepEqual(classifyAdmissionRequirement(['Оценка на документи', 'Среща със семейството']), {
    kind: 'other',
    text: 'Оценка на документи • Среща със семейството',
  })
})

test('returns null for missing or unusable requirements', () => {
  assert.equal(classifyAdmissionRequirement(null), null)
  assert.equal(classifyAdmissionRequirement([]), null)
  assert.equal(classifyAdmissionRequirement({}), null)
})

test('usesSofiaKindergartenSystem only for Bulgarian state kindergartens', () => {
  assert.equal(usesSofiaKindergartenSystem({ school_type: 'state', education_level: 'kindergarten', country_code: 'bg' }), true)
  assert.equal(usesSofiaKindergartenSystem({ school_type: 'private', education_level: 'kindergarten', country_code: 'bg' }), false)
  assert.equal(usesSofiaKindergartenSystem({ school_type: 'state', education_level: 'kindergarten', country_code: 'ro' }), false)
  assert.equal(usesSofiaKindergartenSystem({ school_type: 'state', education_level: 'primary', country_code: 'bg' }), false)
})

test('curated requirement text is picked for the page language', () => {
  const perLanguage = { requirements: { bg: 'Прием чрез ИСОДЗ', en: 'Places through ISODZ' } }
  assert.equal(curatedRequirement(perLanguage, 'bg'), 'Прием чрез ИСОДЗ')
  assert.equal(curatedRequirement(perLanguage, 'en-GB'), 'Places through ISODZ')
  assert.equal(curatedRequirement({ requirements: { bg: 'Само на български' } }, 'en'), 'Само на български')
  // Plain values and the older { type } shape pass through unchanged.
  assert.equal(curatedRequirement({ requirements: 'Official interview' }, 'en'), 'Official interview')
  assert.deepEqual(curatedRequirement({ requirements: { type: 'interview' } }, 'bg'), { type: 'interview' })
  assert.equal(curatedRequirement(null, 'bg'), undefined)
  assert.equal(
    classifyAdmissionRequirement(curatedRequirement(perLanguage, 'bg')).text,
    'Прием чрез ИСОДЗ'
  )
})

const t = key => `t:${key}`

test('admission requirement label translates known kinds and keeps other text as written', () => {
  assert.deepEqual(getAdmissionRequirement('Interview with the family', t), {
    kind: 'interview', icon: '📝', text: 't:schoolCard.admissions.interviewRequired',
  })
  assert.deepEqual(getAdmissionRequirement('Приемен изпит', t), {
    kind: 'test', icon: '📋', text: 't:schoolCard.admissions.testRequired',
  })
  assert.deepEqual(getAdmissionRequirement('Без приемен изпит', t), {
    kind: 'none', icon: '✅', text: 't:schoolCard.admissions.noEntranceExam',
  })
  assert.deepEqual(getAdmissionRequirement('Portfolio review', t), {
    kind: 'other', icon: 'ℹ️', text: 'Portfolio review',
  })
  assert.equal(getAdmissionRequirement(null, t), null)
})

const kindergartenAdmission = {
  historical_thresholds: [
    { year: 2023, age_group: 'first', rounds: [{ round: 1, last_admitted_points: 9 }] },
    {
      year: 2024,
      age_group: 'first',
      rounds: [
        { round: 2, last_admitted_points: 7 },
        { round: 1, last_admitted_points: 8 },
      ],
    },
    { year: 2025, age_group: 'nursery', rounds: [{ round: 1, last_admitted_points: 12 }] },
    { year: 2025, age_group: 'second', rounds: [] },
    { year: 2025, age_group: 'third', rounds: null },
  ],
}

test('last admitted points come from the final round of the latest year for the age group', () => {
  assert.deepEqual(getLastAdmittedPoints(kindergartenAdmission, 'first'), { points: 7, year: 2024, round: 2 })
  assert.deepEqual(getLastAdmittedPoints(kindergartenAdmission, 'nursery'), { points: 12, year: 2025, round: 1 })
  assert.equal(getLastAdmittedPoints(kindergartenAdmission, 'second'), null)
  assert.equal(getLastAdmittedPoints(kindergartenAdmission, 'third'), null)
  assert.deepEqual(getPointsHistory(kindergartenAdmission, 'third'), [])
  assert.equal(getLastAdmittedPoints(kindergartenAdmission, 'preschool'), null)
  assert.equal(getLastAdmittedPoints({}, 'first'), null)
  assert.equal(getLastAdmittedPoints(null, null), null)
})

test('points history lists the final-round points per year, newest first', () => {
  assert.deepEqual(getPointsHistory(kindergartenAdmission, 'first'), [
    { year: 2024, points: 7 },
    { year: 2023, points: 9 },
  ])
  assert.deepEqual(getPointsHistory(undefined, 'first'), [])
})

test('minimum NVO score is the latest year, and the history is newest first', () => {
  const gymnasium = {
    historical_min_scores: [
      { year: 2023, min_score: 401.5 },
      { year: 2025, min_score: 412.25 },
      { year: 2024, min_score: 398 },
    ],
  }
  assert.deepEqual(getMinNvoScore(gymnasium), { score: 412.25, year: 2025 })
  assert.deepEqual(getMinNvoScores(gymnasium).map(item => item.year), [2025, 2024, 2023])
  assert.equal(getMinNvoScore({ historical_min_scores: [] }), null)
  assert.deepEqual(getMinNvoScores(null), [])
})
