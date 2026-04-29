type BadgeVariant = 'default' | 'ok' | 'warn' | 'error' | 'accent' | 'muted'

interface BadgeProps {
  children: React.ReactNode
  variant?: BadgeVariant
}

export default function Badge({ children, variant = 'default' }: BadgeProps) {
  return <span className={'badge badge--' + variant}>{children}</span>
}

// Convenience: status string → variant
export function statusVariant(status: string): BadgeVariant {
  const s = status.toLowerCase()
  if (['ok', 'approved', 'mined', 'settled', 'win', 'completed'].some(k => s.includes(k))) return 'ok'
  if (['warn', 'pending', 'submitted', 'partial'].some(k => s.includes(k))) return 'warn'
  if (['error', 'failed', 'rejected', 'dropped', 'loss'].some(k => s.includes(k))) return 'error'
  if (['active', 'running', 'live'].some(k => s.includes(k))) return 'accent'
  return 'default'
}
