import { useState } from 'react'
import { useLogout } from '@/hooks/useAuth'
import type { SseStatus } from '@/hooks/useSSE'
import type { TabSection } from '@/types/api'

const TAB_LABELS: Record<TabSection, string> = {
  overview: 'Overview',
  scanner: 'Scanner',
  research: 'Research',
  prediction: 'Prediction',
  risk: 'Risk',
  'review-queue': 'Review Queue',
  execution: 'Execution',
  'sandbox-tx': 'Sandbox TX',
  positions: 'Positions',
  settlement: 'Settlement',
  reports: 'Reports',
  system: 'System Health',
  'trader': 'Trader Dashboard',
}

interface TopbarProps {
  active: TabSection
  sseStatus: SseStatus
  runtimeMode?: string
  operatorPaused?: boolean
  username?: string
  authEnabled?: boolean
  onMobileMenuToggle: () => void
}

export default function Topbar({
  active,
  sseStatus,
  runtimeMode,
  operatorPaused,
  username,
  authEnabled,
  onMobileMenuToggle,
}: TopbarProps) {
  const logout = useLogout()
  const [autoRefresh, setAutoRefresh] = useState(() => {
    try { return localStorage.getItem('pmbot-auto-refresh') === '1' } catch { return false }
  })

  function toggleAutoRefresh() {
    setAutoRefresh(prev => {
      const next = !prev
      try { localStorage.setItem('pmbot-auto-refresh', next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }

  const dotClass = sseStatus === 'live'
    ? 'pulse-dot pulse-dot--live'
    : sseStatus === 'error'
      ? 'pulse-dot pulse-dot--error'
      : 'pulse-dot pulse-dot--idle'

  return (
    <header className="topbar" role="banner">
      <button
        className="topbar__menu-btn"
        onClick={onMobileMenuToggle}
        aria-controls="sidebar"
        aria-label="Open navigation"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <line x1="2" y1="4"  x2="14" y2="4"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          <line x1="2" y1="8"  x2="14" y2="8"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          <line x1="2" y1="12" x2="14" y2="12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        </svg>
      </button>

      <span className="topbar__breadcrumb">{TAB_LABELS[active]}</span>

      <div className="topbar__status-row">
        <span className={dotClass} title={`SSE: ${sseStatus}`} aria-hidden="true" />
        {runtimeMode && <span className="status-chip" id="global-mode">{runtimeMode}</span>}
        {operatorPaused && <span className="status-chip status-chip--warn">PAUSED</span>}
      </div>

      <div className="topbar__auth">
        <button
          className={'btn btn--ghost btn--sm' + (autoRefresh ? ' btn--active' : '')}
          onClick={toggleAutoRefresh}
          title={autoRefresh ? 'Auto-refresh ON (15s) — click to disable' : 'Auto-refresh OFF — click to enable'}
          aria-pressed={autoRefresh}
        >
          ↺ Auto
        </button>
        {authEnabled && username && (
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            Logout
          </button>
        )}
      </div>
    </header>
  )
}
