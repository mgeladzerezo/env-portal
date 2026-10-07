import { describe, expect, it } from 'vitest'

import { formatOutput, formatTimeLeft, formatUsd, HOURLY_DIGITS } from './format'

describe('formatUsd', () => {
  it('takes the number of decimals for hourly prices', () => {
    expect(formatUsd(0.0104, HOURLY_DIGITS)).toBe('$0.0104')
  })

  it('uses two decimals for totals', () => {
    expect(formatUsd(12.5)).toBe('$12.50')
    expect(formatUsd(0)).toBe('$0.00')
  })

  it('shows n/a when there is no estimate', () => {
    expect(formatUsd(null)).toBe('n/a')
  })
})

describe('formatTimeLeft', () => {
  const now = new Date('2026-01-01T12:00:00Z')

  it('formats minutes, hours and days', () => {
    expect(formatTimeLeft('2026-01-01T12:45:00Z', now)).toBe('45m')
    expect(formatTimeLeft('2026-01-01T15:30:00Z', now)).toBe('3h 30m')
    expect(formatTimeLeft('2026-01-04T14:00:00Z', now)).toBe('3d 2h')
  })

  it('reports expired once the deadline has passed', () => {
    expect(formatTimeLeft('2026-01-01T12:00:00Z', now)).toBe('expired')
    expect(formatTimeLeft('2026-01-01T11:00:00Z', now)).toBe('expired')
  })
})

describe('formatOutput', () => {
  it('prints strings as they are and everything else as JSON', () => {
    expect(formatOutput('10.0.1.5')).toBe('10.0.1.5')
    expect(formatOutput(['a', 'b'])).toBe('["a","b"]')
  })
})
