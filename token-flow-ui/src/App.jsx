import React from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Chunks from './pages/Chunks.jsx'
import Summaries from './pages/Summaries.jsx'
import Ingest from './pages/Ingest.jsx'
import TokenData from './pages/TokenData.jsx'
import MemoryEntries from './pages/MemoryEntries.jsx'
import Activity from './pages/Activity.jsx'
import Login from './pages/Login.jsx'
import AuthCallback from './pages/AuthCallback.jsx'
import Users from './pages/Users.jsx'
import Sessions from './pages/Sessions.jsx'
import PendingAccess from './pages/PendingAccess.jsx'

// Guard that checks for tf_token in localStorage
function RequireAuth({ children }) {
  const token = localStorage.getItem('tf_token')
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return children
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public routes */}
        <Route path="/login" element={<Login />} />
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="/pending-access" element={<PendingAccess />} />

        {/* Protected routes */}
        <Route
          path="/"
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="token-data" element={<TokenData />} />
          <Route path="memory" element={<MemoryEntries />} />
          <Route path="activity" element={<Activity />} />
          <Route path="chunks" element={<Chunks />} />
          <Route path="summaries" element={<Summaries />} />
          <Route path="ingest" element={<Ingest />} />
          <Route path="users" element={<RequireAuth><Users /></RequireAuth>} />
          <Route path="sessions" element={<RequireAuth><Sessions /></RequireAuth>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
