import { Bell, Bot, Container, Gauge, HardDrive, LogOut, Menu, Settings, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { Brand } from '../App'

const links = [
  ['/', 'Overview', Gauge], ['/storage', 'Storage', HardDrive], ['/disks', 'Disks', HardDrive], ['/docker', 'Docker', Container], ['/alerts', 'Alerts', Bell], ['/sense', 'Ask SENSE', Bot], ['/settings', 'Settings', Settings],
] as const

export default function Layout({ onLogout }: { onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const [monitoringConnected, setMonitoringConnected] = useState<boolean | null>(null)
  const [senseUnread, setSenseUnread] = useState(0)
  const browserNotices = useRef(false)
  const lastNoticeId = useRef<number | null>(null)
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
  useEffect(() => {
    let active = true
    void api<{ browser_notifications?: boolean }>('/api/settings/ai')
      .then((settings) => { browserNotices.current = Boolean(settings?.browser_notifications) })
      .catch(() => undefined)
    const refresh = () => {
      void api<{ unread: number; items: Array<{ id: number; title: string; preview: string }> }>('/api/ai/notifications?unread_only=true')
        .then((result) => {
          if (!active) return
          setSenseUnread(result?.unread ?? 0)
          const newest = result?.items?.[0]
          if (lastNoticeId.current != null && newest?.id > lastNoticeId.current && browserNotices.current && 'Notification' in window && Notification.permission === 'granted') {
            new Notification(newest.title, { body: newest.preview })
          }
          if (newest) lastNoticeId.current = newest.id
        })
        .catch(() => undefined)
    }
    refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  const logout = async () => { await api('/api/auth/logout', { method: 'POST' }); onLogout() }
  return <div className="app-shell">
    <aside className={open ? 'sidebar open' : 'sidebar'}>
      <div className="sidebar-head"><Brand /><button className="icon-button mobile-only" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></button></div>
      <nav>{links.map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === '/'} onClick={() => setOpen(false)}><Icon size={18} />{label}{label === 'Ask SENSE' && (senseUnread ? <span className="sense-unread">{senseUnread}</span> : <span className="ai-dot" />)}</NavLink>)}</nav>
      <div className="sidebar-foot"><div className="sense-status"><span className="status-dot good"/><div><strong>SENSE online</strong><small>Deterministic mode</small></div></div><button className="logout" onClick={logout}><LogOut size={17}/> Sign out</button></div>
    </aside>
    <main><header className="topbar"><button className="icon-button mobile-only" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu /></button><div><span className="eyebrow">SERVER INTELLIGENCE</span><strong>All systems observed</strong></div><span className={monitoringConnected === false ? "live offline" : "live"} aria-live="polite"><i/> {monitoringConnected == null ? "Checking monitoring" : monitoringConnected ? "Monitoring active" : "Connection interrupted"}</span></header><Outlet /></main>
  </div>
}
