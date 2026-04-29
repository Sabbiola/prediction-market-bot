import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { ExecutionTab, ExecutionRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge, { statusVariant } from '@/components/ui/Badge'
import { MiniBar } from '@/components/ui/MiniChart'

function link(url: string, label: string) {
  if (!url) return <span className="muted">—</span>
  return <a href={url} className="table-link">{label}</a>
}

const COLUMNS: ColumnDef<ExecutionRow, unknown>[] = [
  { accessorKey: 'market_id',      header: 'Market ID',   cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'status',         header: 'Status',      cell: i => <Badge variant={statusVariant(String(i.getValue()))}>{String(i.getValue())}</Badge>, size: 80 },
  { accessorKey: 'side',           header: 'Side',        size: 50 },
  { accessorKey: 'stake_usd',      header: 'Stake $',     cell: i => '$' + Number(i.getValue()).toFixed(2), size: 80 },
  { accessorKey: 'fill_price',     header: 'Fill',        cell: i => i.getValue() !== null ? Number(i.getValue()).toFixed(4) : '—', size: 70 },
  { accessorKey: 'execution_mode', header: 'Mode',        size: 100 },
  { accessorKey: 'lifecycle_path', header: 'Lifecycle',   size: 80 },
  { accessorKey: 'message',        header: 'Message',     cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 50)}</span> },
  {
    id: 'links', header: 'Links', size: 90,
    cell: i => (
      <span style={{ display: 'flex', gap: '6px' }}>
        {link((i.row.original as ExecutionRow).review_queue_url, 'RQ')}
        {link((i.row.original as ExecutionRow).sandbox_tx_url, 'TX')}
        {link((i.row.original as ExecutionRow).position_url, 'Pos')}
      </span>
    ),
  },
]

export default function ExecutionPage() {
  const { data, isLoading, error } = useQuery<ExecutionTab>({
    queryKey: ['tab', 'execution'],
    queryFn: () => api.get<ExecutionTab>('/api/tabs/execution'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      <div className="metrics-row">
        <MetricCard label="Intents"    value={d?.intents_count ?? '—'} />
        <MetricCard label="Executions" value={d?.executions_count ?? '—'} variant="accent" />
        <MetricCard label="Submitted"  value={d?.submitted_count ?? '—'} />
        <MetricCard label="Completed"  value={d?.completed_count ?? '—'} variant="ok" />
        <MetricCard label="Failed"     value={d?.failed_count ?? '—'}
          variant={d?.failed_count ? 'error' : 'default'} />
        <MetricCard label="Skipped"    value={d?.skipped_count ?? '—'} />
        <MetricCard label="RQ Links"   value={d?.linked_review_decisions_count ?? '—'} />
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {d?.status_distribution.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Status Distribution</div>
            <MiniBar data={d.status_distribution} />
          </div>
        ) : null}
        {d?.lifecycle_distribution.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Lifecycle</div>
            <MiniBar data={d.lifecycle_distribution} color="var(--color-ok)" />
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
