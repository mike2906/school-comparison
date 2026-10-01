import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
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
import {
  DEFAULT_SITE_ORIGIN,
  alternateLinks,
  canonicalUrl,
  hasCanonical,
  languageBasename,
  languageFromPath,
} from './utils/languageUrl'

// The language is fixed for the lifetime of the document: switching it is a full
// navigation to the other prefix (see LanguageToggle), which is why links and
// navigate() calls can stay unprefixed under the basename.
const LANGUAGE = languageFromPath(window.location.pathname)

// Canonical and hreflang need absolute URLs.
const SITE_ORIGIN = import.meta.env.VITE_SITE_ORIGIN || DEFAULT_SITE_ORIGIN

// The prerendered HTML (scripts/prerender.js) is the head of the page that was loaded and
// is left as it is, including a 404 page's lack of a canonical. After a client-side
// navigation its description, robots and Open Graph tags describe another page, so they
// are dropped and the canonical, hreflang and title follow the new route.
// `pathname` here is the route path: the router has already stripped the basename.
function useLanguageHead() {
  const { pathname } = useLocation()
  const { t } = useTranslation()
  // `/` is served with the results page's head and redirects there (HomeRedirect).
  const loadedPath = useRef(pathname === '/' ? '/search' : pathname)
  const navigated = useRef(false)

  useEffect(() => {
    document.documentElement.lang = LANGUAGE
    if (pathname === '/') return
    if (!navigated.current && pathname === loadedPath.current) return
    navigated.current = true

    document.head
      .querySelectorAll(
        'link[rel="canonical"], link[rel="alternate"][hreflang], meta[name="description"], meta[name="robots"], meta[property^="og:"]'
      )
      .forEach((element) => element.remove())
    // These two pages set no title of their own; the others do (school, about, not found).
    if (pathname === '/search') document.title = t('seo.searchTitle')
    if (pathname === '/compare') document.title = `${t('compare.title')} · ${t('nav.title')}`
    if (!hasCanonical(pathname)) return
    const canonical = document.createElement('link')
    canonical.rel = 'canonical'
    canonical.href = canonicalUrl(pathname, LANGUAGE, SITE_ORIGIN)
    document.head.appendChild(canonical)
    for (const { hreflang, href } of alternateLinks(pathname, SITE_ORIGIN)) {
      const link = document.createElement('link')
      link.rel = 'alternate'
      link.hreflang = hreflang
      link.href = href
      document.head.appendChild(link)
    }
  }, [pathname, t])
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
