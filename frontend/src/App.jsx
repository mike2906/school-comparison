import { createBrowserRouter, RouterProvider, Outlet } from 'react-router-dom'
import { CompareProvider } from './context/CompareContext'
import { CountryProvider } from './context/CountryContext'
import ErrorBoundary from './components/ErrorBoundary/ErrorBoundary'
import LandingPage from './components/LandingPage/LandingPage'
import SearchPage from './components/SearchPage/SearchPage'
import SchoolDetailPage from './components/SchoolDetailPage/SchoolDetailPage'
import ComparePage from './components/ComparePage/ComparePage'
import CompareBar from './components/CompareBar/CompareBar'

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
        element: <LandingPage />,
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
