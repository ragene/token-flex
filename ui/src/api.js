import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8001'

const api = axios.create({ baseURL: BASE_URL })

// ── Auth token storage ────────────────────────────────────────────────────────
const TOKEN_KEY = 'tf_access_token'
const TOKEN_EXP = 'tf_token_exp'

export function getStoredToken() {
  const exp = Number(localStorage.getItem(TOKEN_EXP) || 0)
  if (Date.now() / 1000 > exp - 60) {
    // expired or missing
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(TOKEN_EXP)
    return null
  }
  return localStorage.getItem(TOKEN_KEY)
}

export function storeToken(token, expiresInSeconds = 28800) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(TOKEN_EXP, String(Math.floor(Date.now() / 1000) + expiresInSeconds))
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TOKEN_EXP)
}

// Attach bearer token to every request if we have one
api.interceptors.request.use(config => {
  const token = getStoredToken()
  if (token) config.headers['Authorization'] = `Bearer ${token}`
  return config
})

// ── Auth endpoints ────────────────────────────────────────────────────────────
export const startDeviceFlow = () => api.post('/auth/device/start').then(r => r.data)
export const pollDeviceFlow  = (device_code) => api.post('/auth/device/poll', { device_code }).then(r => r.data)

// ── Data endpoints ────────────────────────────────────────────────────────────
export const getHealth        = () => api.get('/health').then(r => r.data)
export const getTokens        = () => api.get('/tokens').then(r => r.data)
export const getChunks        = (params = {}) => api.get('/chunks', { params }).then(r => r.data)
export const getSummaries     = (params = {}) => api.get('/summaries', { params }).then(r => r.data)
export const postSummarize    = (body) => api.post('/summarize', body).then(r => r.data)
export const postIngest       = (body) => api.post('/ingest', body).then(r => r.data)
export const postAutoIngest   = () => api.post('/memory/ingest/auto').then(r => r.data)
export const postMemoryQuery  = (body) => api.post('/memory/query', body).then(r => r.data)
export const postDistillAndClear = () => api.post('/token-data/distill').then(r => r.data)
