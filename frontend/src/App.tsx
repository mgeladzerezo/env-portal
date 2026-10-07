import { useState } from 'react'

import { useEnvironments, useTemplates } from './api/hooks'
import { EnvironmentDetail } from './components/EnvironmentDetail'
import { EnvironmentList } from './components/EnvironmentList'
import { RequestForm } from './components/RequestForm'

const PANEL = 'rounded-lg border border-slate-200 bg-white p-4'

export function App() {
  const templates = useTemplates()
  const environments = useEnvironments()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const selected = environments.data?.find((environment) => environment.id === selectedId)
  const loadError = templates.error ?? environments.error

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <header className="mb-6">
        <h1 className="text-xl font-semibold">Environment portal</h1>
        <p className="text-sm text-slate-500">
          Short-lived environments from Terraform templates. Everything is destroyed when its TTL
          runs out.
        </p>
      </header>

      {loadError && (
        <p role="alert" className="mb-4 rounded bg-red-50 px-3 py-2 text-sm text-red-800">
          Could not reach the API: {loadError.message}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <section className={PANEL}>
          <h2 className="mb-3 font-semibold">Request an environment</h2>
          {templates.data ? (
            <RequestForm
              templates={templates.data}
              onCreated={(environment) => setSelectedId(environment.id)}
            />
          ) : (
            <p className="text-sm text-slate-500">Loading templates…</p>
          )}
        </section>

        <div className="space-y-4">
          <section className={PANEL}>
            <h2 className="mb-3 font-semibold">Environments</h2>
            <EnvironmentList
              environments={environments.data ?? []}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </section>

          {selected && (
            <section className={PANEL}>
              <EnvironmentDetail key={selected.id} environment={selected} />
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
