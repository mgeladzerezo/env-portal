import { useEffect, useReducer, useRef } from 'react'

import { api, type LogLine } from '../api/client'

export interface LogState {
  lines: LogLine[]
  lastSeq: number
  // True when a line arrived whose seq was not lastSeq + 1, i.e. something was skipped.
  hasGap: boolean
  connection: 'connecting' | 'open' | 'ended' | 'error'
}

export type LogAction =
  | { type: 'reset' }
  | { type: 'opened' }
  | { type: 'line'; line: LogLine }
  | { type: 'ended' }
  | { type: 'error' }

export const initialLogState: LogState = {
  lines: [],
  lastSeq: 0,
  hasGap: false,
  connection: 'connecting',
}

export function logReducer(state: LogState, action: LogAction): LogState {
  switch (action.type) {
    case 'reset':
      return initialLogState
    case 'opened':
      return { ...state, connection: 'open' }
    case 'ended':
      return { ...state, connection: 'ended' }
    case 'error':
      return { ...state, connection: 'error' }
    case 'line': {
      const { line } = action
      // A reconnect can replay lines we already have; seq makes them easy to drop.
      if (line.seq <= state.lastSeq) return state
      return {
        ...state,
        lines: [...state.lines, line],
        lastSeq: line.seq,
        hasGap: state.hasGap || line.seq !== state.lastSeq + 1,
      }
    }
  }
}

/**
 * Follows an environment's log stream. `streamKey` changes whenever a new operation
 * starts, which reopens a stream that the server already ended.
 */
export function useLogStream(
  environmentId: string,
  streamKey: string,
  onStatus: () => void,
): LogState {
  const [state, dispatch] = useReducer(logReducer, initialLogState)
  const cursor = useRef({ environmentId, lastSeq: 0 })

  useEffect(() => {
    if (cursor.current.environmentId !== environmentId) {
      cursor.current = { environmentId, lastSeq: 0 }
      dispatch({ type: 'reset' })
    }
    // Reopening for a new operation continues after the last line already shown.
    const source = new EventSource(api.logStreamUrl(environmentId, cursor.current.lastSeq))
    source.onopen = () => dispatch({ type: 'opened' })
    source.addEventListener('log', (event) => {
      const line = JSON.parse((event as MessageEvent<string>).data) as LogLine
      cursor.current.lastSeq = Math.max(cursor.current.lastSeq, line.seq)
      dispatch({ type: 'line', line })
    })
    source.addEventListener('status', onStatus)
    source.addEventListener('end', () => {
      // Without close() the browser would reconnect and replay the stream forever.
      source.close()
      dispatch({ type: 'ended' })
    })
    // On a dropped connection EventSource reconnects by itself and sends Last-Event-ID,
    // so the server resumes after the last line we received.
    source.onerror = () => dispatch({ type: 'error' })
    return () => source.close()
  }, [environmentId, streamKey, onStatus])

  return state
}
