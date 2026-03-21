import React from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Chunks from './pages/Chunks.jsx'
import Summaries from './pages/Summaries.jsx'
import Ingest from './pages/Ingest.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="chunks" element={<Chunks />} />
          <Route path="summaries" element={<Summaries />} />
          <Route path="ingest" element={<Ingest />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
