import { useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'

import type { Environment } from '../api/client'
import { queryKeys, useDestroy, useRetry, useUnlock } from '../api/hooks'
import { formatOutput } from '../lib/format'
import { useLogStream } from '../lib/logStream'
import { LogViewer } from './LogViewer'
import { StatusBadge } from './StatusBadge'

const BUTTON = 'rounded border px-3 py-1.5 text-sm font-medium disabled:opacity-50'

export function EnvironmentDetail({ environment }: { environment: Environment }) {
  const queryClient = useQueryClient()
  const retry = useRetry()
  const destroy = useDestroy()
  const unlock = useUnlock()

  const refresh = useCallback(
    () => void queryClient.invalidateQueries({ queryKey: queryKeys.environments }),
    [queryClient],
  )
  // A new operation gets a new id, which reopens the log stream the server had ended.
  const streamKey = String(environment.active_operation?.id ?? 'idle')
  const log = useLogStream(environment.id, streamKey, refresh)

  const busy = environment.active_operation !== null
  const canRetry = !busy && ['failed', 'blocked_by_policy'].includes(environment.status)
  const canDestroy = !busy && ['ready', 'failed', 'blocked_by_policy'].includes(environment.status)
  const actionError = retry.error ?? destroy.error ?? unlock.error
  const outputs = Object.entries(environment.outputs)
  const lock = environment.lock_info

  function confirmDestroy() {
    if (window.confirm(`Destroy ${environment.name} and everything in it?`)) {
      destroy.mutate(environment.id)
    }
  }

  function confirmUnlock(lockId: string) {
    const question =
      `Release state lock ${lockId}?\n\n` +
      'Only do this if you are sure no Terraform run is still using this environment.'
    if (window.confirm(question)) unlock.mutate({ id: environment.id, lockId })
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{environment.name}</h2>
          <p className="text-xs text-slate-500">
            {environment.template}@{environment.module_version}, {environment.size}, id{' '}
            {environment.id}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={environment.status} />
          {busy && (
            <span className="text-xs text-slate-500">
              {environment.active_operation?.kind} {environment.active_operation?.status}
            </span>
          )}
          <button
            className={`${BUTTON} border-slate-300`}
            disabled={!canRetry || retry.isPending}
            onClick={() => retry.mutate(environment.id)}
          >
            Retry
          </button>
          <button
            className={`${BUTTON} border-red-300 text-red-700`}
            disabled={!canDestroy || destroy.isPending}
            onClick={confirmDestroy}
          >
            Destroy
          </button>
        </div>
      </header>

      {actionError && (
        <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-800">
          {actionError.message}
        </p>
      )}

      {environment.status === 'failed' && environment.error && (
        <pre className="whitespace-pre-wrap rounded bg-red-50 px-3 py-2 text-xs text-red-900">
          {environment.error}
        </pre>
      )}

      {lock && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <p className="font-medium">The Terraform state is locked</p>
          <p className="mt-1 text-xs">
            Lock {lock.id}
            {lock.who && `, held by ${lock.who}`}
            {lock.created && `, since ${lock.created}`}. The portal does not break locks on its
            own.
          </p>
          <button
            className={`${BUTTON} mt-2 border-amber-400 bg-white`}
            disabled={busy || unlock.isPending}
            onClick={() => confirmUnlock(lock.id)}
          >
            Release lock
          </button>
        </div>
      )}

      {environment.policy_violations.length > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
          <p className="font-medium text-amber-900">Plan blocked by policy</p>
          <ul className="mt-1 list-disc pl-5 text-xs text-amber-900">
            {environment.policy_violations.map((violation) => (
              <li key={`${violation.rule}:${violation.resource}`}>
                <span className="font-mono">{violation.resource}</span>: {violation.message} (
                {violation.rule})
              </li>
            ))}
          </ul>
        </div>
      )}

      {outputs.length > 0 && (
        <section>
          <h3 className="mb-1 text-sm font-medium">Outputs</h3>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
            {outputs.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="font-mono text-slate-500">{key}</dt>
                <dd className="break-all font-mono">{formatOutput(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <LogViewer log={log} />
    </div>
  )
}
