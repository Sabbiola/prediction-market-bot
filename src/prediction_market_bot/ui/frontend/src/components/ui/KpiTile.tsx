import { ResponsiveContainer, AreaChart, Area } from 'recharts'

type Variant = 'default' | 'ok' | 'error' | 'warn' | 'accent'

interface KpiTileProps {
  label: string
  value: string | number
  /** delta value below the number (e.g. "+$12.40 today" or "+2.4%") */
  delta?: string
  /** colours the delta line */
  deltaDirection?: 'up' | 'down' | 'flat'
  variant?: Variant
  /** optional sparkline data for the bottom of the tile */
  sparkline?: { value: number }[]
  icon?: React.ReactNode
  hint?: string
}

const VARIANT_GRADIENTS: Record<Variant, [string, string]> = {
  default: ['rgba(129,140,248,.40)', 'rgba(129,140,248,0)'],
  ok:      ['rgba(52,211,153,.45)',  'rgba(52,211,153,0)'],
  error:   ['rgba(248,113,113,.45)', 'rgba(248,113,113,0)'],
  warn:    ['rgba(251,191,36,.45)',  'rgba(251,191,36,0)'],
  accent:  ['rgba(167,139,250,.45)', 'rgba(167,139,250,0)'],
}

const VARIANT_STROKES: Record<Variant, string> = {
  default: 'var(--color-accent)',
  ok:      'var(--color-ok)',
  error:   'var(--color-error)',
  warn:    'var(--color-warn)',
  accent:  'var(--color-accent-2)',
}

export default function KpiTile({
  label, value, delta, deltaDirection, variant = 'default', sparkline, icon, hint,
}: KpiTileProps) {
  const [grad0, grad1] = VARIANT_GRADIENTS[variant]
  const stroke = VARIANT_STROKES[variant]

  return (
    <div className={'kpi-tile kpi-tile--' + variant}>
      <div className="kpi-tile__label">
        {icon}{label}{hint && <span className="muted2" style={{ marginLeft: 4, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>· {hint}</span>}
      </div>
      <div className="kpi-tile__value">{value}</div>
      {delta && (
        <div
          className={
            'kpi-tile__delta'
            + (deltaDirection === 'up'   ? ' kpi-tile__delta--up'   : '')
            + (deltaDirection === 'down' ? ' kpi-tile__delta--down' : '')
          }
        >
          {deltaDirection === 'up' && '▲ '}
          {deltaDirection === 'down' && '▼ '}
          {delta}
        </div>
      )}
      {sparkline && sparkline.length > 1 && (
        <div className="kpi-tile__sparkline">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={sparkline} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`spark-${variant}-${label}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"   stopColor={grad0} />
                  <stop offset="100%" stopColor={grad1} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="value"
                stroke={stroke}
                strokeWidth={1.5}
                fill={`url(#spark-${variant}-${label})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
