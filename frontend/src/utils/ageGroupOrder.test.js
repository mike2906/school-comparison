import test from 'node:test'
import assert from 'node:assert/strict'

import { orderAgeGroups } from './ageGroupOrder.js'

const config = {
  education_config: {
    age_groups: ['nursery', 'first', 'second', 'third', 'preschool', 'grade_1_4', 'grade_5_7', 'grade_8_12'].map(key => ({ key })),
  },
}

test('orderAgeGroups: youngest first, unknown keys last in their own order', () => {
  assert.deepEqual(
    orderAgeGroups(['first', 'nursery', 'preschool', 'second', 'third'], config),
    ['nursery', 'first', 'second', 'third', 'preschool'],
  )
  assert.deepEqual(orderAgeGroups(['grade_5_7', 'mystery', 'grade_1_4', 'other'], config), ['grade_1_4', 'grade_5_7', 'mystery', 'other'])
  assert.deepEqual(orderAgeGroups(['b', 'a'], null), ['b', 'a'])
})
