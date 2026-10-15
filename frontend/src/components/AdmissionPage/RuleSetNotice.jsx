import { useTranslation } from 'react-i18next'
import { campaignEnd, pickLanguage } from '../../admission/calculator'

const TONE = {
  verified: 'border-primary-200 bg-primary-50 text-primary-900',
  provisional: 'border-amber-200 bg-amber-50 text-amber-900',
  illustration: 'border-red-200 bg-red-50 text-red-900',
}

function formatDate(iso, language) {
  return new Intl.DateTimeFormat(language === 'bg' ? 'bg-BG' : 'en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
    .format(new Date(`${iso}T12:00:00`))
}

/**
 * How far the rules shown can be trusted for the year asked, any court rulings, and,
 * while the campaign is still ranking, until when (then the next year's rules apply).
 */
function RuleSetNotice({ selected, today = null }) {
  const { t, i18n } = useTranslation()
  const { ruleSet, applies, campaign, events } = selected
  const sources = new Map((ruleSet.sources || []).map(source => [source.id, source]))
  const end = ruleSet.campaign === campaign ? campaignEnd(ruleSet) : null
  const running = end && today && today <= end && !ruleSet.schedule?.estimated
  const nextYear = Number(campaign.slice(0, 4)) + 1

  return (
    <div className={`rounded-xl border p-4 text-sm ${TONE[applies]}`}>
      <p className="font-medium">
        {t(`admissionCalc.applies.${applies}`, {
          campaign,
          ruleCampaign: ruleSet.campaign,
          checked: ruleSet.verified?.on,
        })}
      </p>
      {running && (
        <p className="mt-1">
          {t('admissionCalc.runsUntil', { campaign, date: formatDate(end, i18n.language), next: `${nextYear}/${nextYear + 1}` })}
        </p>
      )}
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
