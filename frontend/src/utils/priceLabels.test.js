import assert from 'node:assert/strict'
import test from 'node:test'

import { planNameLabel, stageLabel, textLang } from './priceLabels.js'

const STRINGS = {
  en: {
    'pricing.transport': 'Transport',
    'pricing.materials': 'Materials',
    'ageGroups.nursery': 'Nursery',
    'educationLevels.kindergarten': 'Kindergarten',
    'pricing.grades.preschool': 'Preschool',
    'pricing.grades.one': 'Grade {{n}}',
    'pricing.grades.range': 'Grades {{from}}–{{to}}',
  },
  bg: { 'pricing.transport': 'Транспорт', 'pricing.materials': 'Материали' },
}
const t = (key, { lng = 'en', ...vars } = {}) => {
  const value = STRINGS[lng][key] ?? key
  return value.replace(/\{\{(\w+)\}\}/g, (_, name) => String(vars[name]))
}

test('planNameLabel drops a plan name that only repeats its category, in either language', () => {
  assert.equal(planNameLabel({ category: 'transport', plan_name: 'Транспорт' }, t), null)
  assert.equal(planNameLabel({ category: 'materials', plan_name: ' материали ' }, t), null)
  assert.equal(planNameLabel({ category: 'transport', plan_name: 'Transport' }, t), null)
  // The same word under another category says something.
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Транспорт' }, t), 'Транспорт')
})

test('planNameLabel shows a bare grade or stage as our label and other text as written', () => {
  assert.equal(planNameLabel({ category: 'tuition', plan_name: '10. клас' }, t), 'Grade 10')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'VIII - XII клас' }, t), 'Grades 8–12')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Ясла' }, t), 'Nursery')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Ясла / Месечна такса' }, t), 'Ясла / Месечна такса')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: '2 клас и 3 клас' }, t), '2 клас и 3 клас')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: '500' }, t), '500')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: '  ' }, t), null)
  assert.equal(planNameLabel({ category: 'tuition' }, t), null)
})

test('stageLabel knows nursery and kindergarten only as the whole text', () => {
  assert.equal(stageLabel('Детска градина', t), 'Kindergarten')
  assert.equal(stageLabel('градина', t), 'Kindergarten')
  assert.equal(stageLabel('Детска градина & предучилищна група', t), null)
})

test('textLang marks text that is not in the page language', () => {
  assert.equal(textLang('Модерни танци', 'en'), 'bg')
  assert.equal(textLang('Модерни танци', 'bg'), undefined)
  assert.equal(textLang('English Lessons', 'bg'), 'en')
  assert.equal(textLang('English Lessons', 'en'), undefined)
  assert.equal(textLang('500', 'bg'), undefined)
  assert.equal(textLang(null, 'en'), undefined)
})

test('planNameLabel reads preschool and school stages written alone', () => {
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Preschool' }, t), 'Preschool')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Гимназия' }, t), 'Grades 8–12')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: '1-7 grade' }, t), 'Grades 1–7')
  // A stage beside other words is the website's own name.
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Гимназия / Месечна такса' }, t), 'Гимназия / Месечна такса')
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Такса за 8. клас за годината' }, t), 'Такса за 8. клас за годината')
})

test('planNameLabel keeps names that only look like grades', () => {
  // A language gymnasium's intensive 8th-grade year, not preschool.
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'Подготвителен клас' }, t), 'Подготвителен клас')
  // Payment options numbered I, II, IV.
  assert.equal(planNameLabel({ category: 'tuition', plan_name: 'IV' }, t), 'IV')
})
