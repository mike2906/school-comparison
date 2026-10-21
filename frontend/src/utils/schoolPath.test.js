import assert from 'node:assert/strict'
import { test } from 'node:test'

import { schoolPath } from './schoolPath.js'

test('schoolPath: carries the searched age group, else the bare page', () => {
  assert.equal(schoolPath(12, 'grade_1_4'), '/schools/12?group=grade_1_4')
  assert.equal(schoolPath(12), '/schools/12')
  assert.equal(schoolPath(12, null), '/schools/12')
})

test('schoolPath: carries the enrolment year only with an age group', () => {
  assert.equal(schoolPath(12, 'third', 2027), '/schools/12?group=third&year=2027')
  assert.equal(schoolPath(12, null, 2027), '/schools/12')
})
