import { useEffect, useState } from 'react'
import type { TabSection } from '@/types/api'

interface NavItem {
  section: TabSection
  label: string
  tooltip: string
  badge?: boolean
  icon: React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  {
    section: 'overview', label: 'Overview', tooltip: 'Overview',
    icon: <svg viewBox="0 0 18 18" fill="none"><rect x="2" y="2" width="6" height="6" rx="1.5" fill="currentColor" opacity=".9"/><rect x="10" y="2" width="6" height="6" rx="1.5" fill="currentColor" opacity=".6"/><rect x="2" y="10" width="6" height="6" rx="1.5" fill="currentColor" opacity=".6"/><rect x="10" y="10" width="6" height="6" rx="1.5" fill="currentColor" opacity=".4"/></svg>,
  },
  {
    section: 'scanner', label: 'Scanner', tooltip: 'Scanner Engine',
    icon: <svg viewBox="0 0 18 18" fill="none"><circle cx="8" cy="8" r="5" stroke="currentColor" strokeWidth="1.5"/><line x1="12" y1="12" x2="16" y2="16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  },
  {
    section: 'research', label: 'Research', tooltip: 'Research Engine',
    icon: <svg viewBox="0 0 18 18" fill="none"><path d="M3 5h12M3 9h8M3 13h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  },
  {
    section: 'prediction', label: 'Prediction', tooltip: 'Prediction Engine',
    icon: <svg viewBox="0 0 18 18" fill="none"><polyline points="2,14 6,8 9,11 13,5 16,7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/></svg>,
  },
  {
    section: 'risk', label: 'Risk', tooltip: 'Risk Engine',
    icon: <svg viewBox="0 0 18 18" fill="none"><path d="M9 2L16 15H2L9 2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><line x1="9" y1="8" x2="9" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><circle cx="9" cy="13" r=".8" fill="currentColor"/></svg>,
  },
  {
    section: 'review-queue', label: 'Review Queue', tooltip: 'Review Queue', badge: true,
    icon: <svg viewBox="0 0 18 18" fill="none"><rect x="2" y="2" width="14" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.5"/><path d="M6 9l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  },
  {
    section: 'execution', label: 'Execution', tooltip: 'Execution Lane',
    icon: <svg viewBox="0 0 18 18" fill="none"><polygon points="4,3 14,9 4,15" fill="currentColor" opacity=".85"/></svg>,
  },
  {
    section: 'sandbox-tx', label: 'Sandbox TX', tooltip: 'Sandbox TX',
    icon: <svg viewBox="0 0 18 18" fill="none"><rect x="2" y="5" width="14" height="10" rx="2" stroke="currentColor" strokeWidth="1.5"/><path d="M6 5V4a3 3 0 016 0v1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  },
  {
    section: 'positions', label: 'Positions', tooltip: 'Open Positions',
    icon: <svg viewBox="0 0 18 18" fill="none"><rect x="2" y="10" width="3" height="6" rx="1" fill="currentColor" opacity=".9"/><rect x="7" y="7" width="3" height="9" rx="1" fill="currentColor" opacity=".7"/><rect x="12" y="4" width="3" height="12" rx="1" fill="currentColor" opacity=".5"/></svg>,
  },
  {
    section: 'settlement', label: 'Settlement', tooltip: 'Settlement Lane',
    icon: <svg viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7" stroke="currentColor" strokeWidth="1.5"/><path d="M6 9h6M9 6v6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  },
  {
    section: 'reports', label: 'Reports', tooltip: 'Reports & Replay',
    icon: <svg viewBox="0 0 18 18" fill="none"><path d="M4 2h7l4 4v10a1 1 0 01-1 1H4a1 1 0 01-1-1V3a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><path d="M11 2v5h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/><line x1="5" y1="10" x2="9" y2="10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/><line x1="5" y1="13" x2="11" y2="13" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>,
  },
  {
    section: 'system', label: 'System', tooltip: 'System Health',
    icon: <svg viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7" stroke="currentColor" strokeWidth="1.5"/><path d="M2 9h2l2-4 3 8 2-6 1.5 2H16" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  },
  {
    section: 'trader', label: 'Trader', tooltip: 'Trader Dashboard — Model A vs B',
    icon: <svg viewBox="0 0 18 18" fill="none"><polyline points="2,14 5,9 8,11 11,5 14,8 16,6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/><circle cx="16" cy="6" r="1.5" fill="currentColor"/><circle cx="11" cy="5" r="1.2" fill="var(--color-ok, #22c55e)" opacity=".9"/></svg>,
  },
]

const CORE_SECTIONS: TabSection[] = ['overview']
const INTEL_SECTIONS: TabSection[] = ['scanner', 'research', 'prediction', 'risk']
const OPS_SECTIONS: TabSection[] = ['review-queue', 'execution', 'sandbox-tx', 'positions', 'settlement']
const REPORT_SECTIONS: TabSection[] = ['reports', 'system']
const TRADER_SECTIONS: TabSection[] = ['trader']

interface SidebarProps {
  active: TabSection
  onNavigate: (section: TabSection) => void
  reviewQueueDepth?: number
  username?: string
  role?: string
}

function NavGroup({ label, items, active, onNavigate, collapsed, reviewQueueDepth }: {
  label: string
  items: NavItem[]
  active: TabSection
  onNavigate: (s: TabSection) => void
  collapsed: boolean
  reviewQueueDepth?: number
}) {
  return (
    <>
      {!collapsed && <span className="sidebar__section-label">{label}</span>}
      {items.map(item => (
        <button
          key={item.section}
          className={'sidebar__nav-item' + (active === item.section ? ' sidebar__nav-item--active' : '')}
          data-section={item.section}
          data-tooltip={item.tooltip}
          aria-current={active === item.section ? 'page' : 'false'}
          onClick={() => onNavigate(item.section)}
        >
          <span className="sidebar__icon">{item.icon}</span>
          {!collapsed && <span className="sidebar__nav-label">{item.label}</span>}
          {item.badge && reviewQueueDepth && reviewQueueDepth > 0
            ? <span className="sidebar__badge">{reviewQueueDepth}</span>
            : null}
        </button>
      ))}
    </>
  )
}

export default function Sidebar({ active, onNavigate, reviewQueueDepth, username, role }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('pmbot-sidebar-collapsed') === '1' } catch { return false }
  })

  useEffect(() => {
    try { localStorage.setItem('pmbot-sidebar-collapsed', collapsed ? '1' : '0') } catch { /* ignore */ }
  }, [collapsed])

  const navItems = NAV_ITEMS

  return (
    <aside className={'sidebar' + (collapsed ? ' sidebar--collapsed' : '')} id="sidebar" aria-label="Navigation">
      <div className="sidebar__brand">
        <div className="sidebar__logo" aria-hidden="true">
          <svg viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="3"  cy="9"  r="2" fill="white" opacity=".9"/>
            <circle cx="9"  cy="3"  r="2" fill="white" opacity=".9"/>
            <circle cx="9"  cy="15" r="2" fill="white" opacity=".9"/>
            <circle cx="15" cy="9"  r="2" fill="white" opacity=".9"/>
            <circle cx="9"  cy="9"  r="1.5" fill="white"/>
            <line x1="5"  y1="9"  x2="7.5" y2="9"   stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="10.5" y1="9" x2="13" y2="9"   stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="9"  y1="5"  x2="9"   y2="7.5" stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="9"  y1="10.5" x2="9" y2="13"  stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="4.4" y1="7.4" x2="7.5" y2="8" stroke="white" strokeWidth=".6" opacity=".35"/>
            <line x1="4.4" y1="10.6" x2="7.5" y2="10" stroke="white" strokeWidth=".6" opacity=".35"/>
            <line x1="13.6" y1="7.4" x2="10.5" y2="8" stroke="white" strokeWidth=".6" opacity=".35"/>
            <line x1="13.6" y1="10.6" x2="10.5" y2="10" stroke="white" strokeWidth=".6" opacity=".35"/>
          </svg>
        </div>
        {!collapsed && (
          <div className="sidebar__brand-text">
            <span className="sidebar__title">PMBot</span>
            <span className="sidebar__subtitle">Control Plane</span>
          </div>
        )}
        <button
          className="sidebar__collapse-btn"
          onClick={() => setCollapsed(c => !c)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
            style={{ transform: collapsed ? 'rotate(180deg)' : undefined }}>
            <path d="M7.5 2L3.5 6L7.5 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      </div>

      <nav className="sidebar__nav" role="navigation" aria-label="Main navigation">
        <NavGroup label="Core" items={navItems.filter(i => CORE_SECTIONS.includes(i.section))}
          active={active} onNavigate={onNavigate} collapsed={collapsed} reviewQueueDepth={reviewQueueDepth} />
        <NavGroup label="Intelligence" items={navItems.filter(i => INTEL_SECTIONS.includes(i.section))}
          active={active} onNavigate={onNavigate} collapsed={collapsed} reviewQueueDepth={reviewQueueDepth} />
        <NavGroup label="Operations" items={navItems.filter(i => OPS_SECTIONS.includes(i.section))}
          active={active} onNavigate={onNavigate} collapsed={collapsed} reviewQueueDepth={reviewQueueDepth} />
        <NavGroup label="Reports & System" items={navItems.filter(i => REPORT_SECTIONS.includes(i.section))}
          active={active} onNavigate={onNavigate} collapsed={collapsed} reviewQueueDepth={reviewQueueDepth} />
        <NavGroup label="Live Traders" items={navItems.filter(i => TRADER_SECTIONS.includes(i.section))}
          active={active} onNavigate={onNavigate} collapsed={collapsed} reviewQueueDepth={reviewQueueDepth} />
      </nav>

      <div className="sidebar__footer">
        {!collapsed && username && (
          <div className="sidebar__user">
            <span className="sidebar__user-name">{username}</span>
            <span className={'pill pill--' + (role?.toLowerCase() ?? 'viewer')}>{role}</span>
          </div>
        )}
      </div>
    </aside>
  )
}
