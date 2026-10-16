import { useId } from 'react'
import { useTranslation } from 'react-i18next'

/** A titled block of questions. */
export function Section({ title, intro, children }) {
  return (
    <section className="rounded-2xl border border-neutral-200 bg-white p-5 md:p-6">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
      {intro && <p className="mt-1 text-sm text-neutral-600">{intro}</p>}
      <div className="mt-4 space-y-5">{children}</div>
    </section>
  )
}

/**
 * One question as a row of buttons. `options` are `{ value, label }`; null is a valid
 * value ("not sure"), so the selected option is compared by identity.
 */
export function Choice({ label, hint, options, value, onChange }) {
  const id = useId()
  return (
    <fieldset>
      <legend id={id} className="text-sm font-medium text-neutral-900">{label}</legend>
      {hint && <p className="mt-0.5 text-xs text-neutral-500">{hint}</p>}
      <div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-labelledby={id}>
        {options.map(option => {
          const selected = option.value === value
          return (
            <button
              key={String(option.value)}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(option.value)}
              className={`rounded-lg border px-3 py-1.5 text-sm ${
                selected
                  ? 'border-primary-600 bg-primary-50 font-medium text-primary-800'
                  : 'border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50'
              }`}
            >
              {option.label}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

/** Yes / no / not sure. */
export function YesNo({ label, hint, value, onChange }) {
  const { t } = useTranslation()
  return (
    <Choice
      label={label}
      hint={hint}
      value={value}
      onChange={onChange}
      options={[
        { value: true, label: t('admissionCalc.yes') },
        { value: false, label: t('admissionCalc.no') },
        { value: null, label: t('admissionCalc.notSure') },
      ]}
    />
  )
}

/** A labelled native control (select, input). */
export function Field({ label, hint, children }) {
  const id = useId()
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-neutral-900">{label}</label>
      {hint && <p className="mt-0.5 text-xs text-neutral-500">{hint}</p>}
      <div className="mt-2">{typeof children === 'function' ? children(id) : children}</div>
    </div>
  )
}

export const INPUT_CLASS = 'w-full max-w-xs rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm'

/** A date answer; empty means "not sure". */
export function DateField({ label, hint, value, onChange, max }) {
  return (
    <Field label={label} hint={hint}>
      {id => (
        <input
          id={id}
          type="date"
          value={value || ''}
          max={max}
          onChange={event => onChange(event.target.value || null)}
          className={INPUT_CLASS}
        />
      )}
    </Field>
  )
}

/** A number answer; empty means "not sure". */
export function NumberField({ label, hint, value, onChange, min, max, step = 'any' }) {
  return (
    <Field label={label} hint={hint}>
      {id => (
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          value={value ?? ''}
          onChange={event => {
            const raw = event.target.value
            onChange(raw === '' ? null : Number(raw))
          }}
          className={INPUT_CLASS}
        />
      )}
    </Field>
  )
}

/**
 * On phones the result sits below the questions, so its headline follows the reader. With
 * `saveId` (only while there is something to save) it also carries a button down to the
 * save box, which is screens away under the questions and the breakdown.
 */
export function MobileTotal({ children, saveId = null }) {
  const { t } = useTranslation()
  return (
    <div className="sticky bottom-0 z-10 -mx-4 flex items-center justify-between gap-3 border-t border-neutral-200 bg-white/95 px-4 py-3 text-sm backdrop-blur lg:hidden">
      <div className="min-w-0" aria-hidden="true">{children}</div>
      {saveId && (
        <button
          type="button"
          onClick={() => {
            const box = document.getElementById(saveId)
            if (!box) return
            const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
            box.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'center' })
            box.querySelector('button')?.focus({ preventScroll: true })
          }}
          className="shrink-0 rounded-lg border border-primary-700 px-3 py-1.5 font-medium text-primary-700"
        >
          {t('savedAdmission.jumpToSave')}
        </button>
      )}
    </div>
  )
}
