// src/App.jsx
// -----------------------------------------------------------------------------
// NotificationsProvider est placé ICI (et non dans main.jsx) car il utilise
// useLocation() : il doit être à l'intérieur du <BrowserRouter>, tout en
// englobant Layout et NotificationCenter qui consomment son état.
// -----------------------------------------------------------------------------

import { Routes, Route, Navigate } from 'react-router-dom'
import { NotificationsProvider } from './utils/NotificationsContext'
import Layout from './components/layout/Layout'
import Dashboard from './pages/Dashboard'
import Pods from './pages/Pods'
import YAMLGenerator from './pages/YAMLGenerator'
import Detection from './pages/Detection'
import DetectorComparison from './pages/DetectorComparison'
import MemoryPrediction from './pages/MemoryPrediction'
import Security from './pages/Security'
import DevOps from './pages/DevOps'
import Assistant from './pages/Assistant'
import Resources from './pages/Resources'
import MyApps from './pages/MyApps'

export default function App() {
  return (
    <NotificationsProvider>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard"  element={<Dashboard />} />
          <Route path="pods"       element={<Pods />} />
          <Route path="yaml"       element={<YAMLGenerator />} />
          <Route path="detection"  element={<Detection />} />
          <Route path="comparison" element={<DetectorComparison />} />
          <Route path="prediction" element={<MemoryPrediction />} />
          <Route path="resources"  element={<Resources />} />
          <Route path="security"   element={<Security />} />
          <Route path="devops"     element={<DevOps />} />
          <Route path="apps"       element={<MyApps />} />
          <Route path="assistant"  element={<Assistant />} />
        </Route>
      </Routes>
    </NotificationsProvider>
  )
}
