import { createContext, useContext, useState, useEffect } from 'react'

import { API_BASE } from '../api/base'

const CountryContext = createContext(null)

export function CountryProvider({ children, defaultCountry = 'bg' }) {
  const [config, setConfig] = useState(null)
  const [countryCode, setCountryCode] = useState(defaultCountry)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function loadConfig() {
      setLoading(true)
      setError(null)
      try {
        const response = await fetch(`${API_BASE}/countries/${countryCode}`)
        if (!response.ok) {
          throw new Error(`Failed to load country config: ${response.statusText}`)
        }
        const data = await response.json()
        if (!cancelled) {
          setConfig(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message)
          console.error('Failed to load country config:', err)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadConfig()
    return () => { cancelled = true }
  }, [countryCode])

  return (
    <CountryContext.Provider value={{ config, countryCode, setCountryCode, loading, error }}>
      {children}
    </CountryContext.Provider>
  )
}

export function useCountry() {
  const ctx = useContext(CountryContext)
  if (ctx === null) {
    // Return safe defaults when used outside provider (e.g., during router init)
    return { config: null, countryCode: 'bg', setCountryCode: () => {}, loading: true, error: null }
  }
  return ctx
}
