import { useEffect, useState, useCallback } from 'react'

export type ToastLevel = 'ok' | 'warn' | 'error' | 'info'

export interface ToastMessage {
  id: number
  level: ToastLevel
  text: string
}

let _next = 0
let _dispatch: ((msg: ToastMessage) => void) | null = null

export function toast(text: string, level: ToastLevel = 'info') {
  _dispatch?.({ id: _next++, level, text })
}

export default function ToastContainer() {
  const [messages, setMessages] = useState<ToastMessage[]>([])

  const dismiss = useCallback((id: number) => {
    setMessages(prev => prev.filter(m => m.id !== id))
  }, [])

  useEffect(() => {
    _dispatch = (msg) => setMessages(prev => [...prev.slice(-4), msg])
    return () => { _dispatch = null }
  }, [])

  useEffect(() => {
    if (messages.length === 0) return
    const last = messages[messages.length - 1]
    const t = setTimeout(() => dismiss(last.id), 4000)
    return () => clearTimeout(t)
  }, [messages, dismiss])

  if (messages.length === 0) return null

  return (
    <div className="toast-container" aria-live="polite" aria-atomic="false">
      {messages.map(m => (
        <div key={m.id} className={'toast toast--' + m.level} role="status">
          <span>{m.text}</span>
          <button className="toast__close" onClick={() => dismiss(m.id)} aria-label="Dismiss">×</button>
        </div>
      ))}
    </div>
  )
}
