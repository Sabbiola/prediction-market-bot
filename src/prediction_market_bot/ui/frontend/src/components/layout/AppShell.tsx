import { useState, Suspense, lazy, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import { useSSE } from '@/hooks/useSSE'
import { api } from '@/api/client'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import type { TabSection, OverviewTab } from '@/types/api'
import { TAB_SECTIONS as SECTIONS } from '@/types/api'

// Lazy-load tab pages — loaded on demand
const OverviewPage    = lazy(() => import('@/pages/tabs/OverviewPage'))
const ScannerPage     = lazy(() => import('@/pages/tabs/ScannerPage'))
const ResearchPage    = lazy(() => import('@/pages/tabs/ResearchPage'))
const PredictionPage  = lazy(() => import('@/pages/tabs/PredictionPage'))
const RiskPage        = lazy(() => import('@/pages/tabs/RiskPage'))
const ReviewQueuePage = lazy(() => import('@/pages/tabs/ReviewQueuePage'))
const ExecutionPage   = lazy(() => import('@/pages/tabs/ExecutionPage'))
const SandboxTxPage   = lazy(() => import('@/pages/tabs/SandboxTxPage'))
const PositionsPage   = lazy(() => import('@/pages/tabs/PositionsPage'))
const SettlementPage  = lazy(() => import('@/pages/tabs/SettlementPage'))
const ReportsPage     = lazy(() => import('@/pages/tabs/ReportsPage'))
const SystemPage      = lazy(() => import('@/pages/tabs/SystemPage'))
const TraderLivePage  = lazy(() => import('@/pages/tabs/TraderLivePage'))

function TabContent({ section }: { section: TabSection }) {
  switch (section) {
    case 'overview':     return <OverviewPage />
    case 'scanner':      return <ScannerPage />
    case 'research':     return <ResearchPage />
    case 'prediction':   return <PredictionPage />
    case 'risk':         return <RiskPage />
    case 'review-queue': return <ReviewQueuePage />
    case 'execution':    return <ExecutionPage />
    case 'sandbox-tx':   return <SandboxTxPage />
    case 'positions':    return <PositionsPage />
    case 'settlement':   return <SettlementPage />
    case 'reports':      return <ReportsPage />
    case 'system':       return <SystemPage />
    case 'trader-a':     return <TraderLivePage endpoint="/api/trader/live/a" modelLabel="Model A — v4 (current)" />
    case 'trader-b':     return <TraderLivePage endpoint="/api/trader/live/b" modelLabel="Model B — v5 (candidate)" />
  }
}

interface AppShellProps {
  username: string
  role: string
  authEnabled: boolean
}

export default function AppShell({ username, role, authEnabled }: AppShellProps) {
  const [section, setSection] = useState<TabSection>('overview')
  const [mobileOpen, setMobileOpen] = useState(false)
  const { status: sseStatus } = useSSE()

  // Light poll of overview for topbar indicators
  const { data: overview } = useQuery<OverviewTab>({
    queryKey: ['tab', 'overview'],
    queryFn: () => api.get<OverviewTab>('/api/tabs/overview'),
    staleTime: 15_000,
    refetchInterval: 15_000,
  })

  // Keyboard navigation: [ / ] to cycle tabs
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      const idx = SECTIONS.indexOf(section)
      if (e.key === ']') navigate(SECTIONS[(idx + 1) % SECTIONS.length])
      if (e.key === '[') navigate(SECTIONS[(idx - 1 + SECTIONS.length) % SECTIONS.length])
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [section])

  function navigate(s: TabSection) {
    setSection(s)
    setMobileOpen(false)
  }

  return (
    <div className={'app-shell' + (mobileOpen ? ' app-shell--mobile-open' : '')}>
      <Sidebar
        active={section}
        onNavigate={navigate}
        reviewQueueDepth={overview?.review_queue_depth}
        username={username}
        role={role}
      />

      <div className="app-layout">
        <Topbar
          active={section}
          sseStatus={sseStatus}
          runtimeMode={overview?.runtime_mode}
          operatorPaused={overview?.operator_paused}
          username={username}
          authEnabled={authEnabled}
          onMobileMenuToggle={() => setMobileOpen(o => !o)}
        />

        <main id="main-content" className="content-area" role="main" tabIndex={-1}>
          <ErrorBoundary>
            <Suspense fallback={<div className="tab-loading">Loading…</div>}>
              <TabContent section={section} />
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>

      {mobileOpen && (
        <div
          className="sidebar-overlay"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      )}
    </div>
  )
}
