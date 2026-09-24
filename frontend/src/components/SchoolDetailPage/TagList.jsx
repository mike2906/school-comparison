import { useTranslation } from 'react-i18next'
import { splitTagsByLanguage } from '../../utils/tags'
import { getOptionLabel } from './helpers'

/**
 * Scraped free-text tags. In a non-Bulgarian UI, Bulgarian tags (which are never
 * machine-translated) are grouped under an "In Bulgarian" label instead of being mixed in.
 */
function TagList({ tags, chipClassName }) {
  const { t, i18n } = useTranslation()
  const { main, bulgarian } = splitTagsByLanguage(tags, i18n.language)

  const renderChips = (items, lang) => (
    <div className="flex flex-wrap gap-2" lang={lang}>
      {items.map((tag, idx) => (
        <span key={`${tag}-${idx}`} className={chipClassName}>
          {getOptionLabel(tag, t)}
        </span>
      ))}
    </div>
  )

  return (
    <div className="space-y-4">
      {main.length > 0 && renderChips(main)}
      {bulgarian.length > 0 && (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-neutral-500 mb-2">
            {t('schoolDetail.inBulgarian')}
          </div>
          {renderChips(bulgarian, 'bg')}
        </div>
      )}
    </div>
  )
}

export default TagList
