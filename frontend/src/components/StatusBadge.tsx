import type { EnvironmentStatus } from '../api/client'
import { STATUS_LABELS } from '../lib/format'

const STYLES: Record<EnvironmentStatus, string> = {
  requested: 'bg-slate-100 text-slate-700',
  planning: 'bg-blue-100 text-blue-800',
  applying: 'bg-blue-100 text-blue-800',
  ready: 'bg-emerald-100 text-emerald-800',
  blocked_by_policy: 'bg-amber-100 text-amber-800',
  failed: 'bg-red-100 text-red-800',
  destroying: 'bg-slate-200 text-slate-700',
  destroyed: 'bg-slate-100 text-slate-400',
}

const IN_PROGRESS: EnvironmentStatus[] = ['planning', 'applying', 'destroying']

export function StatusBadge({ status }: { status: EnvironmentStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}
    >
      {IN_PROGRESS.includes(status) && (
        <span aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
      )}
      {STATUS_LABELS[status]}
    </span>
  )
}
