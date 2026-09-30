import { useMemo, useState, type ReactNode } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';

export interface Col<T> {
  id: string;
  header: string;
  /** Sortable/comparable value. */
  value: (r: T) => number | string;
  /** Optional display renderer (defaults to the raw value). */
  render?: (r: T) => ReactNode;
  right?: boolean;
}

interface Props<T> {
  columns: Col<T>[];
  rows: T[];
  onRowClick?: (r: T) => void;
  maxHeight?: number;
  initialSort?: { id: string; desc?: boolean };
  emptyText?: string;
}

/** Sortable TanStack Table with a compact dark style. */
export function DataTable<T>({
  columns,
  rows,
  onRowClick,
  maxHeight = 480,
  initialSort,
  emptyText = 'No data',
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>(
    initialSort
      ? [{ id: initialSort.id, desc: initialSort.desc ?? true }]
      : [],
  );

  const table = useMemo(
    () =>
      useReactTable({
        data: rows,
        state: { sorting },
        onSortingChange: setSorting,
        getCoreRowModel: getCoreRowModel(),
        getSortedRowModel: getSortedRowModel(),
        columns: columns.map(
          (c) =>
            ({
              id: c.id,
              header: c.header,
              accessorFn: (r: T) => c.value(r),
              cell: (info: { row: { original: T } }) => (
                <div
                  style={{
                    textAlign: c.right ? 'right' : 'left',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {c.render ? c.render(info.row.original) : String(c.value(info.row.original) ?? '')}
                </div>
              ),
            }) satisfies ColumnDef<T, unknown>,
        ),
      }),
    [columns, rows, sorting],
  );

  return (
    <div
      style={{
        overflow: 'auto',
        maxHeight,
        border: '1px solid #333',
        borderRadius: 6,
      }}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead style={{ position: 'sticky', top: 0, background: '#232323' }}>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  onClick={h.column.getToggleSortingHandler()}
                  style={{
                    padding: '6px 10px',
                    cursor: h.column.getCanSort() ? 'pointer' : 'default',
                    borderBottom: '1px solid #444',
                    textAlign:
                      columns.find((c) => c.id === h.id)?.right ? 'right' : 'left',
                    color: '#ccc',
                    userSelect: 'none',
                  }}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {h.column.getIsSorted() === 'asc' && ' ▲'}
                  {h.column.getIsSorted() === 'desc' && ' ▼'}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 && (
            <tr>
              <td
                colSpan={columns.length}
                style={{ padding: 12, color: '#888', textAlign: 'center' }}
              >
                {emptyText}
              </td>
            </tr>
          )}
          {table.getRowModel().rows.map((row, i) => (
            <tr
              key={row.id}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              style={{
                background: i % 2 === 0 ? '#1d1d1d' : '#212121',
                cursor: onRowClick ? 'pointer' : 'default',
              }}
            >
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  style={{ padding: '5px 10px', borderBottom: '1px solid #2a2a2a' }}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
