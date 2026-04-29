import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import { SkeletonRow } from './Skeleton'

interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T, unknown>[]
  isLoading?: boolean
  pageSize?: number
  globalFilter?: string
}

export default function DataTable<T>({
  data, columns, isLoading, pageSize = 25, globalFilter,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
  })

  const { pageIndex, pageSize: ps } = table.getState().pagination
  const totalPages = table.getPageCount()

  return (
    <div className="data-table-wrap">
      <div className="data-table-scroll">
        <table className="data-table">
          <thead>
            {table.getHeaderGroups().map(hg => (
              <tr key={hg.id}>
                {hg.headers.map(h => (
                  <th
                    key={h.id}
                    className={h.column.getCanSort() ? 'sortable' : ''}
                    onClick={h.column.getToggleSortingHandler()}
                    style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {h.column.getIsSorted() === 'asc' ? ' ↑' : h.column.getIsSorted() === 'desc' ? ' ↓' : ''}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {isLoading
              ? Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} cols={columns.length} />)
              : table.getRowModel().rows.length === 0
                ? (
                  <tr>
                    <td colSpan={columns.length} className="data-table__empty muted">No data</td>
                  </tr>
                )
                : table.getRowModel().rows.map(row => (
                  <tr key={row.id}>
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="data-table__pagination">
          <button className="btn btn--ghost btn--sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>← Prev</button>
          <span className="muted" style={{ fontSize: '11px' }}>
            {pageIndex + 1} / {totalPages} ({data.length} rows, {ps}/page)
          </span>
          <button className="btn btn--ghost btn--sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>Next →</button>
        </div>
      )}
    </div>
  )
}
