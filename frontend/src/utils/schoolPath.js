/**
 * A school page's path, carrying the age group the parent searched for (`?group=`), so the
 * page leads with that age's exam results and fees, and the enrolment year that group is for
 * (`&year=`), so the page can say when the school's fees are for an earlier year.
 */
export function schoolPath(id, ageGroup = null, targetYear = null) {
  if (!ageGroup) return `/schools/${id}`
  const year = targetYear ? `&year=${encodeURIComponent(targetYear)}` : ''
  return `/schools/${id}?group=${encodeURIComponent(ageGroup)}${year}`
}
