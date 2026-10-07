import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Environment } from '../api/client'
import { EnvironmentList } from './EnvironmentList'

function environment(overrides: Partial<Environment>): Environment {
  return {
    id: 'env-1',
    name: 'checkout',
    owner: 'alice@example.com',
    template: 'static-site',
    module_version: 'v1',
    size: 'small',
    ttl_hours: 8,
    status: 'ready',
    hourly_cost_usd: 0.005,
    outputs: {},
    policy_violations: [],
    error: null,
    lock_info: null,
    created_at: '2026-01-01T12:00:00Z',
    updated_at: '2026-01-01T12:00:00Z',
    expires_at: '2099-01-01T12:00:00Z',
    destroyed_at: null,
    active_operation: null,
    last_operation: null,
    ...overrides,
  }
}

describe('EnvironmentList', () => {
  it('shows a hint when there is nothing to list', () => {
    render(<EnvironmentList environments={[]} selectedId={null} onSelect={vi.fn()} />)

    expect(screen.getByText(/No environments yet/)).toBeInTheDocument()
  })

  it('renders one row per environment with its status', () => {
    const environments = [
      environment({ id: 'a', name: 'checkout', status: 'applying' }),
      environment({ id: 'b', name: 'search', status: 'blocked_by_policy' }),
      environment({ id: 'c', name: 'old', status: 'destroyed' }),
    ]
    render(<EnvironmentList environments={environments} selectedId="b" onSelect={vi.fn()} />)

    const rows = screen.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText('Applying')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Blocked by policy')).toBeInTheDocument()
    expect(rows[1]).toHaveAttribute('aria-selected', 'true')
    expect(within(rows[2]!).getByText('Destroyed')).toBeInTheDocument()
  })

  it('reports the clicked environment', async () => {
    const onSelect = vi.fn()
    render(
      <EnvironmentList
        environments={[environment({ id: 'a' })]}
        selectedId={null}
        onSelect={onSelect}
      />,
    )

    await userEvent.click(screen.getByText('checkout'))

    expect(onSelect).toHaveBeenCalledWith('a')
  })
})
