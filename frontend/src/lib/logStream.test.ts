import { describe, expect, it } from 'vitest'

import type { LogLine } from '../api/client'
import { initialLogState, type LogAction, logReducer } from './logStream'

function line(seq: number, message = `line ${seq}`): LogAction {
  const logLine: LogLine = {
    seq,
    message,
    operation_id: 1,
    created_at: '2026-01-01T12:00:00Z',
  }
  return { type: 'line', line: logLine }
}

function run(actions: LogAction[]) {
  return actions.reduce(logReducer, initialLogState)
}

describe('logReducer', () => {
  it('appends lines in arrival order', () => {
    const state = run([line(1), line(2), line(3)])

    expect(state.lines.map((entry) => entry.seq)).toEqual([1, 2, 3])
    expect(state.lastSeq).toBe(3)
    expect(state.hasGap).toBe(false)
  })

  it('drops lines replayed after a reconnect', () => {
    const state = run([line(1), line(2), line(1), line(2), line(3)])

    expect(state.lines.map((entry) => entry.seq)).toEqual([1, 2, 3])
    expect(state.hasGap).toBe(false)
  })

  it('flags a gap when a sequence number is skipped', () => {
    const state = run([line(1), line(3)])

    expect(state.lines.map((entry) => entry.seq)).toEqual([1, 3])
    expect(state.hasGap).toBe(true)
  })

  it('tracks the connection state without touching the lines', () => {
    const state = run([{ type: 'opened' }, line(1), { type: 'error' }, { type: 'ended' }])

    expect(state.connection).toBe('ended')
    expect(state.lines).toHaveLength(1)
  })

  it('reset clears everything for the next environment', () => {
    expect(run([line(1), line(2), { type: 'reset' }])).toEqual(initialLogState)
  })
})
