import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { ScannerTab, ScannerCandidateRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import { IncidentBannerList } from '@/components/ui/IncidentList'
import { MiniBar } from '@/components/ui/MiniChart'

const COLUMNS: ColumnDef<ScannerCandidateRow, unknown>[] = [
  { accessorKey: 'market_id',           header: 'Market ID',  cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'title',               header: 'Title',      cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 50)}</span> },
  { accessorKey: 'category',            header: 'Category',   size: 90 },
  { accessorKey: 'scan_score',          header: 'Score',      cell: i => Number(i.getValue()).toFixed(3), size: 70 },
  { accessorKey: 'liquidity_usd',       header: 'Liquidity',  cell: i => '$' + Number(i.getValue()).toLocaleString(), size: 90 },
  { accessorKey: 'volume_24h_usd',      header: 'Vol 24h',    cell: i => '$' + Number(i.getValue()).toLocaleString(), size: 90 },
  { accessorKey: 'spread_bps',          header: 'Spread bps', size: 80 },
  { accessorKey: 'hours_to_resolution', header: 'Hours',      cell: i => Number(i.getValue()).toFixed(1), size: 60 },
]

export default function ScannerPage() {
  const [filter, setFilter] = useState('')
  const { data, isLoading, error } = useQuery<ScannerTab>({
    queryKey: ['tab', 'scanner'],
    queryFn: () => api.get<ScannerTab>('/api/tabs/scanner'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      {d?.anomalies.length ? <IncidentBannerList banners={d.anomalies} /> : null}

      <div className="metrics-row">
        <MetricCard label="Total Markets"   value={d?.total_markets ?? '—'} />
        <MetricCard label="Eligible"        value={d?.eligible_markets_count ?? '—'} variant="ok" />
        <MetricCard label="Candidates"      value={d?.candidates_count ?? '—'} variant="accent" />
        <MetricCard label="Rejected"        value={d?.rejected_markets_count ?? '—'} />
        <MetricCard label="Avg Scan Score"  value={d ? d.avg_scan_score.toFixed(3) : '—'} />
        {d?.panel_status && <MetricCard label="Status" value={d.panel_status} />}
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {d?.funnel_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Funnel</div>
            <MiniBar data={d.funnel_summary} />
          </div>
        ) : null}
        {d?.top_reasons.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Top Reasons</div>
            <MiniBar data={d.top_reasons} color="var(--color-ok)" />
          </div>
        ) : null}
        {d?.rejected_reasons.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Rejected Reasons</div>
            <MiniBar data={d.rejected_reasons} color="var(--color-error)" />
          </div>
        ) : null}
        {d?.market_context_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Market Context</div>
            <MiniBar data={d.market_context_summary} color="var(--color-warn)" />
          </div>
        ) : null}
      </div>

      {!d?.available && d?.note && (
        <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>
      )}

      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <input
          className="search-input"
          placeholder="Filter candidates…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </div>

      <div className="card" style={{ padding: 0 }}>
        <DataTable
          data={d?.candidates ?? []}
          columns={COLUMNS}
          isLoading={isLoading}
          globalFilter={filter}
        />
      </div>
    </div>
  )
}
