import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { ResearchTab, ResearchPacketRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import { IncidentBannerList } from '@/components/ui/IncidentList'
import { MiniBar } from '@/components/ui/MiniChart'

const COLUMNS: ColumnDef<ResearchPacketRow, unknown>[] = [
  { accessorKey: 'market_id',         header: 'Market ID',      cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'findings_count',    header: 'Findings',       size: 70 },
  { accessorKey: 'evidence_strength', header: 'Evidence',       cell: i => (Number(i.getValue()) * 100).toFixed(0) + '%', size: 80 },
  { accessorKey: 'disagreement_score', header: 'Disagree',      cell: i => Number(i.getValue()).toFixed(3), size: 80 },
  { accessorKey: 'weighted_sentiment', header: 'Sentiment',     cell: i => Number(i.getValue()).toFixed(3), size: 80 },
  { accessorKey: 'source_types',      header: 'Sources',        cell: i => (i.getValue() as string[]).join(', ') },
  { accessorKey: 'narrative_summary', header: 'Summary',        cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 60)}</span> },
]

export default function ResearchPage() {
  const [filter, setFilter] = useState('')
  const { data, isLoading, error } = useQuery<ResearchTab>({
    queryKey: ['tab', 'research'],
    queryFn: () => api.get<ResearchTab>('/api/tabs/research'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      {d?.anomalies.length ? <IncidentBannerList banners={d.anomalies} /> : null}

      <div className="metrics-row">
        <MetricCard label="Packets"         value={d?.packets_count ?? '—'} />
        <MetricCard label="Findings"        value={d?.findings_count ?? '—'} variant="accent" />
        <MetricCard label="Avg Evidence"    value={d ? (d.avg_evidence_strength * 100).toFixed(0) + '%' : '—'} variant="ok" />
        <MetricCard label="Avg Disagree"    value={d ? d.avg_disagreement_score.toFixed(3) : '—'} />
        <MetricCard label="Source Failures" value={d?.source_failures_count ?? '—'}
          variant={d?.source_failures_count ? 'error' : 'default'} />
        {d?.panel_status && <MetricCard label="Status" value={d.panel_status} />}
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {d?.coverage_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Coverage</div>
            <MiniBar data={d.coverage_summary} color="var(--color-ok)" />
          </div>
        ) : null}
        {d?.source_type_distribution.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Source Types</div>
            <MiniBar data={d.source_type_distribution} color="var(--color-accent)" />
          </div>
        ) : null}
        {d?.diagnostics_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Diagnostics</div>
            <MiniBar data={d.diagnostics_summary} color="var(--color-warn)" />
          </div>
        ) : null}
      </div>

      {!d?.available && d?.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}

      <input className="search-input" placeholder="Filter packets…" value={filter} onChange={e => setFilter(e.target.value)} />

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={d?.packets ?? []} columns={COLUMNS} isLoading={isLoading} globalFilter={filter} />
      </div>
    </div>
  )
}
