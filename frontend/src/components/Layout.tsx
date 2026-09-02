import { Bell, Bot, Container, Gauge, HardDrive, LogOut, Menu, Settings, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../App'

const links = [
  ['/', 'Overview', Gauge], ['/storage', 'Storage', HardDrive], ['/disks', 'Disks', HardDrive], ['/docker', 'Docker', Container], ['/alerts', 'Alerts', Bell], ['/sense', 'Ask SENSE', Bot], ['/settings', 'Settings', Settings],
] as const

export default function Layout({ onLogout }: { onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const [monitoringConnected, setMonitoringConnected] = useState<boolean | null>(null)
  useEffect(() => {
    let active = true
    const renewActivity = () => {
      if (document.visibilityState === 'visible') {
        void api('/api/activity', { method: 'POST' })
          .then(() => { if (active) setMonitoringConnected(true) })
          .catch(() => { if (active) setMonitoringConnected(false) })
      }
    }
    const renewWhenVisible = () => { if (document.visibilityState === 'visible') renewActivity() }
    renewActivity()
    const timer = window.setInterval(renewActivity, 15_000)
    document.addEventListener('visibilitychange', renewWhenVisible)
    return () => {
      active = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', renewWhenVisible)
    }
  }, [])
  const logout = async () => { await api('/api/auth/logout', { method: 'POST' }); onLogout() }
  return <div className="app-shell">
    <aside className={open ? 'sidebar open' : 'sidebar'}>
      <div className="sidebar-head"><Brand /><button className="icon-button mobile-only" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></button></div>
      <nav>{links.map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === '/'} onClick={() => setOpen(false)}><Icon size={18} />{label}{label === 'Ask SENSE' && <span className="ai-dot" />}</NavLink>)}</nav>
      <div className="sidebar-foot"><div className="sense-status"><span className="status-dot good"/><div><strong>SENSE online</strong><small>Deterministic mode</small></div></div><button className="logout" onClick={logout}><LogOut size={17}/> Sign out</button></div>
    </aside>
    <main><header className="topbar"><button className="icon-button mobile-only" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu /></button><div><span className="eyebrow">SERVER INTELLIGENCE</span><strong>All systems observed</strong></div><span className={monitoringConnected === false ? "live offline" : "live"} aria-live="polite"><i/> {monitoringConnected == null ? "Checking monitoring" : monitoringConnected ? "Monitoring active" : "Connection interrupted"}</span></header><Outlet /></main>
  </div>
}
