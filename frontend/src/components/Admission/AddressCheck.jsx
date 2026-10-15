import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { addressSuggestions, catchmentSchoolFor, splitAddress, streetDisplayName } from '../../admission/catchments'
import { loadAddresses, loadDistricts } from '../../admission/loadCatchments'

/**
 * One address field → the official first-grade catchment of that address. The parent
 * types "Иван Щерев 5" (or "Ivan Shterev 5"), picks the street from the suggestions, and
 * the lookup runs in the browser against the address table, loaded on first use; the
 * address is never sent anywhere. Street names show in the page language.
 *
 * `onResult` gets `{ street, streetId, number, schoolId }` (schoolId null when the table
 * does not list the number) or null while the address is incomplete. `value`
 * (`{ streetId, number }`) sets the address from outside: a saved home or a found
 * location. The school the address belongs to is named unless it is one of `ownIds` or
 * `nameSchool` is false (the page says so itself).
 */
function AddressCheck({ onResult, catchments, ownIds = [], nameSchool = true, value = null, label = null }) {
  const { t, i18n } = useTranslation()
  const id = useId()
  const inputRef = useRef(null)
  const [streets, setStreets] = useState(null)
  const [failed, setFailed] = useState(false)
  const [districtNames, setDistrictNames] = useState(null)
  const [text, setText] = useState('')
  const [street, setStreet] = useState(null)
  const [number, setNumber] = useState('')

  const start = () => {
    if (streets || failed) return
    loadAddresses().then(data => setStreets(data.streets), () => setFailed(true))
    loadDistricts().then(
      data => setDistrictNames(new Map(data.features.map(f => [f.properties.code, f.properties.name]))),
      () => {}
    )
  }

  // An address handed in from outside replaces whatever was typed.
  const valueKey = value ? `${value.streetId}|${value.number}` : null
  useEffect(() => {
    if (valueKey) start()
  }, [valueKey])
  useEffect(() => {
    if (!valueKey || !streets) return
    const found = streets.find(s => s.id === value.streetId)
    if (!found) return
    setStreet(found)
    setNumber(value.number)
    setText('')
  }, [valueKey, streets])

  const nameOf = s => streetDisplayName(s, i18n.language, districtNames?.get(s.district))
  const shown = street ? `${nameOf(street)} ${number}`.trimEnd() : text

  const typed = splitAddress(text)
  const suggestions = !streets || street ? [] : addressSuggestions(streets, text)
  const noMatch = Boolean(streets && !street && typed.street.trim().length >= 2 && suggestions.length === 0)
  const schoolId = street && number.trim() ? catchmentSchoolFor(street, number) : undefined

  useEffect(() => {
    // An address handed in is not reported as missing while the table is still loading.
    if (!onResult || (valueKey && !streets && !failed)) return
    onResult(street && number.trim() ? { street, streetId: street.id, number, schoolId: schoolId ?? null } : null)
  }, [street, number, schoolId, Boolean(streets)])

  const change = next => {
    start()
    // Typing after a picked street changes its number; editing the street itself starts over.
    if (street) {
      const name = nameOf(street)
      // Backspace over the space after the name, with no number yet, lets go of the street.
      if (next.startsWith(name) && !(next === name && !number)) {
        setNumber(next.slice(name.length).trim())
        return
      }
      setStreet(null)
      setNumber('')
    }
    setText(next)
  }

  const pick = (option, withNumber) => {
    setStreet(option)
    setNumber(withNumber)
    setText('')
    // Without a number yet, the cursor waits after the street name for it.
    if (!withNumber) requestAnimationFrame(() => inputRef.current?.focus())
  }

  const schoolName = nameSchool && schoolId && !ownIds.includes(schoolId) ? catchments?.get(schoolId)?.properties.name : null

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4">
      <div className="relative">
        <label htmlFor={`${id}-address`} className="block text-sm font-medium text-neutral-800">
          {label || t('admissionArea.address')}
        </label>
        <input
          id={`${id}-address`}
          ref={inputRef}
          type="text"
          autoComplete="off"
          value={street ? `${shown}${number ? '' : ' '}` : shown}
          onFocus={start}
          onChange={event => change(event.target.value)}
          placeholder={t('admissionArea.addressPlaceholder')}
          className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
        {suggestions.length > 0 && (
          <ul className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-neutral-200 bg-white shadow-lg">
            {suggestions.map(({ street: option, number: withNumber, carry }) => (
              <li key={`${option.id}|${withNumber}`}>
                <button
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-neutral-100"
                  onClick={() => pick(option, withNumber || (carry ? typed.number : ''))}
                >
                  {withNumber ? `${nameOf(option)} ${withNumber}` : nameOf(option)}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {noMatch && <p className="mt-3 text-sm text-amber-800">{t('admissionArea.noMatch')}</p>}
      {failed && <p className="mt-3 text-sm text-red-700">{t('admissionArea.loadFailed')}</p>}
      {streets === null && !failed && text && <p className="mt-3 text-sm text-neutral-500">{t('common.loading')}</p>}
      {street && !number.trim() && <p className="mt-3 text-sm text-neutral-600">{t('admissionArea.addNumber')}</p>}
      {schoolId === null && <p className="mt-3 text-sm text-amber-800">{t('admissionArea.numberNotListed')}</p>}
      {schoolName && (
        <p className="mt-3 text-sm text-neutral-800">
          {t('admissionArea.addressBelongsTo', { school: schoolName })}
        </p>
      )}
      <p className="mt-3 text-xs text-neutral-500">{t('admissionArea.addressPrivacy')}</p>
    </div>
  )
}

export default AddressCheck
