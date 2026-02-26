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
  .map(tag => t(`locationTags.${tag}`, { defaultValue: tag }))

