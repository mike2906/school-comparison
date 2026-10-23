/**
 * Live flows for the admission features: the points calculator at /admission (all three
 * stages), a school's cut-offs and its link into the calculator, the first-grade
 * catchment check with a real address from the published address table (one address
 * field, as a parent types it), and answers saved on the device showing on every school
 * in the list.
 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { describe, test } from 'node:test'

import { catchmentSchoolFor, catchmentUrl, displayPlaceName, indexAddresses, schoolCatchmentIds, streetDisplayName } from '../../src/admission/catchments.js'
import { CATCHMENT_CAMPAIGNS } from '../../src/admission/loadCatchments.js'
import { usesSofiaKindergartenSystem } from '../../src/utils/admission.js'
import { DESKTOP, findSchool, number, open, page, schoolName, t, liveBrowser } from './live.js'
import { planningYear } from '../../src/utils/ageSelection.js'

liveBrowser(DESKTOP)

const ruleFile = async (stagePath, campaign) =>
  JSON.parse(await readFile(new URL(`../../src/admission/rules/bg/${stagePath}/${campaign.replace('/', '-')}.json`, import.meta.url)))

/** The start year the page has chosen, as the rule files name a campaign ("2027/2028"). */
async function campaign() {
  const year = Number(await page.getByLabel(t('bg', 'admissionCalc.startYear')).inputValue())
  return `${year}/${year + 1}`
}

/** A question's answer button, found by the question's own label. */
const answer = (question, option) =>
  page.getByRole('radiogroup', { name: question, exact: true }).getByRole('radio', { name: option, exact: true })

const addressTable = async file =>
  JSON.parse(await readFile(new URL(`../../public${catchmentUrl(CATCHMENT_CAMPAIGNS[0], file)}`, import.meta.url)))

/**
 * A Sofia street in one of these catchments with a plain house number, and a name no
 * other street has, so the suggestion to pick is clear. Read from the table the site
 * itself loads (public/ is served at the site root).
 */
async function addressIn(ids) {
  const streets = indexAddresses(await addressTable('addresses.json'))
  const districtNames = new Map((await addressTable('districts.json')).features.map(f => [f.properties.code, f.properties.name]))
  const sameName = new Map()
  for (const street of streets) sameName.set(street.key, (sameName.get(street.key) || 0) + 1)
  for (const street of streets) {
    // An ordinary street (УЛ.), typed without its prefix as a parent would.
    if (street.settlement !== 'ГР.СОФИЯ' || !/^УЛ\./.test(street.street) || street.key.length <= 4 || sameName.get(street.key) > 1) continue
    const number = [...street.numbers.entries()].find(([n, id]) => ids.includes(id) && /^\d+$/.test(n))?.[0]
    if (number) {
      assert.ok(ids.includes(catchmentSchoolFor(street, number)))
      return { street, number, district: districtNames.get(street.district) }
    }
  }
  assert.fail(`an address in catchment ${ids.join(', ')}`)
}

/** Types "street number" into the one address field and picks the suggestion. */
async function typeAddress(scope, { street, number, district }, lang) {
  const name = displayPlaceName(street.street, lang).replace(/^\S+\s/, '')
  await scope.getByLabel(t(lang, 'admissionArea.address')).first().fill(`${name} ${number}`)
  await scope.getByRole('button', { name: `${streetDisplayName(street, lang, district)} ${number}`, exact: true }).click()
}

describe('admission calculator', () => {
  test('kindergarten: a fully answered form gives one total that adds up', async () => {
    await open('bg', '/admission?stage=kindergarten')
    const kg = (key, vars) => t('bg', `admissionCalc.kg.${key}`, vars)
    const start = Number((await campaign()).slice(0, 4))
    // The same September the search starts from, so both give the child the same group.
    assert.equal(start, planningYear(), 'the calculator opens on the search\'s start year')
    // Three years before the start year is a kindergarten group (not the nursery).
    await page.getByLabel(kg('birthYear')).selectOption(String(start - 3))
    await answer(kg('inSofia'), t('bg', 'admissionCalc.yes')).click()
    const sameAddress = answer(kg('sameAddress'), t('bg', 'admissionCalc.yes'))
    if (await sameAddress.count()) await sameAddress.click()
    await page.getByLabel(kg('since')).fill(`${start - 4}-01-15`)
    await answer(kg('sameDistrict'), t('bg', 'admissionCalc.yes')).click()
    await answer(kg('parentActivity', { n: 1 }), kg('activity.working')).click()
    await answer(kg('parentActivity', { n: 2 }), kg('activity.working')).click()
    const nursery = answer(kg('nursery'), t('bg', 'admissionCalc.no'))
    if (await nursery.count()) await nursery.click()
    await answer(kg('siblingThere'), t('bg', 'admissionCalc.no')).click()
    // Facts a parent knows have no "not sure"; whether a kindergarten is in their district may not be known.
    assert.equal(await answer(kg('twins'), t('bg', 'admissionCalc.notSure')).count(), 0, 'no "not sure" on the twins question')
    assert.equal(await answer(kg('parentActivity', { n: 1 }), t('bg', 'admissionCalc.notSure')).count(), 0, 'no "not sure" on a parent\'s work')
    assert.equal(await answer(kg('sameDistrict'), t('bg', 'admissionCalc.notSure')).count(), 1, 'the district question keeps "not sure"')

    const aside = page.locator('aside').filter({ hasText: kg('total') })
    const total = number(await aside.locator('p.text-4xl').innerText())
    const items = await aside.locator('dd').allInnerTexts()
    assert.ok(items.length > 0, 'the total lists the criteria it counts')
    assert.equal(total, items.map(number).reduce((sum, value) => sum + value, 0), 'the total is the sum of its lines')
    assert.ok(total > 0, 'an address in Sofia and two working parents give points')
    assert.equal(await page.getByText(t('bg', 'admissionCalc.rangeNote')).count(), 0, 'every answer given: one total, not a range')
  })

  test('first grade: answering for any school gives a group', async () => {
    await open('bg', '/admission?stage=grade_1')
    const g1 = key => t('bg', `admissionCalc.g1.${key}`)
    await answer(g1('inCatchment'), t('bg', 'admissionCalc.yes')).click()
    const start = Number((await campaign()).slice(0, 4))
    await page.getByLabel(g1('since')).first().fill(`${start - 5}-01-15`)
    // An address inside the catchment for years is group 1 (of 1–4, 1 is best).
    const group = page.locator('aside p', { hasText: new RegExp(`^${g1('group')}$`) }).first().locator('xpath=following-sibling::p[1]')
    await group.filter({ hasText: /^1$/ }).waitFor()
    assert.equal(await page.getByTestId('next-tier-hint').count(), 0, 'the best group has nothing to wait for')

    // Two years in the catchment is group 2: the hint gives the day the address reaches group 1.
    await page.getByLabel(g1('since')).first().fill(`${start - 2}-01-15`)
    await page.getByTestId('next-tier-hint').filter({ hasText: `${start + 1}-01-16` }).waitFor()
  })

  test('eighth grade: each formula gives the score the rules say', async () => {
    await open('bg', '/admission?stage=grade_8')
    const g8 = key => t('bg', `admissionCalc.g8.${key}`)
    const rules = await ruleFile('grade_8', await campaign())
    const { grade_points: gradePoints, nvo_weight_variants: variants } = rules.params
    const nvo = { bulgarian: 80.5, math: 62 }
    await page.getByLabel(g8('bulgarian')).fill(String(nvo.bulgarian))
    await page.getByLabel(g8('math')).fill(String(nvo.math))
    await page.getByLabel(g8('grade.first')).selectOption('6')
    await page.getByLabel(g8('grade.second')).selectOption('5')
    await answer(g8('hasExam'), t('bg', 'admissionCalc.no')).click()
    assert.equal(await answer(g8('hasExam'), t('bg', 'admissionCalc.notSure')).count(), 1, 'whether a class has an aptitude exam keeps "not sure"')

    const rows = page.locator('table tbody tr')
    assert.equal(await rows.count(), variants.length, 'one row per formula the law allows')
    for (const weights of variants) {
      const label = t('bg', 'admissionCalc.g8.formulaText', { bg: weights.bulgarian, math: weights.math })
      const expected = Math.round((weights.bulgarian * nvo.bulgarian + weights.math * nvo.math + gradePoints['6'] + gradePoints['5']) * 100) / 100
      const shown = await rows.filter({ hasText: label }).locator('td').last().innerText()
      assert.equal(number(shown), expected, label)
    }
  })
})

describe('admission from a school page', () => {
  test('a kindergarten with cut-offs shows them and opens the calculator for it', async () => {
    const school = await findSchool(
      'kindergarten with cut-offs',
      () => true,
      s => usesSofiaKindergartenSystem(s) && (s.admission_info?.cutoff_summary || []).length > 0,
    )
    await open('bg', `/schools/${school.id}`)
    const cutoffs = page.locator('section').filter({ has: page.getByRole('heading', { name: t('bg', 'cutoffs.title') }) })
    const rows = cutoffs.getByRole('list', { name: t('bg', 'cutoffs.listLabel') }).locator(':scope > li')
    await rows.first().waitFor()
    assert.ok(await rows.count() > 0, 'a row per age group')
    // kg.sofia.bg keeps only the free-place rankings: the page says the main one is missing.
    if (school.admission_info.cutoff_summary.some(row => row.first?.round_kind === 'current')) {
      await cutoffs.getByText(t('bg', 'cutoffs.legend.mainMissing')).waitFor()
    }
    // Rankings are never side by side: every ranking of a row is one line under "All rankings".
    const several = school.admission_info.cutoff_summary.findIndex(row => row.rounds > 1)
    if (several >= 0) {
      const row = rows.nth(several)
      await row.locator('summary').click()
      assert.ok(await row.locator('ol > li').count() >= 2, 'one line per ranking')
    }
    await page.getByRole('heading', { name: t('bg', 'admissionArea.districtTitle') }).waitFor()

    await cutoffs.getByRole('link', { name: t('bg', 'cutoffs.calculate') }).click()
    await page.waitForURL(url => url.pathname === '/admission' && url.searchParams.get('school') === String(school.id))
    await page.getByRole('link', { name: schoolName(school, 'bg'), exact: true }).waitFor()
    // The calculator repeats the school's cut-offs under the form.
    await page.getByRole('heading', { name: t('bg', 'cutoffs.title') }).waitFor()
    await page.getByRole('tab', { name: t('bg', 'admissionCalc.stages.kindergarten'), selected: true }).waitFor()

    // A child the age of a group this kindergarten ranked: the result card compares the
    // total with that group's last admitted child, also when the row is from the
    // campaign before the start year chosen.
    const start = Number((await campaign()).slice(0, 4))
    const age = r => Number(r.campaign.slice(0, 4)) - r.birth_year
    const row = school.admission_info.cutoff_summary.find(r =>
      r.birth_year && r.stage === 'kindergarten' && r.campaign && Number(r.campaign.slice(0, 4)) <= start &&
      age(r) >= 1 && age(r) < 7 && (r.latest || r.first)?.last_admitted_points != null)
    if (row) {
      const birthYear = row.birth_year + start - Number(row.campaign.slice(0, 4))
      await page.getByLabel(t('bg', 'admissionCalc.kg.birthYear')).selectOption(String(birthYear))
      await page.locator('aside').getByText(t('bg', 'cutoffs.compare.title')).waitFor()
      // A sibling answered here is saved for this kindergarten, so the list counts it there.
      await answer(t('bg', 'admissionCalc.kg.siblingThere'), t('bg', 'admissionCalc.yes')).click()
      await page.getByRole('button', { name: t('bg', 'savedAdmission.save') }).click()
      const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('admissionAnswers') || 'null'))
      assert.equal(saved?.kindergarten?.siblings?.[school.id]?.siblingThere, true, 'the sibling is kept for this kindergarten')
      assert.equal(saved.kindergarten.answers.siblingThere, null, 'and not for every kindergarten')
      await page.evaluate(() => localStorage.removeItem('admissionAnswers'))
    }
  })

  test('a school with a preparatory group opens the kindergarten tab for it', async () => {
    const school = await findSchool(
      'with a preparatory group and a first-grade catchment',
      () => true,
      s => schoolCatchmentIds(s).length > 0 &&
        (s.admission_info?.cutoff_summary || []).some(row => row.stage === 'preparatory'),
    )
    await open('bg', `/schools/${school.id}`)
    const cutoffs = page.locator('section').filter({ has: page.getByRole('heading', { name: t('bg', 'cutoffs.title') }) })
    await cutoffs.getByRole('link', { name: t('bg', 'cutoffs.calculateStage.grade_1') }).waitFor()
    await cutoffs.getByRole('link', { name: t('bg', 'cutoffs.calculateStage.preparatory') }).click()
    await page.waitForURL(url => url.pathname === '/admission' && url.searchParams.get('stage') === 'kindergarten')
    await page.getByRole('tab', { name: t('bg', 'admissionCalc.stages.kindergarten'), selected: true }).waitFor()

    // Six years before the start year is the preparatory group, which this school ranks.
    const start = Number((await campaign()).slice(0, 4))
    await page.getByLabel(t('bg', 'admissionCalc.kg.birthYear')).selectOption(String(start - 6))
    await page.getByText(t('bg', 'admissionCalc.kg.group', { group: t('bg', 'ageGroups.preschool') })).waitFor()
  })

  test('the first-grade tab points to the preparatory group', async () => {
    await open('bg', '/admission?stage=grade_1')
    await page.getByRole('button', { name: t('bg', 'admissionCalc.preparatoryLink') }).click()
    await page.getByRole('tab', { name: t('bg', 'admissionCalc.stages.kindergarten'), selected: true }).waitFor()
  })

  test('an eighth-grade school shows its class cut-offs', async () => {
    const school = await findSchool(
      'with 8th-grade cut-offs',
      () => true,
      s => (s.admission_info?.cutoff_summary || []).some(row => row.stage === 'grade_8'),
    )
    await open('bg', `/schools/${school.id}`)
    const cutoffs = page.locator('section').filter({ has: page.getByRole('heading', { name: t('bg', 'cutoffs.title') }) })
    const rows = cutoffs.getByRole('list', { name: t('bg', 'cutoffs.listLabel') }).locator(':scope > li')
    await rows.first().waitFor()
    const classes = school.admission_info.cutoff_summary.filter(row => row.stage === 'grade_8')
    assert.ok(await rows.count() >= 1, `${classes.length} classes in the API`)
    // The first ranking is the headline; a later ranking that let in a lower score gets its own line.
    await cutoffs.getByText(t('bg', 'cutoffs.ref.main', { date: '' }).replace(/[,\s]+$/, '')).first().waitFor()
    // laterEasierRound: same kind of result (no gender quota) after a main ranking with places.
    if (classes.some(row => !row.quota && row.first?.places !== 0 && row.latest?.min_score < row.first?.min_score)) {
      await cutoffs.getByText(t('bg', 'cutoffs.later', { date: '' }).split('(')[0].trim()).first().waitFor()
    }
    // A later ranking with a higher score is explained where the rankings are listed (laterHarder).
    const higher = school.admission_info.cutoff_summary.findIndex(row => row.stage === 'grade_8' && row.latest?.min_score > row.first?.min_score)
    if (higher >= 0) {
      await rows.nth(higher).locator('summary').click()
      await rows.nth(higher).getByText(t('bg', 'cutoffs.historyNote')).waitFor()
    }
  })

  test('the English page shows eighth-grade classes in English', async () => {
    // Class names come from the RUO lists in Bulgarian; the translate step before each publish
    // gives them English names (class_name_en).
    const school = await findSchool(
      'with translated 8th-grade classes',
      () => true,
      s => (s.admission_info?.cutoff_summary || []).some(row => row.stage === 'grade_8' && row.class_name_en),
    )
    await open('en', `/schools/${school.id}`)
    const cutoffs = page.locator('section').filter({ has: page.getByRole('heading', { name: t('en', 'cutoffs.title') }) })
    const row = school.admission_info.cutoff_summary.find(r => r.stage === 'grade_8' && r.class_name_en)
    await cutoffs.getByText(row.class_name_en).first().waitFor()
    assert.equal(await cutoffs.getByText(row.class_name, { exact: true }).count(), 0, 'not the Bulgarian class name')
  })

  test('a first-grade catchment check names the right area for a real address', async () => {
    const school = await findSchool('with a first-grade catchment', () => true, s => schoolCatchmentIds(s).length > 0)
    const address = await addressIn(schoolCatchmentIds(school))
    await open('bg', `/schools/${school.id}`)
    const area = page.locator('section').filter({ has: page.getByRole('heading', { name: t('bg', 'admissionArea.catchmentTitle') }) })
    await area.locator('.leaflet-container').waitFor()
    // With nothing saved, "use my location" comes before typing an address.
    await area.getByRole('button', { name: t('bg', 'location.useMyLocation') }).waitFor()
    await typeAddress(area, address, 'bg')
    await area.getByText(t('bg', 'admissionArea.verdict.inside')).waitFor()
  })

  test('in English the address field shows street names in Latin letters', async () => {
    const school = await findSchool('with a first-grade catchment', () => true, s => schoolCatchmentIds(s).length > 0)
    const address = await addressIn(schoolCatchmentIds(school))
    await open('en', `/schools/${school.id}`)
    const area = page.locator('section').filter({ has: page.getByRole('heading', { name: t('en', 'admissionArea.catchmentTitle') }) })
    await typeAddress(area, address, 'en')
    await area.getByText(t('en', 'admissionArea.verdict.inside')).waitFor()
    assert.match(await area.getByLabel(t('en', 'admissionArea.address')).inputValue(), /^ul\. [A-Za-z]/)

    // The calculator names the address's school in English too, not by its Cyrillic register name.
    await open('en', '/admission?stage=grade_1')
    await typeAddress(page.locator('main'), address, 'en')
    const [before, after] = t('en', 'admissionCalc.g1.forCatchmentSchool', { school: '§' }).split('§')
    // The Bulgarian register name shows until the school list has loaded.
    const sentence = page.getByText(before.trim()).first()
    const named = async () => {
      const text = await sentence.innerText()
      return text.slice(text.indexOf(before) + before.length, text.indexOf(after))
    }
    for (let tries = 0; tries < 20 && /[А-Яа-я]/.test(await named()); tries++) await page.waitForTimeout(500)
    assert.ok(!/[А-Яа-я]/.test(await named()), `school named in English: ${await named()}`)
  })

  test('saved first-grade answers compare every school in the list, and can be forgotten', async () => {
    const school = await findSchool('with a first-grade catchment and cut-offs', () => true, s =>
      schoolCatchmentIds(s).length > 0 && (s.admission_info?.cutoff_summary || []).some(row => row.stage === 'grade_1'))
    const address = await addressIn(schoolCatchmentIds(school))
    await open('bg', '/admission?stage=grade_1')
    const g1 = key => t('bg', `admissionCalc.g1.${key}`)
    await typeAddress(page.locator('main'), address, 'bg')
    const start = Number((await campaign()).slice(0, 4))
    await page.getByLabel(g1('since')).first().fill(`${start - 5}-01-15`)
    await page.getByRole('button', { name: t('bg', 'savedAdmission.save') }).click()
    // "Near your saved address" opens the list already placed at the saved home.
    // OpenStreetMap turns busy runners away without CORS headers; name that instead of a timeout.
    const refused = []
    const noteRefusal = request => /nominatim\.openstreetmap\.org/.test(request.url()) && refused.push(request.failure()?.errorText)
    page.on('requestfailed', noteRefusal)
    await page.getByRole('link', { name: t('bg', 'savedAdmission.seeListNear') }).click()
    await page.waitForURL(url => url.pathname === '/search' && url.searchParams.get('age_group') === 'grade_1_4')
    await page.getByTestId('saved-admission-banner').waitFor()
    await page.getByTestId('saved-verdict-line').first().waitFor()
    try {
      await page.waitForFunction(() => JSON.parse(localStorage.getItem('userLocation') || 'null')?.savedHome, null, { timeout: 30_000 })
    } catch (error) {
      if (refused.length) throw new Error(`OpenStreetMap refused the saved-address lookup (${refused.join(', ')}), not a site bug`)
      throw error
    } finally {
      page.off('requestfailed', noteRefusal)
    }
    // Used once: a reload or Back must not send the address again without a tap.
    assert.ok(!(await page.evaluate(() => history.state?.usr?.nearSavedHome)), 'the arrival flag stays in history')
    await page.getByRole('button', { name: t('bg', 'location.clear'), exact: true }).first().click()
    // Turned off, the location stays off: a reload neither falls back to the saved home nor
    // looks the saved address up again.
    const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem('userLocation') || 'null'))
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('userLocation') || 'null')?.off)
    await page.reload()
    await page.getByTestId('saved-admission-banner').waitFor()
    assert.deepEqual(await stored(), { off: true }, 'a reload brought the saved home back as the location')
    // The list itself offers the saved home as its location too. The tap runs the same lookup
    // as the arrival above, so it is not sent to OpenStreetMap a second time.
    const locationToggle = page.getByRole('button', { name: t('bg', 'location.setShortSaved') }).first()
    await locationToggle.click()
    await page.getByRole('button', { name: t('bg', 'location.useSavedHome') }).waitFor()
    await locationToggle.click()
    // Hiding schools where the saved result is below the last admitted child.
    await page.getByTestId('hide-below').check()
    await page.getByTestId('hidden-count').waitFor()
    await page.getByTestId('hide-below').uncheck()
    // Opened plainly, the list offers to set itself up for the saved answers.
    await open('bg', '/search')
    await page.getByTestId('saved-show-for').click()
    await page.waitForURL(url => url.searchParams.get('age_group') === 'grade_1_4' && url.searchParams.get('school_type') === 'state')

    // The school page compares the same answers with that school, and uses the address.
    await open('bg', `/schools/${school.id}`)
    await page.getByTestId('saved-verdict').waitFor()
    // The same result leads the facts strip at the top of the page.
    await page.getByText(t('bg', 'savedAdmission.factLabel', { stage: t('bg', 'savedAdmission.stageName.grade_1') }), { exact: true }).first().waitFor()
    // Each compared cut-off says which campaign it is from (2026/27), not a bare date.
    for (const line of await page.getByTestId('saved-verdict').locator('li').allInnerTexts()) {
      assert.match(line, /\d{4}\/\d{2}\b/, line)
    }
    const area = page.locator('section').filter({ has: page.getByRole('heading', { name: t('bg', 'admissionArea.catchmentTitle') }) })
    await area.getByText(t('bg', 'admissionArea.verdict.inside')).waitFor()

    await page.getByTestId('saved-verdict').getByRole('button', { name: t('bg', 'savedAdmission.forget') }).click()
    assert.equal(await page.getByTestId('saved-verdict').count(), 0)
  })

  test('the calculator is in the English menu', async () => {
    await open('en', '/search')
    await page.getByRole('link', { name: t('en', 'nav.admission'), exact: true }).first().click()
    await page.waitForURL(url => url.pathname === '/en/admission')
    await page.getByRole('heading', { level: 1, name: t('en', 'admissionCalc.title') }).waitFor()
  })
})
