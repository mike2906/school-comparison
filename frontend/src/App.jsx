import { useEffect } from 'react'
import { createBrowserRouter, RouterProvider, Outlet, Navigate, useLocation } from 'react-router-dom'
import { CompareProvider } from './context/CompareContext'
import { CountryProvider } from './context/CountryContext'
import ErrorBoundary from './components/ErrorBoundary/ErrorBoundary'
import SearchPage from './components/SearchPage/SearchPage'
import SchoolDetailPage from './components/SchoolDetailPage/SchoolDetailPage'
import ComparePage from './components/ComparePage/ComparePage'
import AboutPage from './components/AboutPage/AboutPage'
import NotFoundPage from './components/NotFoundPage/NotFoundPage'
import CompareBar from './components/CompareBar/CompareBar'
import { alternateLinks, languageBasename, languageFromPath } from './utils/languageUrl'

// The language is fixed for the lifetime of the document: switching it is a full
// navigation to the other prefix (see LanguageToggle), which is why links and
// navigate() calls can stay unprefixed under the basename.
const LANGUAGE = languageFromPath(window.location.pathname)

// hreflang needs absolute URLs. schooldecider.bg is the planned primary domain.
const SITE_ORIGIN = import.meta.env.VITE_SITE_ORIGIN || 'https://schooldecider.bg'

// `pathname` here is the route path: the router has already stripped the basename.
function useLanguageHead() {
  const { pathname } = useLocation()

  useEffect(() => {
    document.documentElement.lang = LANGUAGE
    document.head.querySelectorAll('link[rel="alternate"][hreflang]').forEach((link) => link.remove())
    for (const { hreflang, href } of alternateLinks(pathname, SITE_ORIGIN)) {
      const link = document.createElement('link')
      link.rel = 'alternate'
      link.hreflang = hreflang
      link.href = href
      document.head.appendChild(link)
    }
  }, [pathname])
}

// The results page is the home page; old `/?age_group=...` links keep their filters.
function HomeRedirect() {
  const { search } = useLocation()
  return <Navigate to={`/search${search}`} replace />
}

// Root layout with CompareBar
function RootLayout() {
  useLanguageHead()

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
    errorElement: <NotFoundPage variant="error" />,
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
      {
        path: '*',
        element: <NotFoundPage />,
      },
    ],
  },
], { basename: languageBasename(LANGUAGE) })

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
