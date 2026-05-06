interface Props {
  /** percentage in [0, 1] */
  value: number
  variant?: 'default' | 'ok' | 'error' | 'warn'
  height?: number
}

export default function ProgressBar({ value, variant = 'default', height = 6 }: Props) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="progress-bar" style={{ height }}>
      <div
        className={
          'progress-bar__fill'
          + (variant !== 'default' ? ' progress-bar__fill--' + variant : '')
        }
        style={{ width: pct + '%' }}
      />
    </div>
  )
}
