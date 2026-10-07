import type { Environment } from '../api/client'
import { formatTimeLeft, formatUsd, HOURLY_DIGITS } from '../lib/format'
import { StatusBadge } from './StatusBadge'

interface Props {
  environments: Environment[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function EnvironmentList({ environments, selectedId, onSelect }: Props) {
  if (environments.length === 0) {
    return <p className="text-sm text-slate-500">No environments yet. Request one on the left.</p>
  }
  return (
    <table className="w-full text-left text-sm">
      <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
        <tr>
          <th className="py-2 pr-3 font-medium">Name</th>
          <th className="py-2 pr-3 font-medium">Template</th>
          <th className="py-2 pr-3 font-medium">Status</th>
          <th className="py-2 pr-3 font-medium">Owner</th>
          <th className="py-2 pr-3 font-medium">Expires in</th>
          <th className="py-2 font-medium">Cost / h</th>
        </tr>
      </thead>
      <tbody>
        {environments.map((environment) => (
          <tr
            key={environment.id}
            onClick={() => onSelect(environment.id)}
            aria-selected={environment.id === selectedId}
            className={`cursor-pointer border-b border-slate-100 hover:bg-slate-50 ${
              environment.id === selectedId ? 'bg-slate-100' : ''
            }`}
          >
            <td className="py-2 pr-3 font-medium">{environment.name}</td>
            <td className="py-2 pr-3">
              {environment.template} <span className="text-slate-500">({environment.size})</span>
            </td>
            <td className="py-2 pr-3">
              <StatusBadge status={environment.status} />
            </td>
            <td className="py-2 pr-3 text-slate-600">{environment.owner}</td>
            <td className="py-2 pr-3 text-slate-600">
              {environment.status === 'destroyed' ? '-' : formatTimeLeft(environment.expires_at)}
            </td>
            <td className="py-2 text-slate-600">{formatUsd(environment.hourly_cost_usd, HOURLY_DIGITS)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
