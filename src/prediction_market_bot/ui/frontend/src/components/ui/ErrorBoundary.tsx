import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode; fallback?: ReactNode }
interface State { hasError: boolean; message: string }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(err: Error): State {
    return { hasError: true, message: err.message }
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="error-boundary">
          <span className="text-error" style={{ fontWeight: 500 }}>Component error</span>
          <span className="muted mono" style={{ fontSize: '10px', marginTop: '4px' }}>{this.state.message}</span>
          <button className="btn btn--ghost btn--sm" style={{ marginTop: '8px' }}
            onClick={() => this.setState({ hasError: false, message: '' })}>
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
