import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { forgetSaved } from '../../admission/saved'
import { useSavedAdmission } from '../../hooks/useSavedAdmission'

/**
 * "Save on this device" under a result. Saving is the parent's choice: the answers can
 * include health and family details, so they are kept only in this browser and can be
 * forgotten here or on any page that uses them.
 */
function SaveAnswers({ stage, onSave, dirty, listUrl, blockedReason = null }) {
  const { t } = useTranslation()
  const saved = useSavedAdmission()[stage]
  return (
    <div className="mt-4 rounded-lg border border-neutral-200 p-3 text-sm">
      {saved && !dirty ? (
        <>
          <p className="text-neutral-800">{t('savedAdmission.saved')}</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            <Link to={listUrl} className="font-medium text-primary-700 underline">
              {t('savedAdmission.seeList')}
            </Link>
            <button type="button" onClick={() => forgetSaved(stage)} className="text-neutral-600 underline">{t('savedAdmission.forget')}</button>
          </div>
        </>
      ) : (
        <>
          <button
            type="button"
            onClick={onSave}
            disabled={Boolean(blockedReason)}
            className="w-full disabled:opacity-60 rounded-lg bg-primary-700 px-4 py-2 font-medium text-white hover:bg-primary-800"
          >
            {t(saved ? 'savedAdmission.update' : 'savedAdmission.save')}
          </button>
          <p className="mt-2 text-xs text-neutral-500">{blockedReason || t('savedAdmission.saveNote')}</p>
          {/* What the list will assume for every school, said before the parent saves. */}
          {!blockedReason && <p className="mt-1 text-xs text-neutral-500">{t('savedAdmission.listAssumes', { assumed: t(`savedAdmission.assumed.${stage}`) })}</p>}
        </>
      )}
    </div>
  )
}

export default SaveAnswers
