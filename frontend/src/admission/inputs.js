/**
 * The calculator forms' answers turned into evaluator input. Shared by the calculator
 * page and the per-school results in the list, so both count the same way. Pure, so it
 * runs under `node --test`.
 */
import { EVALUATORS } from './evaluators/index.js'
import { measuredOnFor } from './calculator.js'
import { calculateAgeGroup } from '../utils/education.js'

export const KINDERGARTEN_GROUPS = ['nursery', 'first', 'second', 'third', 'preschool']
export const SOCIAL_FLAGS = ['family_disability_70', 'unknown_or_restricted_parent', 'foster_or_adopted', 'child_at_risk', 'three_plus_children']

/** The kindergarten form before any answer. */
export const KINDERGARTEN_INITIAL = {
  birthYear: null,
  inSofia: null,
  addressSince: null,
  parentChildSameAddress: null,
  sameDistrict: null,
  homeDistrict: null,
  parentCount: 2,
  parentActivities: [null, null],
  twins: false,
  closeInAge: false,
  nurseryAttended: null,
  parentsDeceased: 'none',
  social: Object.fromEntries(SOCIAL_FLAGS.map(flag => [flag, false])),
  additional: 'none',
  transfer: false,
  siblingThere: null,
  wishRank: 1,
}

/** The first-grade form before any answer. */
export const GRADE1_INITIAL = {
  siblingAtSchool: null,
  siblingYoung: null,
  preparatoryGroupHere: null,
  disability50: false,
  parentDeceased: false,
  moreThanTwoChildren: null,
  twins: false,
  fosterUnknownAdopted: false,
}

/** One address on the first-grade form. `check` is the address-table lookup, when made. */
export const EMPTY_ADDRESS = { check: null, inCatchment: null, inNeighbouring: null, since: null }

export function kindergartenAgeGroup(a, startYear) {
  return a.birthYear ? calculateAgeGroup(startYear, a.birthYear) : null
}

/**
 * Kindergarten points for one kindergarten. `place` holds what depends on it: its
 * district code (null when not known), and whether there is a sibling there and where it
 * is on the wish list.
 */
export function evaluateKindergarten(ruleSet, a, startYear, place, today) {
  const ageGroup = kindergartenAgeGroup(a, startYear)
  if (ageGroup != null && !KINDERGARTEN_GROUPS.includes(ageGroup)) return null
  const knownDistrict = place.district ?? null
  const context = {
    district: knownDistrict ?? (a.sameDistrict === true ? 'home' : a.sameDistrict === false ? 'elsewhere' : null),
    siblingThere: place.siblingThere,
    wishRank: place.wishRank,
  }
  const answers = {
    ageGroup,
    inSofia: a.inSofia,
    addressSince: a.addressSince,
    parentChildSameAddress: a.parentChildSameAddress,
    homeDistrict: knownDistrict ? a.homeDistrict : (a.sameDistrict == null ? null : 'home'),
    parents: a.parentActivities.slice(0, a.parentCount).map(activity => ({ activity })),
    twins: a.twins,
    closeInAge: a.closeInAge,
    nurseryAttended: a.nurseryAttended,
    social: { parentsDeceased: a.parentsDeceased, ...a.social },
    additional: a.additional,
    transfer: a.transfer,
  }
  const measuredOn = measuredOnFor(ruleSet, today)
  return EVALUATORS[ruleSet.evaluator].evaluate(ruleSet.params, answers, context, { campaign: ruleSet.campaign, measuredOn })
}

/** First-grade group and points for one address at one school (`context`: sibling, preparatory group). */
export function evaluateGrade1(ruleSet, a, address, context) {
  return EVALUATORS[ruleSet.evaluator].evaluate(
    ruleSet.params,
    {
      address,
      disability50: a.disability50,
      parentDeceased: a.parentDeceased,
      moreThanTwoChildren: a.moreThanTwoChildren,
      twins: a.twins,
      fosterUnknownAdopted: a.fosterUnknownAdopted,
    },
    context,
    { measuredOn: measuredOnFor(ruleSet) }
  )
}
