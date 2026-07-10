// Semantic location focus tags. The API only ever serves these (provenance and
// coords metadata are withheld server-side, see backend app/utils/location_tags.py),
// so no client-side allowlist filtering is needed — we just render what we're given.
const FOCUS_EMOJIS = {
  science_focus: '🔬',
  arts_focus: '🎨',
  sports_focus: '⚽',
  music_focus: '🎵',
  technology_focus: '💻',
  language_focus: '🗣️',
}

export const getFocusEmoji = (tag) => FOCUS_EMOJIS[tag] || ''

export const getFocusEmojis = (tags) => (tags || [])
  .map(tag => FOCUS_EMOJIS[tag])
  .filter(Boolean)

export const getFocusLabels = (t, tags) => (tags || [])
  .map(tag => t(`locationTags.${tag}`))
