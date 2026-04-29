import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { OverviewTab } from '@/types/api'
import MetricCard from '@/components/ui/MetricCard'
import { IncidentBannerList, IncidentFeed } from '@/components/ui/IncidentList'
import { MiniBar } from '@/components/ui/MiniChart'
import Skeleton from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

function fmt(n: number | undefined) { return n !== undefined ? String(n) : '—' }
function fmtMode(s: string) { return s.replace(/_/g, ' ') }

export default function OverviewPage() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery<OverviewTab>({
    queryKey: ['tab', 'overview'],
    queryFn: () => api.get<OverviewTab>('/api/tabs/overview'),
  })

  const runOnce = useMutation({
    mutationFn: () => api.post('/api/actions/run-once', {}),
    onSuccess: () => { toast('Run-once triggered', 'ok'); qc.invalidateQueries({ queryKey: ['tab', 'overview'] }) },
    onError: (e: Error) => toast(e.message, 'error'),
  })
  const pause = useMutation({
    mutationFn: () => api.post('/api/actions/pause', { reason: 'ui_operator_pause' }),
    onSuccess: () => { toast('Scheduler paused', 'warn'); qc.invalidateQueries({ queryKey: ['tab', 'overview'] }) },
    onError: (e: Error) => toast(e.message, 'error'),
  })
  const resume = useMutation({
    mutationFn: () => api.post('/api/actions/resume', {}),
    onSuccess: () => { toast('Scheduler resumed', 'ok'); qc.invalidateQueries({ queryKey: ['tab', 'overview'] }) },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  if (error) return <div className="tab-placeholder text-error">Failed to load: {(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      {d?.incident_banners.length ? <IncidentBannerList banners={d.incident_banners} /> : null}

      <div className="metrics-row">
        {isLoading ? (
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="metric-card"><Skeleton height="40px" /></div>
          ))
        ) : (
          <>
            <MetricCard label="Mode"            value={fmtMode(d?.runtime_mode ?? '')} />
            <MetricCard label="Review Queue"    value={fmt(d?.review_queue_depth)}
              variant={d?.review_queue_depth ? 'warn' : 'default'} />
            <MetricCard label="Open Positions"  value={fmt(d?.open_positions_count)} />
            <MetricCard label="Pending Settle"  value={fmt(d?.pending_settlements_count)} />
            <MetricCard label="TX Pending"      value={fmt(d?.tx_pending_count)}
              variant={d?.tx_pending_count ? 'warn' : 'default'} />
            <MetricCard label="Source Failures" value={fmt(d?.live_source_failures_total)}
              variant={d?.live_source_failures_total ? 'error' : 'default'} />
          </>
        )}
      </div>

      {d?.last_run?.available && (
        <div className="card">
          <div className="card__title">Last Run</div>
          <div className="metrics-row">
            <MetricCard label="Run ID"     value={d.last_run.run_id.slice(-8)} mono />
            <MetricCard label="Status"     value={d.last_run.status}
              variant={d.last_run.status === 'completed' ? 'ok' : d.last_run.status === 'failed' ? 'error' : 'warn'} />
            <MetricCard label="Markets"    value={d.last_run.total_markets} />
            <MetricCard label="Candidates" value={d.last_run.candidates} />
            <MetricCard label="Executed"   value={d.last_run.executed} variant="accent" />
            <MetricCard label="Settled"    value={d.last_run.settled} />
            <MetricCard label="Wins"       value={d.last_run.wins} variant="ok" />
            <MetricCard label="Losses"     value={d.last_run.losses} variant="error" />
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        {d?.counters_chart?.length ? (
          <div className="card" style={{ flex: '1', minWidth: '200px' }}>
            <div className="card__title">Counters</div>
            <MiniBar data={d.counters_chart} />
          </div>
        ) : null}

        {d?.model_visibility && (
          <div className="card" style={{ flex: '1', minWidth: '200px' }}>
            <div className="card__title">Model</div>
            <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span><span className="muted">Engine: </span>{d.model_visibility.effective_engine}</span>
              <span><span className="muted">Version: </span><span className="mono">{d.model_visibility.active_model_version}</span></span>
              <span><span className="muted">Calibration: </span>{d.model_visibility.calibration_method}</span>
              {d.model_visibility.rollback_active && (
                <span className="text-warn">⚠ Rollback: {d.model_visibility.rollback_reason}</span>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card__title">Operator Actions</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button
            className="btn btn--ghost"
            disabled={runOnce.isPending}
            onClick={() => {
              if (!window.confirm('Trigger a run-once pipeline cycle now?')) return
              runOnce.mutate()
            }}
          >
            Run Once
          </button>
          {d?.operator_paused ? (
            <button className="btn btn--ghost" disabled={resume.isPending} onClick={() => resume.mutate()}>
              Resume Scheduler
            </button>
          ) : (
            <button
              className="btn btn--ghost"
              disabled={pause.isPending}
              onClick={() => {
                if (!window.confirm('Pause the scheduler? No new pipeline cycles will start until resumed.')) return
                pause.mutate()
              }}
            >
              Pause Scheduler
            </button>
          )}
        </div>
        {d?.operator_paused && d.operator_pause_reason && (
          <p className="muted" style={{ fontSize: '11px', marginTop: '8px' }}>
            Paused: {d.operator_pause_reason}
          </p>
        )}
      </div>

      {d?.drift_alert && d.drift_alert.overall_status !== 'ok' && (
        <div className="card">
          <div className="card__title">Drift Alert — {d.drift_alert.overall_status.toUpperCase()}</div>
          <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {d.drift_alert.warnings.map((w, i) => <span key={i} className="text-warn">{w}</span>)}
            {d.drift_alert.signals.map((s, i) => (
              <div key={i} style={{ display: 'flex', gap: '8px' }}>
                <span className="muted">{s.name}</span>
                <span className={s.status === 'ok' ? 'text-ok' : 'text-warn'}>{s.status}</span>
                <span>{s.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {d?.incidents_feed?.length ? (
        <div className="card">
          <div className="card__title">Recent Incidents</div>
          <IncidentFeed events={d.incidents_feed} />
        </div>
      ) : null}
    </div>
  )
}
