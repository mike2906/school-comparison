/**
 * The results list renders a window of cards, [start, end) of the sorted schools, and
 * grows it a page at a time as either edge comes near.
 */

// Cards are rendered in pages as the list scrolls: all ~440 at once took ~0.6 s per render.
export const LIST_PAGE_SIZE = 30
const CARDS_BEFORE = 10
const CARDS_AFTER = 20

/**
 * The window to render so the card at `index` is in it, or null when it already is.
 * A card far from the current window gets a fresh window around it: rendering every card
 * up to it froze the page for most of a second when a school was picked on the map.
 */
export function windowForIndex(index, { start, end }) {
  if (!Number.isInteger(index) || index < 0) return null
  if (index >= start && index < end) return null
  // Just past the rendered end: keep the cards above it.
  if (index >= end && index < end + LIST_PAGE_SIZE) return { start, end: index + CARDS_AFTER }
  return { start: Math.max(0, index - CARDS_BEFORE), end: index + CARDS_AFTER }
}
