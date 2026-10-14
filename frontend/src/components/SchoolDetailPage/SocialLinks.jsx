import { useTranslation } from 'react-i18next'
import { safeSourceUrl } from '../../utils/priceSources'

const NETWORKS = ['facebook', 'instagram']

/** Links to the school's own Facebook page and Instagram profile, as its website links them. */
function SocialLinks({ links, className = '' }) {
  const { t } = useTranslation()
  return NETWORKS.map((network) => {
    const href = safeSourceUrl(links?.[network])
    if (!href) return null
    return (
      <a key={network} href={href} target="_blank" rel="noopener noreferrer" className={className}>
        {t(`schools.${network}`)}
      </a>
    )
  })
}

export default SocialLinks
