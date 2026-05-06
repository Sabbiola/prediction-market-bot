import { BarChart, Bar, XAxis, YAxis, Cell, ResponsiveContainer, Tooltip } from 'recharts'

interface Props {
  pnls: number[]
  height?: number
  /** Number of histogram bins */
  bins?: number
}

interface Bucket {
  label: string
  count: number
  mid: number
}

function buildHistogram(pnls: number[], nBins: number): Bucket[] {
  if (!pnls.length) return []
  const min = Math.min(...pnls)
  const max = Math.max(...pnls)
  if (min === max) {
    return [{ label: '$' + min.toFixed(2), count: pnls.length, mid: min }]
  }
  const span = max - min
  const step = span / nBins
  const buckets: Bucket[] = []
  for (let i = 0; i < nBins; i++) {
    const lo = min + i * step
    const hi = lo + step
    const mid = (lo + hi) / 2
    buckets.push({ label: '$' + mid.toFixed(2), count: 0, mid })
  }
  for (const p of pnls) {
    let idx = Math.floor((p - min) / step)
    if (idx >= nBins) idx = nBins - 1
    buckets[idx].count += 1
  }
  return buckets
}

export default function PnlDistribution({ pnls, height = 220, bins = 16 }: Props) {
  const data = buildHistogram(pnls, bins)
  if (!data.length) {
    return <div className="tab-placeholder" style={{ height }}>Nessun PnL ancora</div>
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}>
        <XAxis
          dataKey="label"
          tick={{ fontSize: 9, fill: 'var(--color-muted)' }}
          axisLine={{ stroke: 'var(--color-border)' }}
          tickLine={false}
          interval={Math.floor(data.length / 6)}
        />
        <YAxis
          tick={{ fontSize: 9, fill: 'var(--color-muted)' }}
          axisLine={false} tickLine={false} width={28}
          allowDecimals={false}
        />
        <Tooltip
          contentStyle={{ background: 'var(--color-card-2)', border: '1px solid var(--color-border-2)', borderRadius: 8, fontSize: 11 }}
          itemStyle={{ color: 'var(--color-text)' }}
          labelStyle={{ color: 'var(--color-muted)', fontSize: 10 }}
          formatter={(v) => [Number(v) + ' trades', 'Count']}
        />
        <Bar dataKey="count" radius={[3, 3, 0, 0]}>
          {data.map((b, i) => (
            <Cell key={i} fill={b.mid >= 0 ? 'var(--color-ok)' : 'var(--color-error)'} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
