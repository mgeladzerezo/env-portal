import { type FormEvent, useState } from 'react'

import type { Environment, EnvironmentRequest, Estimate, Template } from '../api/client'
import { useCreateEnvironment, useEstimate } from '../api/hooks'
import { formatUsd, HOURLY_DIGITS } from '../lib/format'

const NAME_PATTERN = '[a-z][a-z0-9\\-]{1,22}[a-z0-9]'

interface Props {
  templates: Template[]
  onCreated: (environment: Environment) => void
}

// An estimate together with the request it was computed for and the key that makes
// confirming it idempotent.
interface Quote {
  estimate: Estimate
  request: EnvironmentRequest
  idempotencyKey: string
}

export function RequestForm({ templates, onCreated }: Props) {
  const [name, setName] = useState('')
  const [templateName, setTemplateName] = useState(templates[0]?.name ?? '')
  const [size, setSize] = useState('small')
  const [ttlHours, setTtlHours] = useState(templates[0]?.default_ttl_hours ?? 8)
  const [quote, setQuote] = useState<Quote | null>(null)
  const estimate = useEstimate()
  const create = useCreateEnvironment()

  const template = templates.find((candidate) => candidate.name === templateName)
  const error = estimate.error ?? create.error

  // Any edit invalidates the quote, so what gets confirmed is always what was priced.
  function edit<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value)
      setQuote(null)
      estimate.reset()
      create.reset()
    }
  }

  function requestEstimate(event: FormEvent) {
    event.preventDefault()
    const request = { name, template: templateName, size, ttl_hours: ttlHours }
    estimate.mutate(request, {
      onSuccess: (result) =>
        setQuote({ estimate: result, request, idempotencyKey: crypto.randomUUID() }),
    })
  }

  function confirm() {
    if (!quote) return
    create.mutate(
      { body: quote.request, idempotencyKey: quote.idempotencyKey },
      {
        onSuccess: (environment) => {
          setQuote(null)
          setName('')
          onCreated(environment)
        },
      },
    )
  }

  return (
    <form onSubmit={requestEstimate} className="space-y-4">
      <label className="block text-sm">
        <span className="font-medium">Name</span>
        <input
          required
          value={name}
          pattern={NAME_PATTERN}
          title="3-24 characters: lowercase letters, digits and dashes"
          onChange={(event) => edit(setName)(event.target.value)}
          placeholder="checkout-pr-42"
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5"
        />
      </label>

      <label className="block text-sm">
        <span className="font-medium">Template</span>
        <select
          value={templateName}
          onChange={(event) => edit(setTemplateName)(event.target.value)}
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5"
        >
          {templates.map((option) => (
            <option key={option.name} value={option.name}>
              {option.title}
            </option>
          ))}
        </select>
        {template && <span className="mt-1 block text-slate-500">{template.description}</span>}
      </label>

      <fieldset className="text-sm">
        <legend className="font-medium">Size</legend>
        <div className="mt-1 space-y-1">
          {template?.sizes.map((option) => (
            <label key={option.name} className="flex items-baseline gap-2">
              <input
                type="radio"
                name="size"
                value={option.name}
                checked={size === option.name}
                onChange={() => edit(setSize)(option.name)}
              />
              <span className="w-16 capitalize">{option.name}</span>
              <span className="text-slate-500">{option.description}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="block text-sm">
        <span className="font-medium">Time to live (hours)</span>
        <input
          type="number"
          required
          min={1}
          max={template?.max_ttl_hours}
          value={ttlHours}
          onChange={(event) => edit(setTtlHours)(Number(event.target.value))}
          className="mt-1 w-28 rounded border border-slate-300 px-2 py-1.5"
        />
        <span className="ml-2 text-slate-500">
          destroyed automatically afterwards (max {template?.max_ttl_hours}h)
        </span>
      </label>

      {error && (
        <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-800">
          {error.message}
        </p>
      )}

      {quote ? (
        <div className="space-y-3 rounded border border-slate-200 bg-slate-50 p-3 text-sm">
          <p className="font-medium">Estimated cost</p>
          <dl className="grid grid-cols-2 gap-y-1">
            <dt className="text-slate-500">Per hour</dt>
            <dd>{formatUsd(quote.estimate.hourly_usd, HOURLY_DIGITS)}</dd>
            <dt className="text-slate-500">For {quote.estimate.ttl_hours} hours</dt>
            <dd className="font-semibold">{formatUsd(quote.estimate.total_usd)}</dd>
            <dt className="text-slate-500">Source</dt>
            <dd>{quote.estimate.source}</dd>
          </dl>
          <button
            type="button"
            onClick={confirm}
            disabled={create.isPending}
            className="rounded bg-slate-900 px-3 py-1.5 font-medium text-white disabled:opacity-50"
          >
            {create.isPending ? 'Creating…' : 'Confirm and create'}
          </button>
        </div>
      ) : (
        <button
          type="submit"
          disabled={estimate.isPending || !template}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {estimate.isPending ? 'Estimating…' : 'Estimate cost'}
        </button>
      )}
    </form>
  )
}
