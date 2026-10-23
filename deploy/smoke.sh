#!/usr/bin/env bash
# Post-deploy smoke tests. CD runs them (.github/workflows/deploy.yml); so can you:
#   deploy/smoke.sh api  https://api.schooldecider.com https://schooldecider.com
#   deploy/smoke.sh site https://schooldecider.com [commit sha]
# Needs curl and jq. Exits non-zero on the first failed check.
set -euo pipefail

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

ok() {
	echo "ok: $*"
}

status() {
	curl -sS -o /dev/null -m 30 -w '%{http_code}' "$@"
}

body() {
	# Gzip, as every browser and the edge ask: the Sofia list is ~4 MB plain and ~0.4 MB
	# gzipped, and plain it did not arrive within 30 s from a just-restarted host (#485's deploy).
	curl -fsS --compressed -m 30 "$@"
}

smoke_api() {
	local api="$1" site="$2" ready="" list count id headers fresh
	# Cloudflare keeps GET answers a few minutes: ask past its copy, to check this deploy.
	fresh="smoke=$(date +%s%N)"

	for _ in $(seq 24); do
		ready="$(body "$api/ready" 2>/dev/null | jq -r '.status' 2>/dev/null || true)"
		[ "$ready" = ready ] && break
		sleep 5
	done
	[ "$ready" = ready ] || fail "$api/ready did not report ready"
	ok "/ready"

	list="$(body "$api/schools?$fresh")" || fail "/schools did not answer"
	count="$(jq 'length' <<< "$list")"
	[ "$count" -gt 0 ] || fail "/schools returned no schools"
	ok "/schools lists $count schools"

	id="$(jq '.[0].id' <<< "$list")"
	[ "$(status "$api/schools/$id?$fresh")" = 200 ] || fail "/schools/$id is not 200"
	ok "/schools/$id"

	[ "$(status "$api/countries/bg?$fresh")" = 200 ] || fail "/countries/bg is not 200"
	ok "/countries/bg"

	# Public GET answers allow any origin, so the copy Cloudflare keeps suits every caller
	# (backend/app/main.py); the one write, POST /search-misses, keeps the allowlist.
	headers="$(curl -sS -m 30 -o /dev/null -D - -H "Origin: $site" "$api/countries/bg?$fresh")"
	grep -qi "^access-control-allow-origin: \*" <<< "$headers" || fail "GET answers do not allow any origin"
	preflight() {
		curl -sS -m 30 -o /dev/null -D - -X OPTIONS -H "Origin: $1" \
			-H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type" \
			"$api/search-misses"
	}
	grep -qi "^access-control-allow-origin: $site" <<< "$(preflight "$site")" || fail "CORS does not let $site post"
	if grep -qi "^access-control-allow-origin:" <<< "$(preflight https://example.com)"; then
		fail "CORS lets https://example.com post"
	fi
	ok "CORS: public GETs, posts only from $site"
}

smoke_site() {
	local site="$1" sha="${2:-}" live="" redirect page sitemap urls school robots

	if [ -n "$sha" ]; then
		# A new query string and no-cache on every try, so no cache can answer for the origin.
		for attempt in $(seq 24); do
			live="$(curl -fsS -m 30 -H 'Cache-Control: no-cache' "$site/version.txt?cb=$sha-$attempt" 2>/dev/null || true)"
			[ "$live" = "$sha" ] && break
			sleep 5
		done
		[ "$live" = "$sha" ] || fail "$site/version.txt is '$live', expected $sha"
		ok "version.txt is $sha"
	fi

	redirect="$(curl -sS -o /dev/null -m 30 -w '%{http_code} %{redirect_url}' "$site/")"
	[ "$redirect" = "301 $site/search" ] || fail "/ answered '$redirect', expected a 301 to /search"
	ok "/ redirects to /search"

	page="$(body "$site/search")"
	grep -q '<html lang="bg">' <<< "$page" || fail "/search is not the Bulgarian page"
	page="$(body "$site/en/search")"
	grep -q '<html lang="en">' <<< "$page" || fail "/en/search is not the English page"
	ok "/search and /en/search"

	sitemap="$(body "$site/sitemap.xml")"
	urls="$(grep -c '<loc>' <<< "$sitemap" || true)"
	# About 1,100 (pages in two languages) since kindergartens count by their admission rankings (2026-10).
	[ "$urls" -ge 800 ] || fail "sitemap.xml lists $urls URLs, expected at least 800"
	ok "sitemap.xml lists $urls URLs"

	# -m 1, not `| head`: with pipefail, head closing the pipe early fails the script.
	school="$(grep -m 1 -o "<loc>$site/schools/[0-9]*</loc>" <<< "$sitemap" | sed 's/<[^>]*>//g')"
	[ -n "$school" ] || fail "sitemap.xml lists no school page"
	page="$(body "$school")"
	grep -q 'rel="canonical"' <<< "$page" || fail "$school has no canonical link"
	grep -qF "\"$school\"" <<< "$page" || fail "$school does not link to itself as canonical"
	ok "$school"

	robots="$(body "$site/robots.txt")"
	grep -q 'ai-train=no' <<< "$robots" || fail "robots.txt lost the crawler policy line"
	grep -qF "Sitemap: $site/sitemap.xml" <<< "$robots" || fail "robots.txt does not name the sitemap"
	ok "robots.txt"

	[ "$(status "$site/smoke-test-no-such-page")" = 404 ] || fail "an unknown URL is not a 404"
	ok "unknown URL is a 404"
}

case "${1:-}" in
	api) smoke_api "${2:?API origin}" "${3:?site origin}" ;;
	site)
		# A new deployment can answer 522 on some URLs for a few seconds; a real fault stays.
		for attempt in 1 2 3; do
			# Not `if (...)`: that would switch off `set -e` inside the checks.
			set +e
			(set -e; smoke_site "${2:?site origin}" "${3:-}")
			result=$?
			set -e
			[ "$result" != 0 ] || exit 0
			[ "$attempt" = 3 ] || { echo "retrying in 15 seconds"; sleep 15; }
		done
		exit 1
		;;
	*)
		echo "usage: deploy/smoke.sh api <API origin> <site origin> | site <site origin> [commit sha]" >&2
		exit 1
		;;
esac
