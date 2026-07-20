import test from 'node:test'
import assert from 'node:assert/strict'

import { getBenchmarkComparison, getNvoDetail, prepareNvoTimelineData } from './nvo.js'

test('getNvoDetail only exposes school averages when both subjects have enough history', () => {
  const school = {
    education_level: 'lower_secondary',
    exam_results: [
      { exam_type: 'nvo_7', subject: 'math', metric: 'average_score', year: 2025, value: 61.0 },
      { exam_type: 'nvo_7', subject: 'bulgarian', metric: 'average_score', year: 2025, value: 72.5 },
      { exam_type: 'nvo_7', subject: 'math', metric: 'average_score', year: 2024, value: 59.0 },
      { exam_type: 'nvo_7', subject: 'bulgarian', metric: 'average_score', year: 2024, value: 70.0 },
    ],
  }

  const detail = getNvoDetail(school, (key) => key)

  assert.equal(detail.examType, 'nvo_7')
  assert.equal(detail.hasAverage, false)
  assert.equal(detail.mathAvg, null)
  assert.equal(detail.bgAvg, null)
  assert.equal(detail.schoolAverageCombined, null)
  assert.equal(detail.latestMath, 61.0)
  assert.equal(detail.latestBg, 72.5)
  assert.equal(detail.latestCombined, 66.75)
  assert.equal(detail.latestYear, 2025)
})

test('getNvoDetail can hide incomplete subject pairs for SchoolCard', () => {
  const school = {
    education_level: 'lower_secondary',
    exam_results: [
      { exam_type: 'nvo_7', subject: 'math', metric: 'average_score', year: 2025, value: 61.0 },
    ],
  }

  assert.equal(
    getNvoDetail(school, (key) => key, { requireCompleteSubjects: true }),
    null
  )
  assert.equal(getNvoDetail(school, (key) => key).latestMath, 61.0)
})

test('prepareNvoTimelineData merges subject-specific national benchmarks by year', () => {
  const chartData = prepareNvoTimelineData(
    [
      { exam_type: 'nvo_7', subject: 'math', metric: 'average_score', year: 2024, value: 58.0 },
      { exam_type: 'nvo_7', subject: 'bulgarian', metric: 'average_score', year: 2024, value: 69.0 },
      { exam_type: 'nvo_7', subject: 'math', metric: 'average_score', year: 2025, value: 61.0 },
      { exam_type: 'nvo_7', subject: 'bulgarian', metric: 'average_score', year: 2025, value: 72.5 },
    ],
    'nvo_7',
    {
      by_year: {
        nvo_7: {
          '2024': { math: 57.2, bulgarian: 67.8 },
          '2025': { math: 59.4, bulgarian: 70.1 },
        },
      },
    }
  )

  assert.deepEqual(chartData, [
    {
      year: 2024,
      math: 58,
      bulgarian: 69,
      nationalMath: 57.2,
      nationalBulgarian: 67.8,
    },
    {
      year: 2025,
      math: 61,
      bulgarian: 72.5,
      nationalMath: 59.4,
      nationalBulgarian: 70.1,
      mathPrevious: 58,
      bulgarianPrevious: 69,
    },
  ])
})

test('getBenchmarkComparison classifies against the same exam, year, and subject benchmark', () => {
  const comparison = getBenchmarkComparison({
    examType: 'nvo_7',
    year: 2025,
    subjectKey: 'math',
    value: 61,
    examAverages: {
      by_year: {
        nvo_7: {
          '2025': { math: 59.4, bulgarian: 70.1 },
        },
      },
    },
  })

  assert.equal(comparison.tone, 'near')
  assert.equal(comparison.diff, 1.6000000000000014)
  assert.equal(comparison.benchmarkValue, 59.4)
  assert.equal(comparison.textClass, 'text-amber-700')
})
