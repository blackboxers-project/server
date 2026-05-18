import { BrowserRouter, Routes, Route } from 'react-router-dom'
import NavBar from './components/NavBar'
import Dashboard from './pages/Dashboard'
import Logs from './pages/Logs'
import Blockchain from './pages/Blockchain'
import './index.css'

export default function App() {
  return (
    <BrowserRouter>
      <NavBar />
      <Routes>
        <Route path="/"           element={<Dashboard />} />
        <Route path="/logs"       element={<Logs />} />
        <Route path="/blockchain" element={<Blockchain />} />
      </Routes>
    </BrowserRouter>
  )
}
