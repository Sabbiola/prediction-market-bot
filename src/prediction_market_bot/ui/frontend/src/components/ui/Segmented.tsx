interface Option<T extends string> {
  value: T
  label: string
  badge?: React.ReactNode
}

interface Props<T extends string> {
  value: T
  options: Option<T>[]
  onChange: (next: T) => void
}

export default function Segmented<T extends string>({ value, options, onChange }: Props<T>) {
  return (
    <div className="segmented">
      {options.map(o => (
        <button
          key={o.value}
          className={'segmented__btn' + (o.value === value ? ' segmented__btn--active' : '')}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.badge}
        </button>
      ))}
    </div>
  )
}
