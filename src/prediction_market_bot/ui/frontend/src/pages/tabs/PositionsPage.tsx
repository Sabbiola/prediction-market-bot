import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { PositionsTab, PositionsRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge from '@/components/ui/Badge'
import { MiniBar } from '@/components/ui/MiniChart'

function pnlVariant(v: number) { return v > 0 ? 'ok' : v < 0 ? 'error' : 'default' }

const COLUMNS: ColumnDef<PositionsRow, unknown>[] = [
  { accessorKey: 'market_id',         header: 'Market ID',   cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'market_title',      header: 'Title',       cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 40)}</span> },
  { accessorKey: 'side',              header: 'Side',        size: 50 },
  { accessorKey: 'market_status',     header: 'Status',      cell: i => <Badge variant="default">{String(i.getValue())}</Badge>, size: 80 },
  { accessorKey: 'shares',            header: 'Shares',      cell: i => Number(i.getValue()).toFixed(2), size: 70 },
  { accessorKey: 'avg_entry_price',   header: 'Avg Entry',   cell: i => Number(i.getValue()).toFixed(4), size: 80 },
  { accessorKey: 'mark_price',        header: 'Mark',        cell: i => Number(i.getValue()).toFixed(4), size: 70 },
  { accessorKey: 'cost_basis_usd',    header: 'Cost $',      cell: i => '$' + Number(i.getValue()).toFixed(2), size: 75 },
  { accessorKey: 'market_value_usd',  header: 'Value $',     cell: i => '$' + Number(i.getValue()).toFixed(2), size: 75 },
  { accessorKey: 'unrealized_pnl_usd', header: 'uPnL $',    cell: i => {
    const v = Number(i.getValue())
    return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>${v.toFixed(2)}</span>
  }, size: 80 },
  { accessorKey: 'exposure_pct',      header: 'Exposure %',  cell: i => (Number(i.getValue()) * 100).toFixed(1) + '%', size: 80 },
  { accessorKey: 'hours_to_resolution', header: 'Hours',     cell: i => i.getValue() !== null ? Number(i.getValue()).toFixed(1) : '—', size: 60 },
]

export default function PositionsPage() {
  const { data, isLoading, error } = useQuery<PositionsTab>({
    queryKey: ['tab', 'positions'],
    queryFn: () => api.get<PositionsTab>('/api/tabs/positions'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      <div className="metrics-row">
        <MetricCard label="Open Positions"  value={d?.open_positions_count ?? '—'} />
        <MetricCard label="Total Exposure"  value={d ? '$' + d.total_exposure_usd.toFixed(2) : '—'} variant="accent" />
        <MetricCard label="uPnL"            value={d ? '$' + d.unrealized_pnl_usd.toFixed(2) : '—'}
          variant={d ? pnlVariant(d.unrealized_pnl_usd) : 'default'} />
        <MetricCard label="Realized PnL"    value={d ? '$' + d.realized_pnl_usd.toFixed(2) : '—'}
          variant={d ? pnlVariant(d.realized_pnl_usd) : 'default'} />
        <MetricCard label="Total PnL"       value={d ? '$' + d.total_pnl_usd.toFixed(2) : '—'}
          variant={d ? pnlVariant(d.total_pnl_usd) : 'default'} />
        <MetricCard label="Linked Reviews"  value={d?.linked_review_count ?? '—'} />
        <MetricCard label="Linked TX"       value={d?.linked_tx_count ?? '—'} />
      </div>

      {d?.exposure_distribution.length ? (
        <div className="card">
          <div className="card__title">Exposure Distribution</div>
          <MiniBar data={d.exposure_distribution} color="var(--color-accent)" height={70} />
        </div>
      ) : null}

      {!d?.available && d?.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={d?.rows ?? []} columns={COLUMNS} isLoading={isLoading} />
      </div>
    </div>
  )
}
