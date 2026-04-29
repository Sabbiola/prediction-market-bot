import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { TraderLiveResponse, TraderLivePosition } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge from '@/components/ui/Badge'

const STARTING_BALANCE = 500

const MODELS = [
  { key: 'a' as const, label: 'Model A — v4', endpoint: '/api/trader/live/a' },
  { key: 'b' as const, label: 'Model B — v5', endpoint: '/api/trader/live/b' },
]

function pnlVariant(v: number): 'ok' | 'error' | 'default' {
  return v > 0 ? 'ok' : v < 0 ? 'error' : 'default'
}

function fmtSecs(secs: number | null): string {
  if (secs === null) return '—'
  if (secs < 0) return 'expired'
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}m ${s}s`
}

const COLUMNS: ColumnDef<TraderLivePosition, unknown>[] = [
  {
    accessorKey: 'title',
    header: 'Market',
    cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 42)}</span>,
  },
  {
    accessorKey: 'side', header: 'Side', size: 50,
    cell: i => <Badge variant={String(i.getValue()) === 'YES' ? 'ok' : 'error'}>{String(i.getValue())}</Badge>,
  },
  { accessorKey: 'shares', header: 'Shares', size: 70, cell: i => Number(i.getValue()).toFixed(2) },
  { accessorKey: 'entry_price', header: 'Entry', size: 65, cell: i => Number(i.getValue()).toFixed(4) },
  { accessorKey: 'side_mark_price', header: 'Mark', size: 65, cell: i => i.getValue() !== null ? Number(i.getValue()).toFixed(4) : '—' },
  { accessorKey: 'cost_basis_usd', header: 'Cost $', size: 65, cell: i => '$' + Number(i.getValue()).toFixed(2) },
  {
    accessorKey: 'unrealized_pnl_usd', header: 'uPnL $', size: 72,
    cell: i => {
      const v = Number(i.getValue())
      return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>${v.toFixed(2)}</span>
    },
  },
  { accessorKey: 'seconds_to_close', header: 'Closes In', size: 80, cell: i => fmtSecs(i.getValue() as number | null) },
  {
    accessorKey: 'slot_anchor_btc', header: 'BTC Anchor', size: 90,
    cell: i => i.getValue() !== null ? '$' + Number(i.getValue()).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '—',
  },
  {
    accessorKey: 'btc_delta_usd', header: 'BTC Delta', size: 80,
    cell: i => {
      if (i.getValue() === null) return '—'
      const v = Number(i.getValue())
      return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>${v.toFixed(0)}</span>
    },
  },
  {
    accessorKey: 'currently_winning', header: 'Winning?', size: 72,
    cell: i => {
      const v = i.getValue()
      if (v === null) return <span className="muted">—</span>
      return v ? <Badge variant="ok">YES</Badge> : <Badge variant="error">NO</Badge>
    },
  },
]

function ModelPanel({ endpoint }: { endpoint: string }) {
  const { data, isLoading, error } = useQuery<TraderLiveResponse>({
    queryKey: ['trader-live', endpoint],
    queryFn: () => api.get<TraderLiveResponse>(endpoint),
    refetchInterval: 4_000,
    staleTime: 3_000,
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const balance = data !== undefined ? STARTING_BALANCE + data.realized_pnl_usd : null
  const balanceDelta = balance !== null ? balance - STARTING_BALANCE : null
  const balancePct = balanceDelta !== null ? (balanceDelta / STARTING_BALANCE * 100).toFixed(2) : null

  return (
    <>
      <div className="card" style={{ marginBottom: '10px', padding: '7px 12px', display: 'flex', alignItems: 'center', gap: '16px' }}>
        {data?.btc_spot_usd != null && (
          <span className="muted" style={{ fontSize: '12px' }}>
            BTC ${data.btc_spot_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}
          </span>
        )}
        {data?.as_of && (
          <span className="muted" style={{ fontSize: '11px' }}>
            updated {new Date(data.as_of).toLocaleTimeString()}
          </span>
        )}
        {data?.open_count === 0 && data.settled_count >= 0 && (
          <span className="muted" style={{ fontSize: '11px' }}>Waiting for next slot...</span>
        )}
      </div>

      <div className="metrics-row" style={{ marginBottom: '10px' }}>
        <MetricCard label="Open" value={data?.open_count ?? '—'} />
        <MetricCard label="Settled" value={data?.settled_count ?? '—'} />
        <MetricCard
          label="Balance"
          value={balance !== null ? '$' + balance.toFixed(2) : '—'}
          variant={balanceDelta !== null ? pnlVariant(balanceDelta) : 'default'}
        />
        <MetricCard
          label="Realized PnL"
          value={data !== undefined ? '$' + data.realized_pnl_usd.toFixed(2) : '—'}
          variant={data !== undefined ? pnlVariant(data.realized_pnl_usd) : 'default'}
        />
        <MetricCard
          label="uPnL"
          value={data !== undefined ? '$' + data.unrealized_pnl_usd.toFixed(2) : '—'}
          variant={data !== undefined ? pnlVariant(data.unrealized_pnl_usd) : 'default'}
        />
        <MetricCard
          label="Exposure"
          value={data !== undefined ? '$' + data.total_exposure_usd.toFixed(2) : '—'}
          variant="accent"
        />
      </div>

      {balancePct !== null && (
        <div style={{ marginBottom: '10px', fontSize: '12px' }} className="muted">
          from ${STARTING_BALANCE.toFixed(2)} &bull; {Number(balancePct) >= 0 ? '+' : ''}{balancePct}%
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={data?.positions ?? []} columns={COLUMNS} isLoading={isLoading} />
      </div>
    </>
  )
}

export default function TraderDashboardPage() {
  const [active, setActive] = useState<'a' | 'b'>('a')
  const model = MODELS.find(m => m.key === active)!

  return (
    <div className="tab-section">
      <div style={{ display: 'flex', gap: '6px', marginBottom: '14px' }}>
        {MODELS.map(m => (
          <button
            key={m.key}
            className={'btn btn--sm' + (active === m.key ? ' btn--active' : ' btn--ghost')}
            onClick={() => setActive(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <ModelPanel endpoint={model.endpoint} />
    </div>
  )
}
