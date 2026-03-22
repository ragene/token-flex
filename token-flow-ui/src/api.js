import axios from 'axios'

// In production, VITE_API_URL is empty so all API calls go to the same origin
// (Envoy routes /token-data/*, /ingest, /chunks, etc. to the token-flow-api cluster).
// For local dev, set VITE_API_URL=http://localhost:8001 in .env.local
const BASE_URL = import.meta.env.VITE_API_URL || ''

const api = axios.create({ baseURL: BASE_URL })

// Attach Auth0 token from localStorage to all requests
api.interceptors.request.use(config => {
  const token = localStorage.getItem('tf_token')
  if (token) {
    config.headers = config.headers || {}
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

export const getHealth = () => api.get('/api/health').then(r => r.data)
export const getTokens = () => api.get('/api/tokens').then(r => r.data)
export const getCurrentSession = () => api.get('/api/session/current').then(r => r.data)
export const getChunks = (params = {}) => api.get('/api/chunks', { params }).then(r => r.data)
export const getSummaries = (params = {}) => api.get('/api/summaries', { params }).then(r => r.data)
export const postSummarize = (body) => api.post('/api/summarize', body).then(r => r.data)
export const postIngest = (body) => api.post('/api/ingest', body).then(r => r.data)
export const postAutoIngest = () => api.post('/api/memory/ingest/auto', {}).then(r => r.data)
export const postMemoryQuery = (body) => api.post('/api/memory/query', body).then(r => r.data)

// Token data REST endpoints (fallback / external callers)
export const getTokenSummary   = ()       => api.get('/token-data/summary').then(r => r.data)
export const getTokenEvents    = (params) => api.get('/token-data/events', { params }).then(r => r.data)
export const postRecordUsage   = (body)   => api.post('/token-data/record', body).then(r => r.data)
export const postPushSnapshot  = (body)   => api.post('/token-data/push', body).then(r => r.data)
export const postDistillAndClear = ()     => api.post('/token-data/distill').then(r => r.data)
export const getTokenExportUrl = ()       => `${BASE_URL}/token-data/export`

// WebSocket URL for the live token-data feed — JWT attached as query param
export const getTokenDataWsUrl = () => {
  const base = BASE_URL
    ? BASE_URL.replace(/^http/, 'ws') + '/token-data/ws'
    : (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.host + '/token-data/ws'
  const token = localStorage.getItem('tf_token')
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}

// Users management
export const getUsers = () => api.get('/api/users/').then(r => r.data)
export const patchUserRole = (id, role) => api.patch(`/api/users/${id}/role`, { role }).then(r => r.data)
export const activateUser = (id) => api.patch(`/api/users/${id}/activate`).then(r => r.data)
export const deactivateUser = (id) => api.patch(`/api/users/${id}/deactivate`).then(r => r.data)
export const deleteUser = (id) => api.delete(`/api/users/${id}`).then(r => r.data)
export const getMe = () => api.get('/api/users/me').then(r => r.data)
