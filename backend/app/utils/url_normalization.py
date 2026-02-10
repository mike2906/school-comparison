from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
    "_ga",
    "_gl",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: str) -> str:
    """
    Minimal URL normalization for stable source keys.

    - Lowercase scheme + host
    - Drop fragment
    - Remove common tracking params
    - Preserve path case and query parameter order
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in TRACKING_PARAMS:
            continue
        query_items.append((key, value))

    query = urlencode(query_items, doseq=True)
    return urlunparse((scheme, netloc, parsed.path, "", query, ""))
