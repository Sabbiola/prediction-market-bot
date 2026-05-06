import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts'

interface Props {
  /** wins / total in [0, 1] */
  rate: number
  total: number
  /** break-even threshold (e.g. 0.40 for HL TP/SL setup); displayed as a marker */
  breakEven?: number
}

function variantColor(rate: number, breakEven: number) {
  if (rate >= breakEven + 0.05) return 'var(--color-ok)'
  if (rate >= breakEven)        return 'var(--color-warn)'
  return 'var(--color-error)'
}

export default function WinRateGauge({ rate, total, breakEven = 0.40 }: Props) {
  const clamped = Math.max(0, Math.min(1, rate))
  const data = [
    { name: 'won', value: clamped },
    { name: 'rest', value: 1 - clamped },
  ]
  const color = variantColor(clamped, breakEven)

  if (total === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 140, color: 'var(--color-muted)' }}>
        <span className="tabular" style={{ fontSize: 26, fontWeight: 700, color: 'var(--color-muted-2)' }}>—</span>
        <span style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.07em' }}>No trades</span>
      </div>
    )
  }

  return (
    <div className="gauge">
      <ResponsiveContainer>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="100%"
            startAngle={180}
            endAngle={0}
            innerRadius="80%"
            outerRadius="100%"
            paddingAngle={0}
            dataKey="value"
            stroke="none"
            isAnimationActive={false}
          >
            <Cell fill={color} />
            <Cell fill="var(--color-border)" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="gauge__center">
        <div className="gauge__value tabular" style={{ color }}>{(clamped * 100).toFixed(1)}%</div>
        <div className="gauge__label">Win rate · {total} trades</div>
        {breakEven && (
          <div className="muted" style={{ fontSize: 9, marginTop: 2 }}>
            break-even {(breakEven * 100).toFixed(0)}%
          </div>
        )}
      </div>
    </div>
  )
}
