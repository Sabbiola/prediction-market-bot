import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { SystemHealthTab } from '@/types/api'
import MetricCard from '@/components/ui/MetricCard'
import Badge, { statusVariant } from '@/components/ui/Badge'
import { IncidentBannerList, IncidentFeed } from '@/components/ui/IncidentList'

export default function SystemPage() {
  const { data, isLoading, error } = useQuery<SystemHealthTab>({
    queryKey: ['tab', 'system'],
    queryFn: () => api.get<SystemHealthTab>('/api/tabs/system'),
  })

  if (isLoading) return <div className="tab-loading">Loading…</div>
  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>
  if (!data) return null

  const d = data

  return (
    <div className="tab-section">
      {d.incident_banners.length ? <IncidentBannerList banners={d.incident_banners} /> : null}

      <div className="metrics-row">
        <MetricCard label="Overall"        value={d.overall_status.toUpperCase()}
          variant={d.overall_status === 'ok' ? 'ok' : d.overall_status === 'degraded' ? 'warn' : 'error'} />
        <MetricCard label="Runtime Mode"   value={d.healthcheck.runtime_mode} />
        <MetricCard label="Execution Mode" value={d.healthcheck.execution_mode} />
        <MetricCard label="Review Queue"   value={d.review_queue_depth}
          variant={d.review_queue_depth ? 'warn' : 'default'} />
        <MetricCard label="TX Pending"     value={d.tx_pending_count}
          variant={d.tx_pending_count ? 'warn' : 'default'} />
        <MetricCard label="TX Mined"       value={d.tx_mined_count} variant="ok" />
        <MetricCard label="TX Failed"      value={d.tx_failed_count}
          variant={d.tx_failed_count ? 'error' : 'default'} />
        <MetricCard label="Pending Settle" value={d.pending_settlements_count} />
        <MetricCard label="Stale Events"   value={d.stale_data_events_total}
          variant={d.stale_data_events_total ? 'warn' : 'default'} />
      </div>

      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <div className="card" style={{ flex: '1', minWidth: '260px' }}>
          <div className="card__title">Startup Validation</div>
          <div style={{ fontSize: '11px', marginBottom: '10px' }}>
            <Badge variant={d.startup_validation.ok ? 'ok' : 'error'}>
              {d.startup_validation.ok ? 'PASS' : 'FAIL'}
            </Badge>
            <span className="muted" style={{ marginLeft: '8px' }}>
              {d.startup_validation.error_count} errors · {d.startup_validation.warning_count} warnings
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            {d.startup_validation.checks.map((c, i) => (
              <div key={i} style={{ display: 'flex', gap: '6px', fontSize: '11px', alignItems: 'baseline' }}>
                <Badge variant={c.ok ? 'ok' : c.severity === 'error' ? 'error' : 'warn'}>
                  {c.ok ? 'OK' : c.severity.toUpperCase()}
                </Badge>
                <span>{c.name}</span>
                {c.detail && <span className="muted" style={{ fontSize: '10px' }}>{c.detail}</span>}
              </div>
            ))}
          </div>
        </div>

        <div className="card" style={{ flex: '1', minWidth: '260px' }}>
          <div className="card__title">Providers</div>
          <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '5px' }}>
            {[
              ['Market data', d.provider_status.market_data_provider],
              ['Research', d.provider_status.research_provider],
              ['Failure policy', d.provider_status.provider_failure_policy],
            ].map(([label, val]) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span className="muted">{label}:</span><span>{val}</span>
              </div>
            ))}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="muted">Sandbox submit:</span>
              <Badge variant={d.provider_status.sandbox_submit_tx ? 'ok' : 'muted'}>
                {d.provider_status.sandbox_submit_tx ? 'ON' : 'OFF'}
              </Badge>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="muted">Source failures:</span>
              <span className={d.provider_status.live_source_failures_total ? 'text-error' : ''}>
                {d.provider_status.live_source_failures_total}
              </span>
            </div>
          </div>
        </div>

        <div className="card" style={{ flex: '1', minWidth: '200px' }}>
          <div className="card__title">Database</div>
          <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <Badge variant={statusVariant(d.db_connectivity.status)}>{d.db_connectivity.status.toUpperCase()}</Badge>
              <span className="muted">{d.db_connectivity.check_name}</span>
            </div>
            {d.db_connectivity.detail && (
              <span className="muted" style={{ fontSize: '10px' }}>{d.db_connectivity.detail}</span>
            )}
          </div>
        </div>
      </div>

      {d.incidents_feed.length ? (
        <div className="card">
          <div className="card__title">Incidents</div>
          <IncidentFeed events={d.incidents_feed} />
        </div>
      ) : null}
    </div>
  )
}
