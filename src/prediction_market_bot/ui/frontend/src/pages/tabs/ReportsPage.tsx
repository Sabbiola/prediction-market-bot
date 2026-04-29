import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { ReportsTab } from '@/types/api'
import MetricCard from '@/components/ui/MetricCard'
import { IncidentFeed } from '@/components/ui/IncidentList'
import { MiniBar } from '@/components/ui/MiniChart'

function Shortcut({ label, command, description }: { label: string, command: string, description: string }) {
  return (
    <div className="shortcut-card">
      <div className="shortcut-card__label">{label}</div>
      <code className="shortcut-card__cmd mono">{command}</code>
      <div className="shortcut-card__desc muted">{description}</div>
    </div>
  )
}

export default function ReportsPage() {
  const { data, isLoading, error } = useQuery<ReportsTab>({
    queryKey: ['tab', 'reports'],
    queryFn: () => api.get<ReportsTab>('/api/tabs/reports'),
  })

  if (isLoading) return <div className="tab-loading">Loading…</div>
  if (error) return <div className="tab-placeholder text-error">{(error as Error).message}</div>
  if (!data) return null

  const d = data

  return (
    <div className="tab-section">
      {/* Run summary */}
      <div className="card">
        <div className="card__title">Run Summary — <span className="mono">{d.run_id}</span></div>
        <div className="metrics-row">
          <MetricCard label="Started"    value={d.started_at.slice(0, 16) || '—'} />
          <MetricCard label="Finished"   value={d.finished_at.slice(0, 16) || '—'} />
          <MetricCard label="Markets"    value={d.total_markets} />
          <MetricCard label="Candidates" value={d.candidates} />
          <MetricCard label="Executed"   value={d.executed} variant="accent" />
          <MetricCard label="Settled"    value={d.settled} />
          <MetricCard label="Wins"       value={d.wins} variant="ok" />
          <MetricCard label="Losses"     value={d.losses} variant="error" />
          <MetricCard label="Skipped"    value={d.skipped} />
          <MetricCard label="Events"     value={d.event_count} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {d.artifact_counts.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Artifacts</div>
            <MiniBar data={d.artifact_counts} height={70} />
          </div>
        ) : null}
        {d.stage_timings_ms.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Stage Timings (ms)</div>
            <MiniBar data={d.stage_timings_ms} color="var(--color-accent)" height={70} />
          </div>
        ) : null}
        {d.failure_categories.length ? (
          <div className="card" style={{ flex: '1', minWidth: '180px' }}>
            <div className="card__title">Failure Categories</div>
            <MiniBar data={d.failure_categories} color="var(--color-error)" height={70} />
          </div>
        ) : null}
      </div>

      {/* Report paths */}
      {(d.report_markdown_path || d.report_json_path) && (
        <div className="card">
          <div className="card__title">Report Files</div>
          <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {d.report_markdown_path && <span><span className="muted">MD: </span><span className="mono">{d.report_markdown_path}</span></span>}
            {d.report_json_path && <span><span className="muted">JSON: </span><span className="mono">{d.report_json_path}</span></span>}
          </div>
        </div>
      )}

      {/* Shortcuts */}
      <div className="card">
        <div className="card__title">CLI Shortcuts</div>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <Shortcut {...d.replay_shortcut} />
          <Shortcut {...d.generate_report_shortcut} />
          <Shortcut {...d.eval_run_shortcut} />
          <Shortcut {...d.eval_window_shortcut} />
        </div>
      </div>

      {/* Incidents feed */}
      {d.incidents_feed.length ? (
        <div className="card">
          <div className="card__title">Incidents</div>
          <IncidentFeed events={d.incidents_feed} />
        </div>
      ) : null}

      {!d.available && d.note && <p className="muted" style={{ fontSize: '11px' }}>{d.note}</p>}
    </div>
  )
}
