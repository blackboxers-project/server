import { NavLink } from 'react-router-dom'
import './NavBar.css'

const LINKS = [
  { to: '/',           label: 'Dashboard'   },
  { to: '/logs',       label: 'Flight Logs' },
  { to: '/blockchain', label: 'Blockchain'  },
]

export default function NavBar() {
  return (
    <header className="navbar">
      <h1>⬛ BLACKBOX</h1>
      <nav>
        {LINKS.map(({ to, label }) => (
          <NavLink key={to} to={to} end className={({ isActive }) => isActive ? 'active' : ''}>
            {label}
          </NavLink>
        ))}
      </nav>
    </header>
  )
}
