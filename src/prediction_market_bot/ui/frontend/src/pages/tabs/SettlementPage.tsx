import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { SettlementTab, SettlementRow, SettlementPendingRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge, { statusVariant } from '@/components/ui/Badge'
import { MiniBar } from '@/components/ui/MiniChart'

const PENDING_COLS: ColumnDef<SettlementPendingRow, unknown>[] = [
  { accessorKey: 'market_id',         header: 'Market ID',    cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'state',             header: 'State',        cell: i => <Badge variant={statusVariant(String(i.getValue()))}>{String(i.getValue())}</Badge>, size: 80 },
  { accessorKey: 'resolution_status', header: 'Resolution',   size: 100 },
  { accessorKey: 'execution_side',    header: 'Side',         size: 50 },
  { accessorKey: 'stake_usd',         header: 'Stake $',      cell: i => '$' + Number(i.getValue()).toFixed(2), size: 75 },
  { accessorKey: 'fill_price',        header: 'Fill',         cell: i => i.getValue() !== null ? Number(i.getValue()).toFixed(4) : '—', size: 70 },
  { accessorKey: 'retry_count',       header: 'Retries',      size: 60 },
  { accessorKey: 'resolution_reason', header: 'Reason',       cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 40)}</span> },
]

const RESOLVED_COLS: ColumnDef<SettlementRow, unknown>[] = [
  { accessorKey: 'market_id',             header: 'Market ID',    cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'outcome_classification', header: 'Outcome',     cell: i => <Badge variant={statusVariant(String(i.getValue()))}>{String(i.getValue())}</Badge>, size: 80 },
  { accessorKey: 'resolved_yes',          header: 'Resolved YES', size: 80 },
  { accessorKey: 'execution_side',        header: 'Side',         size: 50 },
  { accessorKey: 'pnl_usd',              header: 'PnL $',        cell: i => {
    const v = Number(i.getValue())
    return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>${v.toFixed(2)}</span>
  }, size: 80 },
  { accessorKey: 'resolution_reason',    header: 'Reason',       cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 40)}</span> },
  { accessorKey: 'retry_count',          header: 'Retries',      size: 60 },
  { accessorKey: 'updated_at',           header: 'Updated',      cell: i => String(i.getValue()).slice(0, 16), size: 110 },
]

export default function SettlementPage() {
  const [view, setView] = useState<'pending' | 'resolved'>('pending')
  const { data, isLoading, error } = useQuery<SettlementTab>({
    queryKey: ['tab', 'settlement'],
    queryFn: () => api.get<SettlementTab>('/api/tabs/settlement'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      <div className="metrics-row">
        <MetricCard label="Pending"     value={d?.pending_requests_count ?? '—'}
          variant={d?.pending_requests_count ? 'warn' : 'default'} />
        <MetricCard label="Settled"     value={d?.settled_count ?? '—'} variant="ok" />
        <MetricCard label="Resolved"    value={d?.resolved_settlements_count ?? '—'} />
        <MetricCard label="Realized PnL" value={d ? '$' + d.realized_pnl_usd.toFixed(2) : '—'}
          variant={d ? (d.realized_pnl_usd > 0 ? 'ok' : d.realized_pnl_usd < 0 ? 'error' : 'default') : 'default'} />
        <MetricCard label="Retries"     value={d?.retry_count ?? '—'}
          variant={d?.retry_count ? 'warn' : 'default'} />
        <MetricCard label="Failures"    value={d?.failure_count ?? '—'}
          variant={d?.failure_count ? 'error' : 'default'} />
      </div>

      {d?.outcome_distribution.length ? (
        <div className="card">
          <div className="card__title">Outcome Distribution</div>
          <MiniBar data={d.outcome_distribution} height={70} />
        </div>
      ) : null}

      {!d?.available && d?.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}

      <div className="filter-tabs">
        <button className={'filter-tab' + (view === 'pending' ? ' filter-tab--active' : '')} onClick={() => setView('pending')}>
          Pending ({d?.pending_requests_count ?? 0})
        </button>
        <button className={'filter-tab' + (view === 'resolved' ? ' filter-tab--active' : '')} onClick={() => setView('resolved')}>
          Resolved ({d?.resolved_settlements_count ?? 0})
        </button>
      </div>

      <div className="card" style={{ padding: 0 }}>
        {view === 'pending'
          ? <DataTable data={d?.pending_rows ?? []} columns={PENDING_COLS} isLoading={isLoading} />
          : <DataTable data={d?.resolved_rows ?? []} columns={RESOLVED_COLS} isLoading={isLoading} />}
      </div>
    </div>
  )
}
