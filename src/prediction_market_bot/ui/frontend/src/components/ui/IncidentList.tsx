import type { IncidentBanner, IncidentEvent } from '@/types/api'

const LEVEL_CLASS: Record<string, string> = {
  error:   'incident-banner--error',
  warn:    'incident-banner--warn',
  warning: 'incident-banner--warn',
  info:    'incident-banner--info',
}

export function IncidentBannerList({ banners }: { banners: IncidentBanner[] }) {
  if (!banners.length) return null
  return (
    <div className="incident-banners">
      {banners.map((b, i) => (
        <div key={i} className={'incident-banner ' + (LEVEL_CLASS[b.level] ?? 'incident-banner--info')}>
          <div className="incident-banner__header">
            <span className="incident-banner__code mono">{b.code}</span>
            <span className="incident-banner__title">{b.title}</span>
          </div>
          {b.detail && <p className="incident-banner__detail muted">{b.detail}</p>}
          {b.recommendation && <p className="incident-banner__rec">{b.recommendation}</p>}
        </div>
      ))}
    </div>
  )
}

const SEV_CLASS: Record<string, string> = {
  critical: 'text-error',
  error:    'text-error',
  warn:     'text-warn',
  warning:  'text-warn',
  info:     'text-accent',
}

export function IncidentFeed({ events, maxRows = 50 }: { events: IncidentEvent[], maxRows?: number }) {
  const rows = events.slice(0, maxRows)
  if (!rows.length) return <p className="muted" style={{ fontSize: '11px' }}>No incidents</p>
  return (
    <div className="incident-feed">
      {rows.map((e, i) => (
        <div key={i} className="incident-feed__row">
          <span className={'incident-feed__sev mono ' + (SEV_CLASS[e.severity] ?? '')}>{e.severity.toUpperCase()}</span>
          <span className="incident-feed__ts mono muted">{e.timestamp.slice(11, 19)}</span>
          <span className="incident-feed__summary">{e.summary}</span>
          {e.component && <span className="incident-feed__comp muted">[{e.component}]</span>}
        </div>
      ))}
    </div>
  )
}
