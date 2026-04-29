import { useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { PredictionTab, PredictionRow } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import MetricCard from '@/components/ui/MetricCard'
import Badge from '@/components/ui/Badge'
import { IncidentBannerList } from '@/components/ui/IncidentList'
import { MiniBar } from '@/components/ui/MiniChart'

const COLUMNS: ColumnDef<PredictionRow, unknown>[] = [
  { accessorKey: 'market_id',      header: 'Market ID',  cell: i => <span className="mono">{String(i.getValue()).slice(-12)}</span>, size: 110 },
  { accessorKey: 'selected_side',  header: 'Side',       cell: i => <Badge variant={String(i.getValue()) === 'YES' ? 'ok' : 'error'}>{String(i.getValue())}</Badge>, size: 60 },
  { accessorKey: 'market_yes_prob', header: 'Mkt YES',   cell: i => (Number(i.getValue()) * 100).toFixed(1) + '%', size: 75 },
  { accessorKey: 'fair_yes_prob',  header: 'Fair YES',   cell: i => (Number(i.getValue()) * 100).toFixed(1) + '%', size: 75 },
  { accessorKey: 'edge_bps',       header: 'Edge bps',   cell: i => <span className={Number(i.getValue()) > 0 ? 'text-ok' : 'text-error'}>{Number(i.getValue()).toFixed(0)}</span>, size: 70 },
  { accessorKey: 'confidence',     header: 'Confidence', cell: i => (Number(i.getValue()) * 100).toFixed(1) + '%', size: 80 },
  { accessorKey: 'rationale',      header: 'Rationale',  cell: i => <span title={(i.getValue() as string[]).join(' · ')}>{(i.getValue() as string[]).slice(0, 2).join(' · ').slice(0, 60)}</span> },
]

export default function PredictionPage() {
  const { data, isLoading, error } = useQuery<PredictionTab>({
    queryKey: ['tab', 'prediction'],
    queryFn: () => api.get<PredictionTab>('/api/tabs/prediction'),
  })

  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>

  const d = data

  return (
    <div className="tab-section">
      {d?.anomalies.length ? <IncidentBannerList banners={d.anomalies} /> : null}

      <div className="metrics-row">
        <MetricCard label="Predictions"    value={d?.predictions_count ?? '—'} />
        <MetricCard label="Avg Confidence" value={d ? (d.avg_confidence * 100).toFixed(1) + '%' : '—'} variant="accent" />
        <MetricCard label="Avg Edge"       value={d ? d.avg_edge_bps.toFixed(0) + ' bps' : '—'}
          variant={d && d.avg_edge_bps > 0 ? 'ok' : 'error'} />
        <MetricCard label="Avg Prob Gap"   value={d ? (d.avg_probability_gap * 100).toFixed(1) + '%' : '—'} />
        <MetricCard label="Parity Warns"   value={d?.parity_warning_count ?? '—'}
          variant={d?.parity_warning_count ? 'warn' : 'default'} />
        {d?.panel_status && <MetricCard label="Status" value={d.panel_status} />}
      </div>

      {d?.drift_alert && d.drift_alert.overall_status !== 'ok' && (
        <div className="card">
          <div className="card__title">Drift Alert</div>
          <div style={{ fontSize: '11px' }}>
            {d.drift_alert.warnings.map((w, i) => <p key={i} className="text-warn" style={{ margin: '2px 0' }}>{w}</p>)}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {d?.side_distribution.length ? (
          <div className="card" style={{ flex: '1', minWidth: '160px' }}>
            <div className="card__title">Side Distribution</div>
            <MiniBar data={d.side_distribution} />
          </div>
        ) : null}
        {d?.calibration_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '160px' }}>
            <div className="card__title">Calibration</div>
            <MiniBar data={d.calibration_summary} color="var(--color-ok)" />
          </div>
        ) : null}
        {d?.disagreement_buckets.length ? (
          <div className="card" style={{ flex: '1', minWidth: '160px' }}>
            <div className="card__title">Disagreement</div>
            <MiniBar data={d.disagreement_buckets} color="var(--color-warn)" />
          </div>
        ) : null}
        {d?.approval_rate_summary.length ? (
          <div className="card" style={{ flex: '1', minWidth: '160px' }}>
            <div className="card__title">Approval Rate</div>
            <MiniBar data={d.approval_rate_summary} color="var(--color-accent)" />
          </div>
        ) : null}
      </div>

      {d?.model_visibility && (
        <div className="card">
          <div className="card__title">Model Visibility</div>
          <div style={{ fontSize: '11px', display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
            <span><span className="muted">Engine: </span>{d.model_visibility.effective_engine}</span>
            <span><span className="muted">Version: </span><span className="mono">{d.model_visibility.active_model_version}</span></span>
            <span><span className="muted">Schema: </span><span className="mono">{d.model_visibility.feature_schema_version}</span></span>
            {d.model_visibility.gate_required && (
              <span className="text-warn">Gate required: {d.model_visibility.gate_reason}</span>
            )}
          </div>
        </div>
      )}

      {!d?.available && d?.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}

      <div className="card" style={{ padding: 0 }}>
        <DataTable data={d?.rows ?? []} columns={COLUMNS} isLoading={isLoading} />
      </div>
    </div>
  )
}
