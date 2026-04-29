import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { ReviewQueueTab, ReviewQueueRow, ReviewQueueDetail } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge, { statusVariant } from '@/components/ui/Badge'
import { toast } from '@/components/ui/Toast'

const COLUMNS: ColumnDef<ReviewQueueRow, unknown>[] = [
  { accessorKey: 'queue_id',     header: 'ID',       cell: i => <span className="mono">{String(i.getValue()).slice(-8)}</span>, size: 80 },
  { accessorKey: 'market_title', header: 'Market',   cell: i => { const v = String(i.getValue() || (i.row.original as ReviewQueueRow).market_id); return <span title={v}>{v.slice(0, 40)}</span> } },
  { accessorKey: 'side',         header: 'Side',     size: 50 },
  { accessorKey: 'status',       header: 'Status',   cell: i => <Badge variant={statusVariant(String(i.getValue()))}>{String(i.getValue())}</Badge>, size: 80 },
  { accessorKey: 'stake_usd',    header: 'Stake $',  cell: i => '$' + Number(i.getValue()).toFixed(2), size: 80 },
  { accessorKey: 'confidence',   header: 'Conf',     cell: i => (Number(i.getValue()) * 100).toFixed(1) + '%', size: 60 },
  { accessorKey: 'edge_bps',     header: 'Edge bps', cell: i => Number(i.getValue()).toFixed(0), size: 70 },
  { accessorKey: 'expires_at',   header: 'Expires',  cell: i => String(i.getValue() ?? '').slice(11, 16) || '—', size: 60 },
]

function DetailPanel({ item, onClose }: { item: ReviewQueueDetail, onClose: () => void }) {
  const qc = useQueryClient()
  const [rationale, setRationale] = useState('')

  const approve = useMutation({
    mutationFn: () => api.post('/api/actions/review-approve', {
      queue_id: item.queue_id,
      operator_id: 'ui',
      rationale: rationale || 'Approved via UI',
      confirm: true,
    }),
    onSuccess: () => {
      toast('Approved', 'ok')
      qc.invalidateQueries({ queryKey: ['tab', 'review-queue'] })
      onClose()
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  const reject = useMutation({
    mutationFn: () => api.post('/api/actions/review-reject', {
      queue_id: item.queue_id,
      operator_id: 'ui',
      rationale: rationale || 'Rejected via UI',
      confirm: true,
    }),
    onSuccess: () => {
      toast('Rejected', 'warn')
      qc.invalidateQueries({ queryKey: ['tab', 'review-queue'] })
      onClose()
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  return (
    <div className="review-detail">
      <div className="review-detail__header">
        <span className="review-detail__title">{item.market_title || item.market_id}</span>
        <button className="btn btn--ghost btn--sm" onClick={onClose}>✕ Close</button>
      </div>

      <div className="metrics-row">
        <MetricCard label="Side"       value={item.side} />
        <MetricCard label="Stake"      value={'$' + item.stake_usd.toFixed(2)} variant="accent" />
        <MetricCard label="Confidence" value={(item.confidence * 100).toFixed(1) + '%'} />
        <MetricCard label="Edge"       value={item.edge_bps.toFixed(0) + ' bps'} variant={item.edge_bps > 0 ? 'ok' : 'error'} />
        {item.fair_yes_prob !== null && <MetricCard label="Fair YES" value={(item.fair_yes_prob * 100).toFixed(1) + '%'} />}
        {item.market_yes_prob !== null && <MetricCard label="Mkt YES" value={(item.market_yes_prob * 100).toFixed(1) + '%'} />}
        <MetricCard label="Evidence"   value={item.evidence_strength !== null ? (item.evidence_strength * 100).toFixed(0) + '%' : '—'} />
        <MetricCard label="Findings"   value={item.findings_count} />
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {item.prediction_rationale.length > 0 && (
          <div className="card" style={{ flex: '1', minWidth: '200px' }}>
            <div className="card__title">Prediction Rationale</div>
            <ul style={{ margin: 0, padding: '0 0 0 14px', fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
              {item.prediction_rationale.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        )}
        {item.risk_rationale.length > 0 && (
          <div className="card" style={{ flex: '1', minWidth: '200px' }}>
            <div className="card__title">Risk Rationale</div>
            <ul style={{ margin: 0, padding: '0 0 0 14px', fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
              {item.risk_rationale.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        )}
      </div>

      {item.evidence_coverage_summary && (
        <div className="card">
          <div className="card__title">Evidence Coverage</div>
          <p style={{ fontSize: '11px', margin: 0 }}>{item.evidence_coverage_summary}</p>
        </div>
      )}

      {(item.can_approve || item.can_reject) && (
        <div className="card">
          <div className="card__title">Decision</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <textarea
              className="review-rationale-input"
              placeholder="Rationale (optional)"
              value={rationale}
              onChange={e => setRationale(e.target.value)}
              rows={2}
            />
            <div style={{ display: 'flex', gap: '8px' }}>
              {item.can_approve && (
                <button
                  className="btn btn--approve"
                  disabled={approve.isPending || reject.isPending}
                  onClick={() => {
                    if (!window.confirm('Approve this trade?')) return
                    approve.mutate()
                  }}
                >
                  ✓ Approve
                </button>
              )}
              {item.can_reject && (
                <button
                  className="btn btn--reject"
                  disabled={approve.isPending || reject.isPending}
                  onClick={() => {
                    if (!window.confirm('Reject this trade?')) return
                    reject.mutate()
                  }}
                >
                  ✕ Reject
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ReviewQueuePage() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('pending')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery<ReviewQueueTab>({
    queryKey: ['tab', 'review-queue', statusFilter, selectedId],
    queryFn: () => {
      const params = new URLSearchParams({ status: statusFilter })
      if (selectedId) params.set('queue_id', selectedId)
      return api.get<ReviewQueueTab>('/api/tabs/review-queue?' + params)
    },
    refetchInterval: selectedId ? false : 15_000,
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  function closeDetail() {
    setSelectedId(null)
    qc.invalidateQueries({ queryKey: ['tab', 'review-queue'] })
  }

  return (
    <div className="tab-section">
      <div className="metrics-row">
        <MetricCard label="Pending"  value={d?.pending_count ?? '—'} variant={d?.pending_count ? 'warn' : 'default'} />
        <MetricCard label="Approved" value={d?.approved_count ?? '—'} variant="ok" />
        <MetricCard label="Executed" value={d?.executed_count ?? '—'} variant="accent" />
        <MetricCard label="Rejected" value={d?.rejected_count ?? '—'} />
        <MetricCard label="Expired"  value={d?.expired_count ?? '—'} />
        <MetricCard label="Total"    value={d?.total_count ?? '—'} />
      </div>

      {/* Status filter tabs */}
      <div className="filter-tabs">
        {(d?.status_filters ?? ['pending', 'approved', 'rejected', 'executed', 'expired']).map(f => (
          <button
            key={f}
            className={'filter-tab' + (statusFilter === f ? ' filter-tab--active' : '')}
            onClick={() => { setStatusFilter(f); setSelectedId(null) }}
          >
            {f}
          </button>
        ))}
      </div>

      {d?.selected_item && selectedId ? (
        <DetailPanel item={d.selected_item} onClose={closeDetail} />
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <DataTable
            data={d?.rows ?? []}
            columns={COLUMNS}
            isLoading={isLoading}
            pageSize={20}
          />
        </div>
      )}

      {!selectedId && d?.rows && d.rows.length > 0 && (
        <p className="muted" style={{ fontSize: '11px', textAlign: 'center' }}>
          Click a row in the queue table (or use the API with queue_id param) to view details
        </p>
      )}

      {/* Selectable rows via a separate click handler wrapper would go here in a future polish pass */}
    </div>
  )
}
