import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Optional custom fallback. */
  fallback?: (error: unknown) => ReactNode;
}

interface State {
  error: unknown | null;
}

/**
 * Catches render errors from a view so a single bad render cannot unmount
 * the whole SPA (a black screen) and show an error instead. Added after the
 * Minified React error #300 incident (hook inside `useMemo` crashed the
 * tree on the second combat click).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error('ErrorBoundary caught a render error:', error, info);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error !== null) {
      if (this.props.fallback) return this.props.fallback(error);
      return (
        <div className="pad" style={{ color: '#e05555' }}>
          <h3>View failed to render</h3>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{String(error)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}
