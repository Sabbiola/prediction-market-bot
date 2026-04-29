import { useAuth } from '@/hooks/useAuth'
import LoginPage from '@/pages/LoginPage'
import AppShell from '@/components/layout/AppShell'

export default function App() {
  const { data: session, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="app-loading">
        <span className="mono muted">Loading…</span>
      </div>
    )
  }

  if (!session?.authenticated) {
    return <LoginPage />
  }

  return (
    <AppShell
      username={session.username}
      role={session.role}
      authEnabled={true}
    />
  )
}
