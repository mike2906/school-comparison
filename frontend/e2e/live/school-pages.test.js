/**
 * Live flows for what a parent reads on a school: finding it by name (Latin saint
 * spellings and a brand in the web address too), the facts read
 * from its website (with open days), its Facebook and Instagram links, fees with their
 * payment plans, last year's fees left out once this year lists them again, fees for the
 * searched age first with the other ages folded away, a headline fee range that says what
 * each end is, compare for an age showing that age's fee, comparing fees
 * side by side, the exam heading naming the years its chart plots, exam results with a decimal
 * comma on the Bulgarian page, no pin for a school whose location is
 * only approximate (the page says the address is not on the map yet), and a nursery
 * called a nursery with an address that names its district. A school page also rides out a
 * dropped connection while the API restarts, and says it could not load rather than "not
 * found" when it cannot.
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { planNameLabel } from '../../src/utils/priceLabels.js'
import { rowAgeGroupKeys, splitRowsForAgeGroup } from '../../src/utils/priceRows.js'
import { getAvailableExamTypes, prepareNvoTimelineData } from '../../src/utils/nvo.js'
import { formatCurrency, groupPricingByAcademicYear, rangeEndLabels, statedTuition } from '../../src/utils/pricing.js'
import { upcomingOpenDays } from '../../src/utils/schoolFacts.js'
import { API, NoSchoolError, PHONE, address, allSchools, findSchool, open, page, schoolName, t, liveBrowser } from './live.js'

liveBrowser(PHONE)

const hasCoordinates = location => location.lat != null && location.lng != null
const isPrivate = school => ['private', 'international'].includes(school.school_type)
// The white card a section heading opens.
const card = heading => page.getByRole('heading', { name: heading, exact: true }).locator('xpath=ancestor::div[contains(@class, "rounded-2xl")][1]')
// One spelling of an address: a link may carry a Cyrillic page name as is or percent-encoded.
const sameUrl = url => new URL(url).href
const planRow = school => (school.pricing || []).find(row => (row.pricing_context?.payment_options || []).length > 0)

describe('finding a school', () => {
  test('search by name, open the school, switch to English', async () => {
    const school = (await allSchools()).find(s => isPrivate(s) && schoolName(s, 'bg').length > 6)
    assert.ok(school, 'a private school to search for')
    const name = schoolName(school, 'bg')
    await open('bg', '/search')
    // Phones get a shorter box of their own (nameSearch.placeholderShort).
    await page.getByPlaceholder(t('bg', 'nameSearch.placeholderShort')).filter({ visible: true }).first().fill(name)
    await page.waitForURL(url => url.searchParams.get('q') === name)
    await page.locator(`a[href="/schools/${school.id}"]`).first().click()
    await page.waitForURL(url => url.pathname === `/schools/${school.id}`)
    await page.getByRole('heading', { level: 1, name, exact: true }).waitFor()

    await page.getByRole('link', { name: 'EN', exact: true }).first().click()
    await page.waitForURL(url => url.pathname === `/en/schools/${school.id}`)
    await page.getByRole('heading', { level: 1, name: schoolName(school, 'en'), exact: true }).waitFor()
  })

  test('a Latin "Saint ..." spelling finds a school named „Света/Свети ...“', async () => {
    // Parents write "Saint Sofia" or "Sveta Sofia" for „Света София“ (St. Sofia). The
    // whole English name with "Saint" for "St." keeps the result list short.
    const school = (await allSchools()).find(s => /\bSt\. /.test(schoolName(s, 'en')) && /(^|[\s„"])Свет[аи] /.test(schoolName(s, 'bg')))
    assert.ok(school, 'a school named after a saint')
    const query = schoolName(school, 'en').replace(/\bSt\. /, 'Saint ')
    await open('bg', '/search')
    await page.getByPlaceholder(t('bg', 'nameSearch.placeholderShort')).filter({ visible: true }).first().fill(query)
    await page.waitForURL(url => url.searchParams.get('q') === query)
    await page.locator(`a[href="/schools/${school.id}"]`).first().waitFor()
  })

  test('a brand written only in the web address finds its school', async () => {
    // Busy School, Quest Junior: many private schools write their Latin brand only in the
    // logo, so a parent's spelling is in the address alone (busy-school.com, questjunior.bg).
    const generic = /^(www|school|schools|kindergarten|sofia|private|academy|college|online|kids)$/
    const brandWord = school => {
      const host = new URL(school.website_url).hostname.replace(/^www\./, '')
      const words = host.split('.').slice(0, -1).join(' ').split(/[^a-z]+/).filter(w => w.length >= 6 && !generic.test(w))
      const names = Object.values(school.resolved_name_i18n || {}).join(' ').toLowerCase()
      return words.find(word => !names.includes(word))
    }
    const school = await findSchool('with a brand only in its web address', s => s.website_url && brandWord(s), isPrivate)
    const query = brandWord(school)
    await open('bg', '/search')
    await page.getByPlaceholder(t('bg', 'nameSearch.placeholderShort')).filter({ visible: true }).first().fill(query)
    await page.waitForURL(url => url.searchParams.get('q') === query)
    await page.locator(`a[href="/schools/${school.id}"]`).first().waitFor()
  })

  test('a typed name sorts by best match first, and clearing it brings the old sort back', async () => {
    // "Saint Sofia" once listed a seminary named after another saint first, sorted by distance.
    const phrase = /(^|[\s„"])Св(\.|ет[аи])\s*София/i
    const best = new Set((await allSchools()).filter(s => phrase.test(schoolName(s, 'bg'))).map(s => s.id))
    assert.ok(best.size > 0, 'a school named „Света София“')
    await open('bg', '/search?sort=type')
    const sort = page.locator('#results-sort')
    const box = page.getByPlaceholder(t('bg', 'nameSearch.placeholderShort')).filter({ visible: true }).first()
    await box.fill('Saint Sofia')
    await page.waitForURL(url => url.searchParams.get('q') === 'Saint Sofia')
    await page.waitForFunction(() => document.querySelector('#results-sort')?.value === 'match')
    const first = await page.locator('article[data-school-id]').first().getAttribute('data-school-id')
    assert.ok(best.has(Number(first)), `first result ${first} is a „Света София“`)
    await box.fill('')
    await page.waitForFunction(() => document.querySelector('#results-sort')?.value === 'type')
    assert.equal(await sort.inputValue(), 'type')
  })

  test("a private kindergarten's name keeps its type, as the school writes it", async () => {
    // '"Частна детска градина СОФИЯ" ЕООД' once showed as a bare "Sofia", which reads as
    // the city. A name the school's own website gives (a brand) may still drop the type.
    const typed = /частна детска градина/i
    const registered = (await allSchools()).filter(s => isPrivate(s) && typed.test(s.name_i18n?.bg || ''))
    assert.ok(registered.length > 0, 'private kindergartens named in the register')
    const keeping = registered.filter(s => typed.test(schoolName(s, 'bg')))
    assert.ok(keeping.length >= registered.length * 0.8, `${keeping.length} of ${registered.length} keep the type`)
    const school = keeping.find(s => /kindergarten/i.test(schoolName(s, 'en')))
    assert.ok(school, 'one says "Kindergarten" in English')
    await open('en', `/schools/${school.id}`)
    await page.getByRole('heading', { level: 1, name: schoolName(school, 'en'), exact: true }).waitFor()
  })
})

describe('a school page', () => {
  test('facts from the school website link their pages, open days included', async testContext => {
    // Open days are listed only while upcoming, so some weeks no school has one; the
    // facts must still show then.
    const openDays = s => upcomingOpenDays(s).length > 0
    const school = await findSchool('with an open day', openDays, isPrivate, 200)
      .catch(error => {
        if (!(error instanceof NoSchoolError)) throw error
        return findSchool('with facts', s => (s.school_facts?.facts || []).length > 0, isPrivate)
      })
    await open('bg', `/schools/${school.id}`)
    const facts = card(t('bg', 'schoolFacts.title'))
    await facts.waitFor()
    // Every fact names the page it came from.
    const sources = await facts.locator('li a').evaluateAll(links => links.map(a => a.href))
    assert.ok(sources.length > 0 && sources.every(href => /^https?:\/\//.test(href)))
    if (!openDays(school)) {
      testContext.diagnostic('No school lists an upcoming open day; checked the facts only.')
      return
    }
    const line = facts.locator('dt', { hasText: t('bg', 'schoolFacts.glance.openDays') })
    await line.waitFor()
    const days = upcomingOpenDays(school)
    const expected = days.find(day => day.register_url)?.register_url || days[0].source_url
    const href = await line.locator('xpath=following-sibling::dd[1]//a').getAttribute('href')
    assert.equal(sameUrl(href), sameUrl(expected), 'the open day links its sign-up or announcement page')

    // The next open day also leads the key facts at the top, so nobody has to scroll past
    // the fees to find it.
    const strip = page.getByRole('region', { name: t('bg', 'schoolDetail.keyFacts') })
    const fact = strip.locator('div.rounded-xl', { hasText: t('bg', 'schoolDetail.nextOpenDay') })
    await fact.waitFor()
    const nextHref = await fact.getByRole('link').getAttribute('href')
    assert.equal(sameUrl(nextHref), sameUrl(expected), 'the top open day links the same page')
  })

  test("the school's own Facebook or Instagram is linked", async () => {
    const school = await findSchool('with social links', s => Boolean(s.social_links?.facebook || s.social_links?.instagram), () => true, 200)
    await open('bg', `/schools/${school.id}`)
    for (const network of ['facebook', 'instagram']) {
      const url = school.social_links[network]
      if (!url) continue
      const link = page.getByRole('link', { name: t('bg', `schools.${network}`), exact: true }).first()
      assert.equal(sameUrl(await link.getAttribute('href')), sameUrl(url))
      assert.equal(await link.getAttribute('target'), '_blank')
    }
  })

  test('a fee with a payment plan shows the plan under it, in euro', async () => {
    const listed = (await allSchools()).find(s => isPrivate(s) && planRow(s))
    assert.ok(listed, 'a private school with a payment plan')
    await open('bg', `/schools/${listed.id}`)
    const section = card(t('bg', 'pricing.title'))
    await section.getByText(t('bg', 'pricing.payInParts')).first().waitFor()
    const text = await section.innerText()
    assert.match(text, /EUR|€/, 'fees are shown in euro')
    assert.doesNotMatch(text, /лв|BGN/, 'no lev prices')
  })

  test("the English page names a fee in English, not in the website's Bulgarian", async () => {
    // 625's "половин ден" and "целодневно гледане" showed as is on the English page.
    const pageT = (key, { lng = 'en', ...vars } = {}) => {
      try { return t(lng, key, vars) } catch { return key }
    }
    // A row the page names in the reader's English (not as one of our grade or stage labels).
    const english = row => /\p{Script=Cyrillic}/u.test(row.plan_name || '') &&
      row.pricing_context?.plan_name_en && planNameLabel(row, pageT, 'en') === row.pricing_context.plan_name_en
    const listed = (await allSchools()).find(s => isPrivate(s) && (s.pricing || []).some(english))
    assert.ok(listed, 'a private school with a fee name in English')
    const row = listed.pricing.find(english)
    await open('en', `/schools/${listed.id}`)
    const section = card(t('en', 'pricing.title'))
    await section.getByText(row.pricing_context.plan_name_en, { exact: true }).first().waitFor()
    assert.equal(await section.getByText(row.plan_name, { exact: true }).count(), 0, `"${row.plan_name}" is not shown`)
  })

  test('a deposit is listed as a deposit, not as a registration fee', async testContext => {
    // 574's deposit, which comes off the last month, was listed as "Такса за записване".
    const deposit = row => row.category === 'registration' && row.pricing_context?.deposit === true
    const listed = (await allSchools()).find(s => isPrivate(s) && (s.pricing || []).some(deposit))
    if (!listed) {
      testContext.diagnostic('No school lists a deposit yet; nothing to check.')
      return
    }
    await open('bg', `/schools/${listed.id}`)
    const section = card(t('bg', 'pricing.title'))
    await section.getByText(t('bg', 'pricing.deposit'), { exact: true }).first().waitFor()
    if (!listed.pricing.some(row => row.category === 'registration' && !deposit(row))) {
      assert.equal(await section.getByText(t('bg', 'pricing.registration'), { exact: true }).count(), 0,
        'no registration heading for a deposit alone')
    }
  })

  test("last year's fee is left out once a later year lists it again at that price", async testContext => {
    // 596 showed its 2025-26 bus fees under the 2026-27 ones, which read as two prices.
    const shownYears = school => new Set(groupPricingByAcademicYear(school.pricing).map(group => group.academicYear))
    const listed = (await allSchools()).find(s => isPrivate(s) &&
      (s.pricing || []).some(row => row.academic_year_canonical && !shownYears(s).has(row.academic_year_canonical)))
    if (!listed) {
      testContext.diagnostic('No school lists a fee again in a later year; nothing to leave out.')
      return
    }
    const hidden = listed.pricing.find(row => row.academic_year_canonical && !shownYears(listed).has(row.academic_year_canonical))
    await open('bg', `/schools/${listed.id}`)
    const section = card(t('bg', 'pricing.title'))
    await section.waitFor()
    assert.equal(await section.getByText(hidden.academic_year_canonical, { exact: true }).count(), 0,
      `no ${hidden.academic_year_canonical} group`)
  })

  test("the searched age's fees lead; other ages' fees are folded away", async () => {
    // From a search for grades 5-7, a school listing every grade showed all of them at once.
    const groupOf = school => [...new Set((school.pricing || []).flatMap(row => [...rowAgeGroupKeys(row)]))]
      .find(group => {
        const { forGroup, otherAges } = splitRowsForAgeGroup(school.pricing, group)
        return forGroup.some(row => rowAgeGroupKeys(row).has(group)) && otherAges.length > 0
      })
    const listed = (await allSchools()).find(s => isPrivate(s) && groupOf(s))
    assert.ok(listed, 'a private school with fees for more than one age')
    const group = groupOf(listed)
    // As many as the fold lists: a past year's fee repeated later is left out there.
    const otherCount = groupPricingByAcademicYear(splitRowsForAgeGroup(listed.pricing, group).otherAges)
      .reduce((sum, yearGroup) => sum + yearGroup.rows.length, 0)
    await open('bg', `/schools/${listed.id}?group=${group}`)
    const section = card(t('bg', 'pricing.title'))
    const name = t('bg', `ageGroups.${group}`)
    await section.getByText(t('bg', 'pricing.forAgeGroup', { group: name.charAt(0).toLocaleLowerCase('bg') + name.slice(1) })).waitFor()
    const folded = section.locator('details')
    assert.equal(await folded.getAttribute('open'), null, 'other ages start folded')
    await folded.locator('summary', { hasText: t('bg', 'pricing.otherAges', { count: otherCount }) }).click()
    assert.notEqual(await folded.getAttribute('open'), null, 'other ages open on a tap')
  })

  test('the headline fee range says what each end is', async () => {
    // 541's "8064–9141 EUR" did not say half day and full day; 625's started at a promotion.
    const pageT = (key, vars = {}) => {
      try { return t('bg', key, vars) } catch { return key }
    }
    const ends = school => rangeEndLabels(statedTuition(school.pricing), pageT, 'bg')
    const listed = (await allSchools()).find(s => isPrivate(s) && ends(s))
    assert.ok(listed, 'a private school whose headline fee is a range')
    const { low, high } = ends(listed)
    await open('bg', `/schools/${listed.id}`)
    const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    await page.getByText(new RegExp(`^${escape(low)}: .+ · ${escape(high)}: `)).first().waitFor()
  })

  test('compare for an age shows that age\'s fee, not a fee naming no age', async () => {
    // 271 read "7,200–7,950" for grade 1: 7,950 was its kindergarten fee, stored with no age.
    const shown = fee => (fee.min === fee.max
      ? formatCurrency(fee.min, 'bg', fee.currency)
      : `${formatCurrency(fee.min, 'bg', fee.currency)}–${formatCurrency(fee.max, 'bg', fee.currency)}`)
    const groups = ['nursery', 'first', 'second', 'third', 'preschool', 'grade_1_4', 'grade_5_7', 'grade_8_12']
    const unnamed = row => rowAgeGroupKeys(row).size === 0
    const schools = (await allSchools()).filter(isPrivate)
    // An end of the all-ages range is a fee naming no age, and the age's own fee leaves it out.
    const picked = schools.flatMap(s => groups.map(group => {
      const forGroup = statedTuition(s.pricing, { ageGroup: group })
      const all = statedTuition(s.pricing)
      const unnamedEnd = [...(all?.ends?.low || []), ...(all?.ends?.high || [])].some(unnamed)
      return forGroup && unnamedEnd && !shown(forGroup).includes(shown(all)) ? { s, group, forGroup, all } : null
    })).find(Boolean)
    assert.ok(picked, 'a private school whose all-ages range ends at a fee naming no age')
    const other = schools.find(s => {
      const fee = s.id !== picked.s.id && statedTuition(s.pricing, { ageGroup: picked.group })
      return fee && !shown(fee).includes(shown(picked.all))
    })
    assert.ok(other, `a second private school with a fee for ${picked.group}`)
    await open('bg', `/compare?ids=${picked.s.id},${other.id}&age_group=${picked.group}`)
    await page.getByRole('heading', { level: 1, name: t('bg', 'compare.title') }).waitFor()
    await page.getByText(shown(picked.forGroup)).first().waitFor()
    assert.ok(!(await page.locator('body').innerText()).includes(shown(picked.all)), `${picked.s.id} shows its all-ages range`)
  })

  test('two private schools with fees compare side by side', async () => {
    const withFees = (await allSchools()).filter(s => isPrivate(s) && (s.pricing || []).some(row => row.amount != null || row.amount_min != null))
    assert.ok(withFees.length >= 2, 'two private schools with fees')
    const pair = withFees.slice(0, 2)
    await open('bg', `/compare?ids=${pair.map(s => s.id).join(',')}`)
    await page.getByRole('heading', { level: 1, name: t('bg', 'compare.title') }).waitFor()
    for (const school of pair) await page.getByText(schoolName(school, 'bg'), { exact: true }).first().waitFor()
    assert.match(await page.locator('body').innerText(), /EUR|€/)
  })

  test('the exam heading names the years its chart plots', async () => {
    // One NVO exam, so the chart opens on it, and more years than the five-year average uses.
    const school = await findSchool('with one NVO exam over six or more years', s => {
      const types = getAvailableExamTypes(s.exam_results || [])
      return types.length === 1 && prepareNvoTimelineData(s.exam_results, types[0]).length >= 6
    })
    const years = prepareNvoTimelineData(school.exam_results, getAvailableExamTypes(school.exam_results)[0]).map(item => item.year)
    await open('bg', `/schools/${school.id}`)
    await card(t('bg', 'schools.academicPerformance'))
      .getByText(t('bg', 'academicPerformance.basedOnYears', { start: years[0], end: years[years.length - 1] }))
      .waitFor()
  })

  test('the Bulgarian page writes exam results with a decimal comma', async () => {
    // Results always show one decimal ("56,0 т."), so any school with NVO results will do.
    const school = await findSchool('with NVO results', s => getAvailableExamTypes(s.exam_results || []).length > 0)
    await open('bg', `/schools/${school.id}`)
    const section = card(t('bg', 'schools.academicPerformance'))
    await section.getByText(/\d,\d т\./).first().waitFor()
    assert.equal(await section.getByText(/\d\.\d т\./).count(), 0, 'no decimal point on the Bulgarian page')
  })

  test('a school with only an approximate location has no pin but keeps its address', async () => {
    const listed = (await allSchools()).find(s => (s.locations || []).length > 0 && !(s.locations || []).some(hasCoordinates) &&
      s.locations.some(location => address(location, 'bg')))
    assert.ok(listed, 'a listed school without a published point')
    await open('bg', `/schools/${listed.id}`)
    await page.getByRole('heading', { name: t('bg', 'schools.locationsAndEnrollment') }).waitFor()
    assert.equal(await page.locator('a[href*="google.com/maps/dir"]').count(), 0, 'no directions to a guessed point')
    await page.getByText(t('bg', 'schoolDetail.notOnMap')).first().waitFor()
    await page.getByText(address(listed.locations.find(location => address(location, 'bg')), 'bg')).first().waitFor()
  })

  test('a nursery is called a nursery, and its address names its district', async () => {
    const nursery = (await allSchools()).find(s => s.admission_info?.admin_district && (s.locations || []).length > 0 &&
      s.locations.every(location => (location.age_groups || []).every(group => group === 'nursery')))
    assert.ok(nursery, 'a nursery with a district')
    await open('en', `/schools/${nursery.id}`)
    await page.getByText(t('en', 'educationLevels.nursery'), { exact: true }).first().waitFor()
    assert.equal(await page.getByText(t('en', 'educationLevels.kindergarten'), { exact: true }).count(), 0, 'not labelled a kindergarten')
    const shown = address(nursery.locations[0], 'en')
    assert.match(shown, / district$|district,/, 'the address names a district')
    await page.getByText(shown).first().waitFor()
  })
})

describe('a school page while the API restarts', () => {
  // A deploy or publish restarts the API for a few seconds: dropped connections there.
  const dropDetail = async (id, times) => {
    let dropped = 0
    await page.route(url => url.href.startsWith(API) && new URL(url).pathname === `/schools/${id}`, route => {
      if (dropped >= times) return route.continue()
      dropped += 1
      return route.abort('connectionreset')
    })
    return () => dropped
  }

  test('a dropped request is asked again and the school shows', async () => {
    const school = (await allSchools())[0]
    const dropped = await dropDetail(school.id, 1)
    await open('bg', `/schools/${school.id}`)
    await page.getByRole('heading', { level: 1, name: schoolName(school, 'bg'), exact: true }).waitFor()
    assert.ok(dropped() >= 1, 'the school request was dropped once')
  })

  test('a school that cannot load says so, not that it is gone', async () => {
    const school = (await allSchools())[0]
    await dropDetail(school.id, Infinity)
    await open('bg', `/schools/${school.id}`)
    await page.getByRole('heading', { name: t('bg', 'schools.loadFailed'), exact: true }).waitFor()
    await page.getByRole('button', { name: t('bg', 'common.tryAgain'), exact: true }).waitFor()
    assert.equal(await page.getByText(t('bg', 'schools.notFound'), { exact: true }).count(), 0)
  })
})
