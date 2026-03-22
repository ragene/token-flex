import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8001'

const api = axios.create({ baseURL: BASE_URL })

export const getHealth = () => api.get('/health').then(r => r.data)
export const getTokens = () => api.get('/tokens').then(r => r.data)
export const getChunks = (params = {}) => api.get('/chunks', { params }).then(r => r.data)
export const getSummaries = (params = {}) => api.get('/summaries', { params }).then(r => r.data)
export const postSummarize = (body) => api.post('/summarize', body).then(r => r.data)
export const postIngest = (body) => api.post('/ingest', body).then(r => r.data)
export const postAutoIngest = () => api.post('/memory/ingest/auto').then(r => r.data)
export const postMemoryQuery = (body) => api.post('/memory/query', body).then(r => r.data)
export const postDistillAndClear = () => api.post('/token-data/distill').then(r => r.data)
