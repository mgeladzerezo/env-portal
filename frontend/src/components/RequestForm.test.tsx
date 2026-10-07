import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Environment, Estimate, Template } from '../api/client'
import { RequestForm } from './RequestForm'

const templates: Template[] = [
  {
    name: 'vm-database',
    title: 'VM + database',
    description: 'One EC2 instance and a PostgreSQL RDS instance.',
    module_version: 'v1',
    sizes: [
      { name: 'small', description: 't3.micro', hourly_usd: 0.033 },
      { name: 'medium', description: 't3.small', hourly_usd: 0.064 },
    ],
    default_ttl_hours: 8,
    max_ttl_hours: 168,
  },
]

const estimate: Estimate = {
  template: 'vm-database',
  size: 'small',
  ttl_hours: 8,
  hourly_usd: 0.033,
  total_usd: 0.26,
  items: [],
  source: 'static',
}

const fetchMock = vi.fn<typeof fetch>()

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status })
}

function calls(path: string) {
  return fetchMock.mock.calls.filter(([url]) => url === path)
}

function renderForm(onCreated = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RequestForm templates={templates} onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return onCreated
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('RequestForm', () => {
  it('shows the cost estimate before anything is created', async () => {
    fetchMock.mockResolvedValueOnce(json(estimate))
    const user = userEvent.setup()
    renderForm()

    await user.type(screen.getByLabelText('Name'), 'checkout-pr-42')
    await user.click(screen.getByRole('button', { name: 'Estimate cost' }))

    expect(await screen.findByText('Estimated cost')).toBeInTheDocument()
    expect(screen.getByText('$0.0330')).toBeInTheDocument()
    expect(screen.getByText('$0.26')).toBeInTheDocument()
    expect(calls('/api/environments')).toHaveLength(0)
    const [, init] = calls('/api/estimates')[0]!
    expect(JSON.parse(init!.body as string)).toEqual({
      template: 'vm-database',
      size: 'small',
      ttl_hours: 8,
    })
  })

  it('creates the environment only after confirming, with an idempotency key', async () => {
    const created = { id: 'env-1', name: 'checkout-pr-42' } as Environment
    fetchMock.mockResolvedValueOnce(json(estimate)).mockResolvedValueOnce(json(created, 201))
    const user = userEvent.setup()
    const onCreated = renderForm()

    await user.type(screen.getByLabelText('Name'), 'checkout-pr-42')
    await user.click(screen.getByRole('button', { name: 'Estimate cost' }))
    await user.click(await screen.findByRole('button', { name: 'Confirm and create' }))

    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith(created))
    const [, init] = calls('/api/environments')[0]!
    expect(JSON.parse(init!.body as string)).toEqual({
      name: 'checkout-pr-42',
      template: 'vm-database',
      size: 'small',
      ttl_hours: 8,
    })
    expect((init!.headers as Record<string, string>)['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('discards the estimate when the request changes', async () => {
    fetchMock.mockResolvedValueOnce(json(estimate))
    const user = userEvent.setup()
    renderForm()

    await user.type(screen.getByLabelText('Name'), 'checkout-pr-42')
    await user.click(screen.getByRole('button', { name: 'Estimate cost' }))
    await screen.findByText('Estimated cost')
    await user.click(screen.getByRole('radio', { name: /medium/ }))

    expect(screen.queryByText('Estimated cost')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm and create' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Estimate cost' })).toBeInTheDocument()
  })

  it('shows the API error when the estimate is rejected', async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: 'ttl_hours must be between 1 and 168' }, 422))
    const user = userEvent.setup()
    renderForm()

    await user.type(screen.getByLabelText('Name'), 'checkout-pr-42')
    await user.click(screen.getByRole('button', { name: 'Estimate cost' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'ttl_hours must be between 1 and 168',
    )
  })
})
