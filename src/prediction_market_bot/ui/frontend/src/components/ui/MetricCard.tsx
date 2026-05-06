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
    <div className={'metric-card metric-card--' + variant}>
      <span className="metric-card__label">{label}</span>
      <span className={'metric-card__value tabular' + (mono ? ' mono' : '') + (valueClass ? ' ' + valueClass : '')}>
        {value}
      </span>
      {sub && <span className="metric-card__sub">{sub}</span>}
    </div>
  )
}
