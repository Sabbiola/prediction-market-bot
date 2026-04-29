interface SkeletonProps {
  width?: string | number
  height?: string | number
  className?: string
}

export default function Skeleton({ width = '100%', height = '16px', className }: SkeletonProps) {
  return (
    <span
      className={'skeleton' + (className ? ' ' + className : '')}
      style={{ width, height, display: 'block' }}
      aria-hidden="true"
    />
  )
}

export function SkeletonRow({ cols = 4 }: { cols?: number }) {
  return (
    <tr className="skeleton-row">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i}><Skeleton height="12px" /></td>
      ))}
    </tr>
  )
}
