import React from 'react'

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null, errorInfo: null }
  }

  static getDerivedStateFromError() {
    // Update state so the next render will show the fallback UI
    return { hasError: true }
  }

  componentDidCatch(error, errorInfo) {
    // Log error to console (could also send to error reporting service)
    console.error('Error caught by ErrorBoundary:', error, errorInfo)
    this.setState({
      error,
      errorInfo
    })
  }

  render() {
    if (this.state.hasError) {
      // Fallback UI
      return (
        <div className="min-h-screen bg-neutral-50 flex items-center justify-center px-4">
          <div className="max-w-lg w-full bg-white rounded-2xl shadow-xl border border-neutral-200 p-8">
            <div className="flex flex-col items-center text-center">
              {/* Error Icon */}
              <div className="w-16 h-16 mb-4 rounded-full bg-red-100 flex items-center justify-center">
                <svg
                  className="w-8 h-8 text-red-500"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
              </div>

              {/* Error Message */}
              <h1 className="text-2xl font-bold text-neutral-900 mb-2">
                Something went wrong
              </h1>
              <p className="text-neutral-600 mb-6">
                An unexpected error occurred. Please try refreshing the page.
              </p>

              {/* Action Buttons */}
              <div className="flex gap-3 w-full">
                <button
                  onClick={() => window.location.href = '/'}
                  className="flex-1 px-6 py-3 bg-primary-700 text-white rounded-lg hover:bg-primary-800 transition-colors font-medium"
                >
                  Go to Home
                </button>
                <button
                  onClick={() => window.location.reload()}
                  className="flex-1 px-6 py-3 bg-neutral-100 text-neutral-700 rounded-lg hover:bg-neutral-200 transition-colors font-medium"
                >
                  Reload Page
                </button>
              </div>

              {/* Error Details (only in development) */}
              {process.env.NODE_ENV === 'development' && this.state.error && (
                <details className="mt-6 w-full text-left">
                  <summary className="cursor-pointer text-sm font-medium text-neutral-700 hover:text-neutral-900">
                    Error Details (Development Only)
                  </summary>
                  <div className="mt-3 p-4 bg-neutral-50 rounded-lg border border-neutral-200 text-xs font-mono overflow-auto">
                    <div className="text-red-600 font-semibold mb-2">
                      {this.state.error.toString()}
                    </div>
                    <div className="text-neutral-600 whitespace-pre-wrap">
                      {this.state.errorInfo?.componentStack}
                    </div>
                  </div>
                </details>
              )}
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
