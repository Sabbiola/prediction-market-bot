import { BarChart, Bar, XAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import type { ChartPoint } from '@/types/api'

interface MiniBarProps {
  data: ChartPoint[]
  height?: number
  color?: string
}

export function MiniBar({ data, height = 80, color = 'var(--color-accent)' }: MiniBarProps) {
  if (!data.length) return <div className="mini-chart-empty muted">—</div>

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
        <XAxis dataKey="label" tick={{ fontSize: 9, fill: 'var(--color-muted)' }} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={{ background: 'var(--color-card)', border: '1px solid var(--color-border)', fontSize: 10, borderRadius: 4 }}
          itemStyle={{ color: 'var(--color-text)' }}
          labelStyle={{ color: 'var(--color-muted)', fontSize: 9 }}
        />
        <Bar dataKey="value" radius={[2, 2, 0, 0]}>
          {data.map((_, i) => <Cell key={i} fill={color} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
