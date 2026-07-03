const FOCUS_EMOJIS = {
  science_focus: '🔬',
  arts_focus: '🎨',
  sports_focus: '⚽',
  music_focus: '🎵',
  technology_focus: '💻',
  language_focus: '🗣️',
}

export const isLocationFocusTag = (tag) => Object.prototype.hasOwnProperty.call(FOCUS_EMOJIS, tag)

export const getLocationFocusTags = (tags) => (tags || []).filter(isLocationFocusTag)

export const getFocusEmoji = (tag) => FOCUS_EMOJIS[tag] || ''

export const getFocusEmojis = (tags) => (tags || [])
  .map(tag => FOCUS_EMOJIS[tag])
  .filter(Boolean)

export const getFocusLabels = (t, tags) => (tags || [])
  .filter(isLocationFocusTag)
  .map(tag => t(`locationTags.${tag}`))
