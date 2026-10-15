/**
 * A school page's path, carrying the age group the parent searched for (`?group=`), so the
 * page leads with that age's exam results and fees.
 */
export function schoolPath(id, ageGroup = null) {
  return ageGroup ? `/schools/${id}?group=${encodeURIComponent(ageGroup)}` : `/schools/${id}`
}
