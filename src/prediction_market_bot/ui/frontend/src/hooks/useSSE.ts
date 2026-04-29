import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { IncidentEvent } from '@/types/api'

export type SseStatus = 'connecting' | 'live' | 'error' | 'closed'

export function useSSE() {
  const qc = useQueryClient()
  const [status, setStatus] = useState<SseStatus>('connecting')
  const [latest, setLatest] = useState<IncidentEvent | null>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const es = new EventSource('/api/incidents/stream', { withCredentials: true })
    esRef.current = es

    es.addEventListener('open', () => setStatus('live'))

    es.addEventListener('incidents', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as IncidentEvent
        setLatest(data)
        // Invalidate overview so the feed refreshes
        qc.invalidateQueries({ queryKey: ['tab', 'overview'] })
      } catch (_) { /* ignore malformed */ }
    })

    es.addEventListener('heartbeat', () => {
      setStatus('live')
    })

    es.addEventListener('error', () => {
      setStatus('error')
      // EventSource auto-reconnects; don't close
    })

    return () => {
      es.close()
      esRef.current = null
      setStatus('closed')
    }
  }, [qc])

  return { status, latest }
}
