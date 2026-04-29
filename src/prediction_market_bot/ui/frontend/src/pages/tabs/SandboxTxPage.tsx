import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { SandboxTxTab, SandboxTxRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge, { statusVariant } from '@/components/ui/Badge'
import { MiniBar } from '@/components/ui/MiniChart'
import { toast } from '@/components/ui/Toast'

const COLUMNS: ColumnDef<SandboxTxRow, unknown>[] = [
  { accessorKey: 'intent_id',            header: 'Intent ID',    cell: i => <span className="mono">{String(i.getValue()).slice(-10)}</span>, size: 95 },
  { accessorKey: 'market_id',            header: 'Market',       cell: i => <span className="mono">{String(i.getValue()).slice(-10)}</span>, size: 95 },
  { accessorKey: 'side',                 header: 'Side',         size: 50 },
  { accessorKey: 'confirmation_status',  header: 'Confirmation', cell: i => <Badge variant={statusVariant(String(i.getValue()))}>{String(i.getValue())}</Badge>, size: 90 },
  { accessorKey: 'tx_hash',             header: 'TX Hash',      cell: i => <span className="mono">{String(i.getValue()).slice(0, 12) || '—'}</span>, size: 90 },
  { accessorKey: 'nonce',               header: 'Nonce',        size: 60 },
  { accessorKey: 'retry_count',         header: 'Retries',      size: 60 },
  { accessorKey: 'execution_mode',      header: 'Mode',         size: 100 },
  { accessorKey: 'message',             header: 'Message',      cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 50)}</span> },
]

export default function SandboxTxPage() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery<SandboxTxTab>({
    queryKey: ['tab', 'sandbox-tx'],
    queryFn: () => api.get<SandboxTxTab>('/api/tabs/sandbox-tx'),
  })

  const reconcile = useMutation({
    mutationFn: () => api.post('/api/actions/tx-reconcile', {}),
    onSuccess: () => { toast('Reconcile triggered', 'ok'); qc.invalidateQueries({ queryKey: ['tab', 'sandbox-tx'] }) },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  const resubmit = useMutation({
    mutationFn: (intentId: string) => api.post('/api/actions/tx-resubmit-safe', { intent_id: intentId, confirm: true }),
    onSuccess: () => { toast('Resubmit triggered', 'ok'); qc.invalidateQueries({ queryKey: ['tab', 'sandbox-tx'] }) },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      <div className="metrics-row">
        <MetricCard label="Attempts"   value={d?.attempts_count ?? '—'} />
        <MetricCard label="Receipts"   value={d?.receipts_count ?? '—'} variant="ok" />
        <MetricCard label="Pending"    value={d?.pending_count ?? '—'} variant={d?.pending_count ? 'warn' : 'default'} />
        <MetricCard label="Mined"      value={d?.mined_count ?? '—'} variant="ok" />
        <MetricCard label="Failed"     value={d?.failed_count ?? '—'} variant={d?.failed_count ? 'error' : 'default'} />
        <MetricCard label="Dropped"    value={d?.dropped_count ?? '—'} />
        <MetricCard label="Replaced"   value={d?.replaced_count ?? '—'} />
        <MetricCard label="Backlog"    value={d?.reconcile_backlog_count ?? '—'} variant={d?.reconcile_backlog_count ? 'warn' : 'default'} />
      </div>

      {d?.action_hint && (
        <div className="card">
          <div className="card__title">Hint</div>
          <p style={{ fontSize: '11px', margin: 0 }}>{d.action_hint}</p>
          <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
            {d.can_reconcile && (
              <button className="btn btn--ghost btn--sm" disabled={reconcile.isPending} onClick={() => reconcile.mutate()}>
                Reconcile
              </button>
            )}
            {d.can_resubmit_safe && d.active_intent_id && (
              <button
                className="btn btn--ghost btn--sm"
                disabled={resubmit.isPending}
                onClick={() => {
                  if (!window.confirm('Resubmit safe TX for intent ' + d.active_intent_id + '?')) return
                  resubmit.mutate(d.active_intent_id)
                }}
              >
                Resubmit Safe
              </button>
            )}
          </div>
        </div>
      )}

      {d?.confirmation_distribution.length ? (
        <div className="card">
          <div className="card__title">Confirmation Status</div>
          <MiniBar data={d.confirmation_distribution} height={70} />
        </div>
      ) : null}

      {!d?.available && d?.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={d?.rows ?? []} columns={COLUMNS} isLoading={isLoading} />
      </div>
    </div>
  )
}
