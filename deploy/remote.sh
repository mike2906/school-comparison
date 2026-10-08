#!/usr/bin/env bash
# CD's call to the API host (.github/workflows/deploy.yml). The key can only run
# deploy/deploy.sh there, and the host key is pinned in deploy/ssh_host_key.pub.
#   deploy/remote.sh deploy     needs GHCR_TOKEN (read access to the image)
#   deploy/remote.sh rollback
#   deploy/remote.sh publish    needs SNAPSHOT_FILE (a pg_dump -Fc of the launch DB)
# Environment: DEPLOY_SSH_KEY, DEPLOY_HOST; GITHUB_SHA for deploy and rollback;
# DEPLOY_USER (default ubuntu).
set -euo pipefail

: "${DEPLOY_SSH_KEY:?}" "${DEPLOY_HOST:?}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

key="$(mktemp)"
known_hosts="$(mktemp)"
trap 'rm -f "$key" "$known_hosts"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$key"
printf '%s %s\n' "$DEPLOY_HOST" "$(cat deploy/ssh_host_key.pub)" > "$known_hosts"

remote() {
	ssh -i "$key" -o IdentitiesOnly=yes -o BatchMode=yes \
		-o UserKnownHostsFile="$known_hosts" -o StrictHostKeyChecking=yes \
		-o ConnectTimeout=15 -o ServerAliveInterval=30 \
		"${DEPLOY_USER:-ubuntu}@$DEPLOY_HOST" "$@"
}

case "${1:-}" in
	deploy)
		# Must match the hash deploy/deploy.sh computes on the host.
		config_hash="$(cat docker-compose.prod.yml deploy/Caddyfile deploy/cloudflare-proxies.caddy deploy/deploy.sh | sha256sum | cut -d' ' -f1)"
		printf '%s\n' "${GHCR_TOKEN:?}" | remote "deploy ${GITHUB_SHA:?} $config_hash"
		;;
	rollback)
		remote "rollback ${GITHUB_SHA:?}" </dev/null
		;;
	publish)
		# The host checks what it received against this before touching the database.
		remote "publish $(sha256sum "${SNAPSHOT_FILE:?}" | cut -d' ' -f1)" < "$SNAPSHOT_FILE"
		;;
	*)
		echo "usage: deploy/remote.sh deploy|rollback|publish" >&2
		exit 1
		;;
esac
