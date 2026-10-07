import { useEffect, useRef } from 'react'

import type { LogState } from '../lib/logStream'

const CONNECTION_LABELS: Record<LogState['connection'], string> = {
  connecting: 'connecting',
  open: 'live',
  ended: 'complete',
  error: 'reconnecting',
}

export function LogViewer({ log }: { log: LogState }) {
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'nearest' })
  }, [log.lastSeq])

  return (
    <section>
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <h3 className="font-medium">Provisioning log</h3>
        <span className="text-xs text-slate-500">
          {log.lines.length} lines, {CONNECTION_LABELS[log.connection]}
        </span>
      </div>
      {log.hasGap && (
        <p role="alert" className="mb-1 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
          Some lines are missing from this view. Reload the page to fetch the full log.
        </p>
      )}
      <div
        role="log"
        className="h-80 overflow-auto whitespace-pre rounded bg-slate-900 p-3 font-mono text-xs leading-5 text-slate-100"
      >
        {log.lines.length === 0 && <span className="text-slate-500">No output yet.</span>}
        {log.lines.map((line) => (
          <div key={line.seq} className={line.message.startsWith('[') ? 'text-sky-300' : ''}>
            {line.message || ' '}
          </div>
        ))}
        <div ref={bottom} />
      </div>
    </section>
  )
}
