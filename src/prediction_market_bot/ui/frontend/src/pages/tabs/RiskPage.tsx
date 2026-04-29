import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { RiskTab, RiskRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge from '@/components/ui/Badge'
import { IncidentBannerList } from '@/components/ui/IncidentList'
import { MiniBar } from '@/components/ui/MiniChart'

const COLUMNS: ColumnDef<RiskRow, unknown>[] = [
  { accessorKey: 'market_id',        header: 'Market ID',   cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'approved',         header: 'Approved',    cell: i => <Badge variant={i.getValue() ? 'ok' : 'error'}>{i.getValue() ? 'YES' : 'NO'}</Badge>, size: 75 },
  { accessorKey: 'side',             header: 'Side',        size: 50 },
  { accessorKey: 'stake_usd',        header: 'Stake $',     cell: i => '$' + Number(i.getValue()).toFixed(2), size: 80 },
  { accessorKey: 'bankroll_fraction', header: 'Bankroll %', cell: i => (Number(i.getValue()) * 100).toFixed(2) + '%', size: 80 },
  { accessorKey: 'fractional_kelly', header: 'f-Kelly',     cell: i => Number(i.getValue()).toFixed(4), size: 75 },
  { accessorKey: 'reasoning',        header: 'Reasoning',   cell: i => <span title={(i.getValue() as string[]).join(' · ')}>{(i.getValue() as string[]).slice(0, 2).join(' · ').slice(0, 60)}</span> },
]

export default function RiskPage() {
  const { data, isLoading, error } = useQuery<RiskTab>({
    queryKey: ['tab', 'risk'],
    queryFn: () => api.get<RiskTab>('/api/tabs/risk'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      {d?.anomalies.length ? <IncidentBannerList banners={d.anomalies} /> : null}

      {(d?.daily_stop_triggered || d?.circuit_breaker_active) && (
        <div className="incident-banner incident-banner--error">
          <div className="incident-banner__header">
            <span className="incident-banner__title">
              {d?.daily_stop_triggered ? '🛑 Daily stop triggered' : ''}
              {d?.circuit_breaker_active ? '🛑 Circuit breaker active' : ''}
            </span>
          </div>
        </div>
      )}

      <div className="metrics-row">
        <MetricCard label="Decisions"   value={d?.decisions_count ?? '—'} />
        <MetricCard label="Approved"    value={d?.approved_count ?? '—'} variant="ok" />
        <MetricCard label="Blocked"     value={d?.blocked_count ?? '—'}
          variant={d?.blocked_count ? 'warn' : 'default'} />
        <MetricCard label="Avg Stake"   value={d ? '$' + d.avg_stake_usd.toFixed(2) : '—'} />
        <MetricCard label="Proposed $"  value={d ? '$' + d.proposed_stake_usd.toFixed(2) : '—'} />
        <MetricCard label="Approved $"  value={d ? '$' + d.approved_stake_usd.toFixed(2) : '—'} variant="accent" />
        <MetricCard label="Portfolio $" value={d ? '$' + d.avg_portfolio_exposure_usd.toFixed(2) : '—'} />
        {d?.panel_status && <MetricCard label="Status" value={d.panel_status} />}
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {d?.guardrail_distribution.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Guardrails</div>
            <MiniBar data={d.guardrail_distribution} color="var(--color-warn)" />
          </div>
        ) : null}
        {d?.reason_code_distribution.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Reason Codes</div>
            <MiniBar data={d.reason_code_distribution} color="var(--color-error)" />
          </div>
        ) : null}
        {d?.diagnostics_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Diagnostics</div>
            <MiniBar data={d.diagnostics_summary} color="var(--color-accent)" />
          </div>
        ) : null}
      </div>

      {!d?.available && d?.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={d?.rows ?? []} columns={COLUMNS} isLoading={isLoading} />
      </div>
    </div>
  )
}
