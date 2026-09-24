import { createBrowserRouter, RouterProvider, Outlet, Navigate, useLocation } from 'react-router-dom'
import { CompareProvider } from './context/CompareContext'
import { CountryProvider } from './context/CountryContext'
import ErrorBoundary from './components/ErrorBoundary/ErrorBoundary'
import SearchPage from './components/SearchPage/SearchPage'
import SchoolDetailPage from './components/SchoolDetailPage/SchoolDetailPage'
import ComparePage from './components/ComparePage/ComparePage'
import AboutPage from './components/AboutPage/AboutPage'
import CompareBar from './components/CompareBar/CompareBar'

// The results page is the home page; old `/?age_group=...` links keep their filters.
function HomeRedirect() {
  const { search } = useLocation()
  return <Navigate to={`/search${search}`} replace />
}

// Root layout with CompareBar
function RootLayout() {
  return (
    <>
      <Outlet />
      <CompareBar />
    </>
  )
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      {
        index: true,
        element: <HomeRedirect />,
      },
      {
        path: 'search',
        element: <SearchPage />,
      },
      {
        path: 'schools/:id',
        element: <SchoolDetailPage />,
      },
      {
        path: 'compare',
        element: <ComparePage />,
      },
      {
        path: 'about',
        element: <AboutPage />,
      },
    ],
  },
])

function App() {
  return (
    <ErrorBoundary>
      <CountryProvider>
        <CompareProvider>
          <RouterProvider router={router} />
        </CompareProvider>
      </CountryProvider>
    </ErrorBoundary>
  )
}

export default App
