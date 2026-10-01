// Dev: `/api`, which the Vite proxy forwards to the local backend. Production builds set
// VITE_API_BASE to the API origin (see frontend/.env.example).
export const API_BASE = (import.meta.env?.VITE_API_BASE || '/api').replace(/\/+$/, '')
