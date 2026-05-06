import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts'

export interface EquityPoint {
  /** ISO timestamp or label */
  t: string
  /** running balance in USD */
  balance: number
  /** marker if this point closed a trade */
  pnl?: number
}

interface Props {
  data: EquityPoint[]
  startingBalance: number
  height?: number
}

function fmtUsd(n: number) {
  return '$' + n.toFixed(2)
}

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleString('en-GB', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso }
}

export default function EquityCurve({ data, startingBalance, height = 260 }: Props) {
  if (!data.length) {
    return (
      <div className="tab-placeholder" style={{ height }}>
        Nessun trade chiuso ancora — l'equity curve appare al primo settlement.
      </div>
    )
  }

  const finalBalance = data[data.length - 1]?.balance ?? startingBalance
  const isUp = finalBalance >= startingBalance

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 16, right: 18, left: 6, bottom: 4 }}>
        <defs>
          <linearGradient id="equity-gradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  stopColor={isUp ? 'rgba(52,211,153,.4)' : 'rgba(248,113,113,.4)'} />
            <stop offset="80%" stopColor={isUp ? 'rgba(52,211,153,.0)' : 'rgba(248,113,113,.0)'} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="t"
          tick={{ fontSize: 10, fill: 'var(--color-muted)' }}
          axisLine={{ stroke: 'var(--color-border)' }}
          tickLine={false}
          tickFormatter={fmtTime}
          minTickGap={50}
        />
        <YAxis
          tick={{ fontSize: 10, fill: 'var(--color-muted)' }}
          axisLine={false} tickLine={false}
          tickFormatter={(v) => '$' + Math.round(v)}
          width={50}
          domain={['auto', 'auto']}
        />
        <ReferenceLine
          y={startingBalance}
          stroke="var(--color-muted-2)"
          strokeDasharray="4 4"
          label={{ value: 'start', position: 'insideTopLeft', fill: 'var(--color-muted)', fontSize: 9 }}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--color-card-2)',
            border: '1px solid var(--color-border-2)',
            borderRadius: 8,
            fontSize: 11,
          }}
          itemStyle={{ color: 'var(--color-text)' }}
          labelStyle={{ color: 'var(--color-muted)', fontSize: 10 }}
          labelFormatter={(label) => fmtTime(String(label))}
          formatter={(v) => [fmtUsd(Number(v)), 'Balance']}
        />
        <Area
          type="monotone" dataKey="balance"
          stroke={isUp ? 'var(--color-ok)' : 'var(--color-error)'}
          strokeWidth={2}
          fill="url(#equity-gradient)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
