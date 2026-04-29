import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { TraderLiveResponse, TraderLivePosition } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge from '@/components/ui/Badge'

interface Props {
  endpoint: string
  modelLabel: string
}

function pnlVariant(v: number) {
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
    cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 45)}</span>,
  },
  { accessorKey: 'side', header: 'Side', size: 50,
    cell: i => <Badge variant={String(i.getValue()) === 'YES' ? 'ok' : 'error'}>{String(i.getValue())}</Badge>,
  },
  { accessorKey: 'shares',         header: 'Shares',     cell: i => Number(i.getValue()).toFixed(2), size: 70 },
  { accessorKey: 'entry_price',    header: 'Entry',      cell: i => Number(i.getValue()).toFixed(4), size: 70 },
  { accessorKey: 'side_mark_price', header: 'Mark',      cell: i => i.getValue() !== null ? Number(i.getValue()).toFixed(4) : '—', size: 70 },
  { accessorKey: 'cost_basis_usd', header: 'Cost $',     cell: i => '$' + Number(i.getValue()).toFixed(2), size: 70 },
  { accessorKey: 'unrealized_pnl_usd', header: 'uPnL $', size: 75,
    cell: i => {
      const v = Number(i.getValue())
      return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>${v.toFixed(2)}</span>
    },
  },
  { accessorKey: 'seconds_to_close', header: 'Closes In', size: 80,
    cell: i => fmtSecs(i.getValue() as number | null),
  },
  { accessorKey: 'slot_anchor_btc', header: 'BTC Anchor', size: 90,
    cell: i => i.getValue() !== null ? '$' + Number(i.getValue()).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '—',
  },
  { accessorKey: 'btc_delta_usd', header: 'BTC Delta', size: 80,
    cell: i => {
      if (i.getValue() === null) return '—'
      const v = Number(i.getValue())
      return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>${v.toFixed(0)}</span>
    },
  },
  { accessorKey: 'currently_winning', header: 'Winning?', size: 75,
    cell: i => {
      const v = i.getValue()
      if (v === null) return <span className="muted">—</span>
      return v
        ? <Badge variant="ok">YES</Badge>
        : <Badge variant="error">NO</Badge>
    },
  },
]

export default function TraderLivePage({ endpoint, modelLabel }: Props) {
  const { data, isLoading, error } = useQuery<TraderLiveResponse>({
    queryKey: ['trader-live', endpoint],
    queryFn: () => api.get<TraderLiveResponse>(endpoint),
    refetchInterval: 4_000,
    staleTime: 3_000,
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      <div className="card" style={{ marginBottom: '12px', padding: '8px 12px' }}>
        <span style={{ fontWeight: 600, fontSize: '13px' }}>{modelLabel}</span>
        {d?.btc_spot_usd !== undefined && d.btc_spot_usd !== null && (
          <span className="muted" style={{ marginLeft: '12px', fontSize: '12px' }}>
            BTC ${d.btc_spot_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}
          </span>
        )}
        {d?.as_of && (
          <span className="muted" style={{ marginLeft: '12px', fontSize: '11px' }}>
            as of {new Date(d.as_of).toLocaleTimeString()}
          </span>
        )}
      </div>

      <div className="metrics-row">
        <MetricCard label="Open Positions"  value={d?.open_count ?? '—'} />
        <MetricCard label="Settled"         value={d?.settled_count ?? '—'} />
        <MetricCard label="Exposure"        value={d ? '$' + d.total_exposure_usd.toFixed(2) : '—'} variant="accent" />
        <MetricCard label="uPnL"            value={d ? '$' + d.unrealized_pnl_usd.toFixed(2) : '—'}
          variant={d ? pnlVariant(d.unrealized_pnl_usd) : 'default'} />
        <MetricCard label="Realized PnL"    value={d ? '$' + d.realized_pnl_usd.toFixed(2) : '—'}
          variant={d ? pnlVariant(d.realized_pnl_usd) : 'default'} />
      </div>

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={d?.positions ?? []} columns={COLUMNS} isLoading={isLoading} />
      </div>
    </div>
  )
}
