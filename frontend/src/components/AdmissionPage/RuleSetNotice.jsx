import { useTranslation } from 'react-i18next'
import { pickLanguage } from '../../admission/calculator'

const TONE = {
  verified: 'border-primary-200 bg-primary-50 text-primary-900',
  provisional: 'border-amber-200 bg-amber-50 text-amber-900',
  illustration: 'border-red-200 bg-red-50 text-red-900',
}

/** How far the rules shown can be trusted for the year asked, and any court rulings. */
function RuleSetNotice({ selected }) {
  const { t, i18n } = useTranslation()
  const { ruleSet, applies, campaign, events } = selected
  const sources = new Map((ruleSet.sources || []).map(source => [source.id, source]))

  return (
    <div className={`rounded-xl border p-4 text-sm ${TONE[applies]}`}>
      <p className="font-medium">
        {t(`admissionCalc.applies.${applies}`, {
          campaign,
          ruleCampaign: ruleSet.campaign,
          checked: ruleSet.verified?.on,
        })}
      </p>
      <p className="mt-1">
        {pickLanguage(ruleSet.title, i18n.language)}
        {ruleSet.official_system_url && (
          <>
            {' · '}
            <a href={ruleSet.official_system_url} target="_blank" rel="noreferrer" className="underline">
              {new URL(ruleSet.official_system_url).hostname}
            </a>
          </>
        )}
      </p>
      {events.length > 0 && (
        <ul className="mt-2 space-y-1">
          {events.map(event => {
            const source = sources.get(event.source)
            return (
              <li key={`${event.status}-${event.from}`}>
                {t(`admissionCalc.status.${event.status}`, { date: event.from })}
                {source?.url && (
                  <>
                    {' '}
                    <a href={source.url} target="_blank" rel="noreferrer" className="underline">
                      {t('admissionCalc.source')}
                    </a>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export default RuleSetNotice
