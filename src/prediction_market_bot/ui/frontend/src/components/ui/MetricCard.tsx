interface MetricCardProps {
  label: string
  value: string | number
  sub?: string
  variant?: 'default' | 'ok' | 'warn' | 'error' | 'accent'
  mono?: boolean
}

export default function MetricCard({ label, value, sub, variant = 'default', mono }: MetricCardProps) {
  const valueClass = {
    default: '',
    ok:      'text-ok',
    warn:    'text-warn',
    error:   'text-error',
    accent:  'text-accent',
  }[variant]

  return (
    <div className="metric-card">
      <span className="metric-card__label muted">{label}</span>
      <span className={'metric-card__value' + (mono ? ' mono' : '') + (valueClass ? ' ' + valueClass : '')}>
        {value}
      </span>
      {sub && <span className="metric-card__sub muted">{sub}</span>}
    </div>
  )
}
