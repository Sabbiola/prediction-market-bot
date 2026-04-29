import { useState, type FormEvent } from 'react'
import { useLogin } from '@/hooks/useAuth'

export default function LoginPage() {
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      const res = await login.mutateAsync({ username, password })
      if (!res.authenticated) setError(res.message || 'Invalid credentials')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-logo" aria-hidden="true">
          <svg viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="3"  cy="9"  r="2" fill="white" opacity=".9"/>
            <circle cx="9"  cy="3"  r="2" fill="white" opacity=".9"/>
            <circle cx="9"  cy="15" r="2" fill="white" opacity=".9"/>
            <circle cx="15" cy="9"  r="2" fill="white" opacity=".9"/>
            <circle cx="9"  cy="9"  r="1.5" fill="white"/>
            <line x1="5"  y1="9"  x2="7.5" y2="9"   stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="10.5" y1="9" x2="13" y2="9"   stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="9"  y1="5"  x2="9"   y2="7.5" stroke="white" strokeWidth=".9" opacity=".6"/>
            <line x1="9"  y1="10.5" x2="9" y2="13"  stroke="white" strokeWidth=".9" opacity=".6"/>
          </svg>
        </div>
        <h1 className="login-title">PMBot</h1>
        <p className="login-subtitle">Control Plane</p>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="login-field">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={e => setUsername(e.target.value)}
              required
            />
          </div>
          <div className="login-field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
            />
          </div>
          {error && <p className="login-error" role="alert">{error}</p>}
          <button type="submit" className="login-btn" disabled={login.isPending}>
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
